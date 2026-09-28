import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from cloud_warehouse.legacy_cases import validate_bundle
from tour.api.plans import Plan, PlanDay, PlanVersion, diff_days
from tour.api.store import SqliteSessionStore
from tour.api.warehouse_legacy import export_case


def fixture(tmp_path):
    path = tmp_path / "sessions.sqlite"
    store = SqliteSessionStore(path)
    session = store.start("ACME-old-advisor")
    session.messages.extend(
        [
            {"role": "user", "content": "ACME 客户定制方案"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "private-tool",
                        "name": "create_order",
                        "input": {"secret": "never-replay"},
                    }
                ],
            },
            {"role": "assistant", "content": "历史参考行程"},
        ]
    )
    store.save(session)
    plan = Plan(
        plan_id="PL-Abcd1234",
        session_id=session.session_id,
        user_id=session.user_id,
        route_id=36,
        route_name="ACME 线路",
        departure_id=36,
    )
    store.create_plan(plan)
    versions = []
    for n in range(1, 4):
        version = PlanVersion(
            plan_id=plan.plan_id,
            version=n,
            parent_version=None if n == 1 else 1,
            title=f"ACME v{n}",
            days=[PlanDay(label="第1天", note=f"ACME 活动 {n}")],
            share_token=f"private-share-{n}",
        )
        store.add_version(version, diff_days(None if n == 1 else versions[0].days, version.days))
        versions.append(version)
    mapping = dict(
        session_id=session.session_id,
        legacy_user_id=session.user_id,
        source_namespace=uuid4(),
        organization_id=uuid4(),
        user_id=uuid4(),
        connection_id=uuid4(),
    )
    return path, store, mapping


def test_export_consistent_readonly_projection_preserves_branch_and_share_versions(tmp_path):
    path, store, mapping = fixture(tmp_path)
    before = path.read_bytes()
    exported = export_case(path, **mapping)
    assert path.read_bytes() == before
    assert export_case(path, **mapping) == exported
    assert [m.text for m in exported.messages] == ["ACME 客户定制方案", "历史参考行程"]
    assert exported.plans[0].versions[2].parent_version == 1
    assert (
        exported.plans[0].versions[2].share_token_hash
        == hashlib.sha256(b"private-share-3").hexdigest()
    )
    assert "private-share-" not in exported.model_dump_json()
    assert "never-replay" not in exported.model_dump_json()
    assert "state_json" not in exported.model_dump_json()
    assert store.latest_version("PL-Abcd1234") == 3
    assert validate_bundle(exported)


@pytest.mark.parametrize(
    "fault",
    ["owner", "state_owner", "plan_owner", "version_column", "version_gap", "duplicate_share"],
)
def test_inconsistent_legacy_identity_and_versions_fail_instead_of_partial_export(tmp_path, fault):
    path, _, mapping = fixture(tmp_path)
    with sqlite3.connect(path) as conn:
        if fault == "owner":
            mapping["legacy_user_id"] = "other"
        elif fault == "state_owner":
            raw = json.loads(conn.execute("SELECT state_json FROM sessions").fetchone()[0])
            raw["user_id"] = "other"
            conn.execute("UPDATE sessions SET state_json=?", (json.dumps(raw),))
        elif fault == "plan_owner":
            conn.execute("UPDATE plans SET user_id='other'")
        elif fault == "version_column":
            conn.execute("UPDATE plan_versions SET parent_version=2 WHERE version=3")
        elif fault == "version_gap":
            conn.execute("DELETE FROM plan_versions WHERE version=2")
        elif fault == "duplicate_share":
            row = json.loads(
                conn.execute("SELECT payload_json FROM plan_versions WHERE version=3").fetchone()[0]
            )
            row["share_token"] = "private-share-1"
            conn.execute(
                "UPDATE plan_versions SET payload_json=? WHERE version=3", (json.dumps(row),)
            )
    with pytest.raises(ValueError):
        export_case(path, **mapping)


def test_missing_source_is_not_created_and_symlink_rejected(tmp_path):
    path, _, mapping = fixture(tmp_path)
    missing = tmp_path / "absent.sqlite"
    with pytest.raises(ValueError):
        export_case(missing, **mapping)
    assert not missing.exists()
    link = tmp_path / "link.sqlite"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        export_case(link, **mapping)


def test_oversize_source_is_rejected_before_parsing(tmp_path):
    path, _, mapping = fixture(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE sessions SET state_json=?", ("not-json" * 300_000,))
    with pytest.raises(ValueError, match="大小上限"):
        export_case(path, **mapping)


def test_equivalent_timestamp_offsets_do_not_corrupt_version_identity(tmp_path):
    path, _, mapping = fixture(tmp_path)
    with sqlite3.connect(path) as conn:
        payload = json.loads(
            conn.execute("SELECT payload_json FROM plan_versions WHERE version=3").fetchone()[0]
        )
        payload["created_at"] = (
            datetime.fromisoformat(payload["created_at"])
            .astimezone(timezone(timedelta(hours=8)))
            .isoformat()
        )
        conn.execute(
            "UPDATE plan_versions SET payload_json=? WHERE version=3", (json.dumps(payload),)
        )
    assert len(export_case(path, **mapping).plans[0].versions) == 3
