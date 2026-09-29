"""Scoped synchronization history and audited human schedule controls."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .changes import Conflict
from .persistence import Forbidden, require_role, transaction


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)
    action: Literal["configure", "run_now", "retry"]
    interval_seconds: int | None = Field(default=None, ge=60, le=86400, strict=True)
    max_attempts: int | None = Field(default=None, ge=1, le=10, strict=True)
    enabled: bool | None = Field(default=None, strict=True)
    note: str = Field(min_length=1, max_length=500)


ERRORS = {
    "SYNC_CONFIG_STALE": "任务配置已变化，请刷新后重新确认",
    "SYNC_NOTE_REQUIRED": "请填写操作说明",
    "SYNC_CONFIG_INVALID": "请填写完整有效的调度配置",
    "SYNC_SOURCE_INACTIVE": "来源已停用或不支持目录同步",
    "SYNC_JOB_RUNNING": "任务正在执行，请在完成后修改周期或重试",
    "SYNC_JOB_DISABLED": "请先启用任务",
    "SYNC_JOB_NOT_FAILED": "只可重试已停止的失败任务",
    "SYNC_JOB_NOT_WAITING": "只可立即排队等待中的任务",
    "SYNC_ACTION_INVALID": "不支持的同步操作",
    "SYNC_SOURCE_COOLDOWN": "上游要求等待，冷却结束后才可执行同步",
}


def control(engine, actor, job_id: UUID, command: Control):
    try:
        with transaction(engine, actor) as conn:
            require_role(conn, "supplier_admin")
            row = conn.scalar(
                text("""SELECT warehouse_sync_control(:job,:version,:action,
                    :interval,:attempts,:enabled,:note)"""),
                {
                    "job": job_id,
                    "version": command.version,
                    "action": command.action,
                    "interval": command.interval_seconds,
                    "attempts": command.max_attempts,
                    "enabled": command.enabled,
                    "note": command.note,
                },
            )
            # Lease tokens and worker identity are never part of the human response.
            return {key: row[key] for key in ("id", "config_version", "status", "enabled")}
    except DBAPIError as error:
        code = getattr(getattr(error.orig, "diag", None), "message_primary", "")
        if code in ERRORS:
            raise Conflict(ERRORS[code]) from None
        if code == "SYNC_CONTROL_FORBIDDEN":
            raise Forbidden("同步任务不存在或无权管理") from None
        raise


def _page(rows, limit):
    return {
        "items": rows[:limit],
        "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    }


def _limit(limit):
    if not 1 <= limit <= 100:
        raise Conflict("分页大小须为 1 至 100")


def jobs(engine, actor, *, after=None, limit=100):
    _limit(limit)
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "sync_worker", "auditor")
        if after and not conn.scalar(text("SELECT id FROM sync_job WHERE id=:id"), {"id": after}):
            raise Forbidden("分页位置不存在或无权限")
        rows = conn.execute(
            text("""SELECT j.id,j.connection_id,c.name AS source_name,c.active AS source_active,
                j.status,j.interval_seconds,j.attempts,j.max_attempts,j.next_run_at,j.lease_until,
                j.last_error,j.last_run_id,j.completed_count,j.updated_at,j.enabled,
                j.config_version,j.worker_last_seen_at,j.provider_not_before FROM sync_job j
                JOIN supplier_connection c ON c.id=j.connection_id
                WHERE CAST(:after AS uuid) IS NULL OR j.id>:after ORDER BY j.id LIMIT :limit"""),
            {"after": after, "limit": limit + 1},
        ).mappings()
        return _page([dict(row) for row in rows], limit)


def runs(engine, actor, *, connection_id=None, before=None, limit=50):
    _limit(limit)
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "sync_worker", "auditor")
        if connection_id and not conn.scalar(
            text(
                "SELECT id FROM supplier_connection WHERE id=:id AND supplier_org_id=warehouse_org_id()"
            ),
            {"id": connection_id},
        ):
            raise Forbidden("来源不存在或无权限")
        anchor = None
        if before:
            anchor = (
                conn.execute(
                    text("""SELECT started_at,id FROM sync_run WHERE id=:id
                  AND (CAST(:source AS uuid) IS NULL OR connection_id=:source)"""),
                    {"id": before, "source": connection_id},
                )
                .mappings()
                .one_or_none()
            )
            if not anchor:
                raise Forbidden("分页位置不存在或不属于所选来源")
        rows = conn.execute(
            text("""SELECT r.id,r.connection_id,c.name AS source_name,r.status,r.started_at,
                r.completed_at,r.product_count,r.departure_count,r.source_departure_count,
                r.quarantined_count,r.error_code FROM sync_run r
                JOIN supplier_connection c ON c.id=r.connection_id
                WHERE (CAST(:source AS uuid) IS NULL OR r.connection_id=:source)
                AND (CAST(:before AS uuid) IS NULL OR (r.started_at,r.id)<(:started,:before))
                ORDER BY r.started_at DESC,r.id DESC LIMIT :limit"""),
            {
                "source": connection_id,
                "before": before,
                "started": anchor["started_at"] if anchor else None,
                "limit": limit + 1,
            },
        ).mappings()
        return _page([dict(row) for row in rows], limit)


def issues(engine, actor, run_id, *, after=None, limit=100):
    _limit(limit)
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "sync_worker", "product_editor", "auditor")
        if not conn.scalar(text("SELECT id FROM sync_run WHERE id=:id"), {"id": run_id}):
            raise Forbidden("同步批次不存在或无权限")
        anchor = None
        if after:
            anchor = (
                conn.execute(
                    text(
                        "SELECT external_id,id FROM source_issue WHERE id=:id AND sync_run_id=:run"
                    ),
                    {"id": after, "run": run_id},
                )
                .mappings()
                .one_or_none()
            )
            if not anchor:
                raise Forbidden("分页位置不存在或不属于所选批次")
        rows = conn.execute(
            text("""SELECT id,connection_id,sync_run_id,external_id,code,created_at FROM source_issue
                WHERE sync_run_id=:run AND (CAST(:after AS uuid) IS NULL OR
                  (external_id,id)>(:external,:after)) ORDER BY external_id,id LIMIT :limit"""),
            {
                "run": run_id,
                "after": after,
                "external": anchor["external_id"] if anchor else None,
                "limit": limit + 1,
            },
        ).mappings()
        return _page([dict(row) for row in rows], limit)


def _departure_observation(body):
    if body is None:
        return None
    # A source record can also contain private prices, URLs or arbitrary additions.
    # Only the fields needed to locate the upstream plan leave this service.
    return {
        "period_code": str(body.get("periodCode", ""))[:300],
        "depart_date": body.get("departDate"),
        "return_date": body.get("returnDate"),
        "route_id": body.get("routeId"),
    }


def issue_detail(engine, actor, issue_id: UUID):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "sync_worker", "product_editor", "auditor")
        # One statement selects a consistent successful batch and its snapshot.
        # A newer failed/running attempt never supplies a claimed repair.
        row = (
            conn.execute(
                text("""SELECT i.id,i.connection_id,i.sync_run_id,i.external_id,i.code,i.created_at,
              c.name AS source_name,c.active AS source_active,
              old.body AS original_body,old.observed_at AS original_observed_at,
              latest.id AS latest_run_id,latest.completed_at AS latest_completed_at,
              latest.window_start,latest.window_end,
              current.body AS current_body,current.observed_at AS current_observed_at,
              attempt.id AS attempt_id,attempt.status AS attempt_status,
              attempt.started_at AS attempt_started_at,attempt.error_code AS attempt_error
            FROM source_issue i JOIN supplier_connection c ON c.id=i.connection_id
            LEFT JOIN source_snapshot old ON old.sync_run_id=i.sync_run_id
              AND old.connection_id=i.connection_id AND old.external_id=i.external_id
              AND old.entity_type='departure'
            LEFT JOIN LATERAL (
              SELECT r.id,r.completed_at,r.window_start,r.window_end FROM sync_run r
              WHERE r.connection_id=i.connection_id AND r.status='published'
              ORDER BY r.completed_at DESC,r.started_at DESC,r.id DESC LIMIT 1
            ) latest ON true
            LEFT JOIN source_snapshot current ON current.sync_run_id=latest.id
              AND current.connection_id=i.connection_id AND current.external_id=i.external_id
              AND current.entity_type='departure'
            LEFT JOIN LATERAL (
              SELECT r.id,r.status,r.started_at,r.error_code FROM sync_run r
              WHERE r.connection_id=i.connection_id
              ORDER BY r.started_at DESC,r.id DESC LIMIT 1
            ) attempt ON true
            WHERE i.id=:id AND i.supplier_org_id=warehouse_org_id()"""),
                {"id": issue_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("同步异常不存在或无权限")
        current = _departure_observation(row["current_body"])
        state = (
            "not_observed"
            if current is None
            else "unlinked"
            if current["route_id"] == 0
            else "linked"
        )
        return {
            **{
                key: row[key]
                for key in (
                    "id",
                    "connection_id",
                    "sync_run_id",
                    "external_id",
                    "code",
                    "created_at",
                    "source_name",
                    "source_active",
                )
            },
            "original": _departure_observation(row["original_body"]),
            "original_observed_at": row["original_observed_at"],
            "latest_successful_scan": {
                "run_id": row["latest_run_id"],
                "completed_at": row["latest_completed_at"],
                "window_start": row["window_start"],
                "window_end": row["window_end"],
                "observation": current,
                "observed_at": row["current_observed_at"],
                "state": state,
            }
            if row["latest_run_id"]
            else None,
            "latest_attempt": {
                "run_id": row["attempt_id"],
                "status": row["attempt_status"],
                "started_at": row["attempt_started_at"],
                "error_code": row["attempt_error"],
            }
            if row["attempt_id"]
            else None,
        }
