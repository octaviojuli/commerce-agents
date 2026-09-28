"""Read-only SQLite snapshot export for explicitly mapped historical advisor cases."""

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from cloud_warehouse.integrations import canonical, fingerprint
from cloud_warehouse.legacy_cases import MAX_BYTES, Bundle, validate_bundle

from .plans import DayDiff, Plan, PlanVersion
from .store import display_messages


def export_case(
    path, *, session_id, legacy_user_id, source_namespace, organization_id, user_id, connection_id
):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("来源必须为现有 SQLite 普通文件")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("BEGIN")
        size = conn.execute(
            """SELECT
              COALESCE((SELECT length(CAST(state_json AS BLOB)) FROM sessions WHERE session_id=:session),0)
              + COALESCE((SELECT sum(length(CAST(message_json AS BLOB))) FROM messages WHERE session_id=:session),0)
              + COALESCE((SELECT sum(length(CAST(v.payload_json AS BLOB))+length(CAST(v.diff_json AS BLOB)))
                FROM plan_versions v JOIN plans p USING(plan_id) WHERE p.session_id=:session),0)""",
            {"session": session_id},
        ).fetchone()[0]
        if size > MAX_BYTES:
            raise ValueError("来源案例超过迁移大小上限，禁止截断导入")
        session = conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if session is None or session["user_id"] != legacy_user_id:
            raise ValueError("旧会话与指定顾问不匹配")
        session = dict(session)
        if len(session["state_json"].encode()) > MAX_BYTES:
            raise ValueError("来源会话状态超过上限")
        state = json.loads(session["state_json"])
        if (
            state.get("user_id") != legacy_user_id
            or state.get("session_id", session_id) != session_id
        ):
            raise ValueError("旧会话状态与记录归属不一致")
        messages = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY seq LIMIT 5001", (session_id,)
            )
        ]
        plans = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM plans WHERE session_id=? ORDER BY created_at,plan_id LIMIT 201",
                (session_id,),
            )
        ]
        if len(messages) > 5000 or len(plans) > 200:
            raise ValueError("案例超过迁移数量上限，禁止截断导入")
        raw = {"session": session, "messages": messages, "plans": plans, "versions": []}
        projected = []
        for row in plans:
            plan = Plan.model_validate(row)
            if plan.user_id != legacy_user_id or plan.session_id != session_id:
                raise ValueError("旧方案与会话顾问不一致")
            versions = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM plan_versions WHERE plan_id=? ORDER BY version LIMIT 1001",
                    (plan.plan_id,),
                )
            ]
            if len(versions) > 1000:
                raise ValueError("方案版本超过迁移数量上限")
            raw["versions"].extend(versions)
            saved = []
            for record in versions:
                version = PlanVersion.model_validate_json(record["payload_json"])
                if (
                    version.plan_id,
                    version.version,
                    version.parent_version,
                    version.share_token,
                    version.created_at,
                ) != (
                    plan.plan_id,
                    record["version"],
                    record["parent_version"],
                    record["share_token"],
                    datetime.fromisoformat(record["created_at"]),
                ):
                    raise ValueError("旧方案版本列与内容不一致")
                diff = TypeAdapter(list[DayDiff]).validate_json(record["diff_json"])
                saved.append(
                    {
                        **version.model_dump(mode="json", exclude={"plan_id", "share_token"}),
                        "share_token_hash": hashlib.sha256(
                            version.share_token.encode()
                        ).hexdigest(),
                        "diff": [d.model_dump(mode="json") for d in diff],
                    }
                )
            projected.append(
                {
                    **plan.model_dump(mode="json", exclude={"session_id", "user_id"}),
                    "versions": saved,
                }
            )
        # Hash the consistent logical snapshot, including private state, without exporting it.
        if len(canonical(raw).encode()) > MAX_BYTES:
            raise ValueError("来源案例超过迁移大小上限，禁止截断导入")
        bundle = Bundle(
            source_namespace=source_namespace,
            legacy_session_id=session_id,
            legacy_user_id=legacy_user_id,
            organization_id=organization_id,
            user_id=user_id,
            connection_id=connection_id,
            source_hash=fingerprint(raw),
            created_at=session["created_at"],
            updated_at=session["updated_at"],
            messages=display_messages([json.loads(r["message_json"]) for r in messages]),
            plans=projected,
        )
        validate_bundle(bundle)
        return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--session", required=True)
    parser.add_argument("--legacy-user", required=True)
    for name in ("source-namespace", "organization", "user", "connection"):
        parser.add_argument("--" + name, type=UUID, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        bundle = export_case(
            args.source,
            session_id=args.session,
            legacy_user_id=args.legacy_user,
            source_namespace=args.source_namespace,
            organization_id=args.organization,
            user_id=args.user,
            connection_id=args.connection,
        )
        if args.destination.parent.stat().st_mode & 0o077:
            raise ValueError("输出目录须为私有目录")
        with args.destination.open("x", encoding="utf8") as target:
            target.write(bundle.model_dump_json(indent=2))
            target.flush()
            os.fsync(target.fileno())
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
        parser.exit(1, "历史案例导出失败；请核对私有来源、数据完整性及明确归属。\n")
    print(
        json.dumps({"exported": True, "messages": len(bundle.messages), "plans": len(bundle.plans)})
    )


if __name__ == "__main__":
    main()
