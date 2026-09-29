"""Supplier-scoped operational evidence; no external notification or business repair."""

import logging
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import outbox
from .catalog import INVENTORY_FRESHNESS_SECONDS
from .changes import audit
from .persistence import Forbidden, require_role, transaction

FAILURE_CODES = {
    "APPLY_CONFLICT",
    "INVENTORY_LIMIT_REJECTED",
    "DUPLICATE_STOCK_REFERENCE",
    "APPLY_DATABASE_ERROR",
}


def record_change_failure(engine, actor, change_id, code):
    """Record after business rollback; observability must not replace the original error."""
    try:
        with transaction(engine, actor) as conn:
            require_role(conn, "supplier_admin")
            kind = conn.scalar(
                text("""SELECT r.kind FROM change_request r JOIN change_approval a ON a.change_id=r.id
              WHERE r.id=:id AND r.organization_id=warehouse_org_id() AND a.approved_by=warehouse_user_id()"""),
                {"id": change_id},
            )
            if kind:
                audit(
                    conn,
                    actor,
                    "change.apply_failed",
                    change_id,
                    {
                        "code": code if code in FAILURE_CODES else "APPLY_CONFLICT",
                        "change_kind": kind,
                    },
                )
    except (Forbidden, SQLAlchemyError):
        # Do not log exception objects: DB errors may include source values or credentials.
        logging.getLogger(__name__).error("WAREHOUSE_OPERATION_OBSERVATION_FAILED")


def snapshot(engine, actor):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "auditor", "sync_worker")
        observed = conn.scalar(text("SELECT now()"))
        sources = [
            dict(row)
            for row in conn.execute(
                text("""SELECT c.id,c.name,c.active,c.connector_type,c.capabilities,
          j.id AS job_id,j.status AS job_status,j.enabled,j.interval_seconds,j.last_error AS job_error,
          j.worker_last_seen_at,j.lease_until,j.next_run_at,j.provider_not_before,
          recent.id AS latest_run_id,recent.status AS latest_run_status,recent.error_code AS latest_error,
          recent.started_at AS latest_started_at,success.completed_at AS last_success_at,
          success.started_at AS last_success_started_at,
          success.quarantined_count,success.source_departure_count,
          stats.runs_24h,stats.successes_24h
          FROM supplier_connection c LEFT JOIN sync_job j ON j.connection_id=c.id
          LEFT JOIN LATERAL (SELECT id,status,error_code,started_at FROM sync_run
            WHERE connection_id=c.id ORDER BY started_at DESC,id DESC LIMIT 1) recent ON true
          LEFT JOIN LATERAL (SELECT started_at,completed_at,quarantined_count,source_departure_count FROM sync_run
            WHERE connection_id=c.id AND status='published' ORDER BY completed_at DESC,id DESC LIMIT 1) success ON true
          LEFT JOIN LATERAL (SELECT count(*) FILTER(WHERE status IN ('published','failed')) AS runs_24h,
            count(*) FILTER(WHERE status='published') AS successes_24h FROM sync_run
            WHERE connection_id=c.id AND started_at>=now()-interval '24 hours') stats ON true
          WHERE c.supplier_org_id=warehouse_org_id() ORDER BY c.id""")
            ).mappings()
        ]
        inventory = dict(
            conn.execute(
                text("""SELECT count(*) AS departures,
          count(*) FILTER(WHERE d.availability_expires_at IS NULL OR d.availability_expires_at<=now()) AS expired,
          count(*) FILTER(WHERE d.available_seats IS NULL) AS unknown
          FROM departure d JOIN supplier_connection c ON c.id=d.connection_id
          JOIN supplier_product p ON p.id=d.product_id
          WHERE d.supplier_org_id=warehouse_org_id() AND c.active AND d.status='published' AND p.status='published'
            AND c.capabilities->>'inventory_owner' IS DISTINCT FROM 'warehouse'
            AND d.depart_date>=(now() AT TIME ZONE COALESCE(c.capabilities->>'business_timezone','Asia/Shanghai'))::date""")
            )
            .mappings()
            .one()
        )
        ledger = dict(
            conn.execute(
                text("""SELECT count(*) AS pools,count(*) FILTER(WHERE
            i.total<>COALESCE(m.total,0) OR i.sold<>COALESCE(m.sold,0) OR i.blocked<>COALESCE(m.blocked,0)
            OR i.held<>0 OR i.version<>COALESCE(m.entries,0)) AS mismatches
          FROM inventory_pool i LEFT JOIN LATERAL (SELECT sum(delta_total) AS total,sum(delta_sold) AS sold,
            sum(delta_blocked) AS blocked,count(*) AS entries FROM inventory_movement WHERE pool_id=i.id) m ON true
          WHERE i.supplier_org_id=warehouse_org_id()""")
            )
            .mappings()
            .one()
        )
        documents = dict(
            conn.execute(
                text("""SELECT
          (SELECT count(*) FROM document_fetch f JOIN supplier_product p ON p.id=f.product_id
            WHERE f.supplier_org_id=warehouse_org_id() AND f.product_version=p.version AND f.status='failed') AS fetch_failed,
          (SELECT count(*) FROM document_asset a JOIN supplier_product p ON p.id=a.product_id
            WHERE a.supplier_org_id=warehouse_org_id() AND a.product_version=p.version AND a.status='failed') AS parse_failed,
          (SELECT count(*) FROM document_asset a JOIN supplier_product p ON p.id=a.product_id
            WHERE a.supplier_org_id=warehouse_org_id() AND a.product_version=p.version AND a.status='parsed'
              AND NOT EXISTS(SELECT 1 FROM document_publication pub WHERE pub.product_id=p.id AND pub.product_version=p.version)) AS awaiting_review
          """)
            )
            .mappings()
            .one()
        )
        documents["failure_stages"] = [
            dict(row)
            for row in conn.execute(
                text("""SELECT
          CASE WHEN last_error IN ('DOCUMENT_PARSE_OPEN_FAILED','DOCUMENT_PARSE_SEGMENT_FAILED','DOCUMENT_PARSE_DAYS_FAILED','DOCUMENT_PARSE_TERMS_FAILED','DOCUMENT_PARSE_VALIDATION_FAILED','DOCUMENT_TEXT_MISSING','DOCUMENT_TEXT_INSUFFICIENT','DOCUMENT_EXTRACTION_RETIRED') THEN last_error ELSE 'DOCUMENT_PARSE_FAILED' END AS code,count(*) AS count
          FROM document_asset WHERE supplier_org_id=warehouse_org_id() AND status='failed'
          GROUP BY 1 ORDER BY 1""")
            ).mappings()
        ]
        failures = [
            dict(row)
            for row in conn.execute(
                text("""SELECT details->>'code' AS code,count(*) AS count,
          max(occurred_at) AS latest_at FROM audit_event
          WHERE organization_id=warehouse_org_id() AND action='change.apply_failed'
            AND occurred_at>=now()-interval '24 hours' GROUP BY details->>'code' ORDER BY code""")
            ).mappings()
        ]
    projection = outbox.status(engine, actor)
    issues = []

    def issue(code, severity, message, action, resource=None, count=None):
        issues.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "action": action,
                "resource_id": resource,
                "count": count,
            }
        )

    for source in sources:
        catalog_read = source.pop("capabilities").get("catalog_read", False)
        source["monitored"] = (
            source["active"] and source["connector_type"] != "excel" and catalog_read
        )
        source["stale_after_seconds"] = max(900, (source["interval_seconds"] or 300) * 3)
        source["sync_age_seconds"] = (
            max(0, int((observed - source["last_success_at"]).total_seconds()))
            if source["last_success_at"]
            else None
        )
        # The previous snapshot ages while it is fetched and while its replacement
        # is fetched. Last duration is an estimate, never an upstream freshness SLA.
        duration = (
            max(
                0.0, (source["last_success_at"] - source["last_success_started_at"]).total_seconds()
            )
            if source["last_success_at"] and source["last_success_started_at"]
            else None
        )
        source["last_scan_seconds"] = round(duration, 2) if duration is not None else None
        source["inventory_freshness_seconds"] = INVENTORY_FRESHNESS_SECONDS
        source["estimated_refresh_headroom_seconds"] = (
            round(INVENTORY_FRESHNESS_SECONDS - source["interval_seconds"] - 2 * duration - 5, 2)
            if duration is not None and source["interval_seconds"] is not None
            else None
        )
        if not source["monitored"]:
            continue
        identifier = source["id"]
        if source["job_id"] is None:
            issue(
                "SYNC_NOT_SCHEDULED",
                "warning",
                "供应源尚未配置持续同步",
                "配置目录 Worker 与同步任务；单次同步不会自动持续刷新。",
                identifier,
            )
        elif not source["enabled"]:
            issue(
                "SYNC_PAUSED",
                "info",
                "供应源同步已暂停",
                "确认暂停是否符合运营安排，数据仍会随时间过期。",
                identifier,
            )
        else:
            headroom = source["estimated_refresh_headroom_seconds"]
            if headroom is not None and headroom <= 0:
                issue(
                    "SYNC_FRESHNESS_GAP_RISK",
                    "warning",
                    "当前同步间隔和抓取耗时可能导致库存快照在下一批发布前过期",
                    "核对上游限流后缩短间隔；若抓取本身过慢，应改进增量接入。不要延长库存有效期掩盖延迟。",
                    identifier,
                )
            if not source["worker_last_seen_at"] or source[
                "worker_last_seen_at"
            ] < observed - timedelta(seconds=90):
                issue(
                    "SYNC_WORKER_STALE",
                    "warning",
                    "目录后台心跳缺失或超过 90 秒",
                    "检查后台进程、数据库连接及 Worker 身份。",
                    identifier,
                )
            if (
                source["job_status"] == "running"
                and source["lease_until"]
                and source["lease_until"] <= observed
            ):
                issue(
                    "SYNC_LEASE_EXPIRED",
                    "warning",
                    "同步执行租约已经过期",
                    "检查中断原因；后台恢复后按已有重试次数接管。",
                    identifier,
                )
            if source["job_status"] == "failed":
                issue(
                    "SYNC_JOB_FAILED",
                    "critical",
                    "目录调度已停止自动重试",
                    "核对失败原因后，在同步任务中明确重试。",
                    identifier,
                )
        if source["latest_error"] in ("SOURCE_AUTH_FAILED", "SOURCE_PERMISSION_DENIED") or source[
            "job_error"
        ] in ("SOURCE_AUTH_FAILED", "SOURCE_PERMISSION_DENIED"):
            issue(
                "SOURCE_AUTH_FAILURE",
                "critical",
                "最近同步存在供应商鉴权或权限失败",
                "由接入管理员核对供应商账号和权限；修复后重新同步。",
                identifier,
            )
        elif source["latest_run_status"] == "failed":
            issue(
                "SOURCE_SYNC_FAILED",
                "warning",
                "最近一次目录同步失败",
                "查看该来源批次记录；保留上次发布不代表最新数据可用。",
                identifier,
            )
        if (source["job_id"] is None or source["enabled"]) and (
            source["sync_age_seconds"] is None
            or source["sync_age_seconds"] > source["stale_after_seconds"]
        ):
            issue(
                "SOURCE_SYNC_STALE",
                "warning",
                "供应源尚未成功同步或已超过刷新期限",
                "检查调度、上游服务和冷却时间；不要将旧余位当作实时库存。",
                identifier,
            )
        if source["quarantined_count"]:
            issue(
                "SOURCE_QUARANTINED",
                "warning",
                "最新成功批次有未发布的异常团期",
                "在同步异常详情核对来源编号和线路关联。",
                identifier,
                source["quarantined_count"],
            )
    if inventory["expired"]:
        issue(
            "INVENTORY_SNAPSHOTS_EXPIRED",
            "warning",
            "已发布未来团期存在过期库存快照",
            "恢复来源刷新；顾问端继续按过期或未知展示。",
            count=inventory["expired"],
        )
    if ledger["mismatches"]:
        issue(
            "INVENTORY_LEDGER_MISMATCH",
            "critical",
            "托管库存余额或版本与流水不一致",
            "停止相关库存写入并核对完整账本；不要直接覆盖余额。",
            count=ledger["mismatches"],
        )
    if projection["failed"]:
        issue(
            "SEARCH_CONSUMER_FAILED",
            "critical",
            "检索事件存在处理失败",
            "核对检索 Worker 状态和固定错误码，修复后显式重试。",
            count=projection["failed"],
        )
    if (projection["projection"]["products"] or projection["pending"]) and (
        not projection["worker"] or projection["worker"]["stale"]
    ):
        issue(
            "SEARCH_WORKER_STALE",
            "warning",
            "检索后台心跳缺失或超过 30 秒",
            "检查检索后台进程；查询仍会回退到当前业务数据。",
        )
    if projection["pending"] and projection["oldest_pending_at"] < observed - timedelta(seconds=5):
        issue(
            "SEARCH_BACKLOG",
            "warning",
            "检索事件等待已超过 5 秒目标",
            "检查消费者吞吐和失败重试；业务事实已提交，不会因此回滚。",
            count=projection["pending"],
        )
    if documents["fetch_failed"] or documents["parse_failed"]:
        issue(
            "DOCUMENT_PROCESSING_FAILED",
            "warning",
            "当前版本线路文档存在下载或解析失败",
            "进入线路文档查看错误；必要时人工补充可解析原文件。",
            count=documents["fetch_failed"] + documents["parse_failed"],
        )
    for failure in failures:
        code = failure["code"]
        if code not in FAILURE_CODES:
            continue
        issue(
            code,
            "critical" if code == "APPLY_DATABASE_ERROR" else "warning",
            {
                "APPLY_CONFLICT": "近 24 小时有审批应用冲突",
                "INVENTORY_LIMIT_REJECTED": "近 24 小时有库存不足或非法余额请求被拒绝",
                "DUPLICATE_STOCK_REFERENCE": "近 24 小时有重复库存业务编号被拒绝",
                "APPLY_DATABASE_ERROR": "近 24 小时有审批执行数据库错误",
            }[code],
            "先核对变更状态与库存流水，再决定是否重试；数据库连接错误不能直接认定提交失败。"
            if code == "APPLY_DATABASE_ERROR"
            else "冲突或库存校验未提交业务变更；核对审批与库存流水，勿重复登记。",
            count=failure["count"],
        )
    severity = (
        "critical"
        if any(i["severity"] == "critical" for i in issues)
        else "warning"
        if any(i["severity"] == "warning" for i in issues)
        else "ok"
    )
    return {
        "observed_at": observed,
        "status": severity,
        "issues": issues,
        "sources": sources,
        "inventory": inventory,
        "ledger": ledger,
        "documents": documents,
        "search": projection,
        "apply_failures_24h": failures,
        "not_monitored": ["定时备份结果与异地副本", "接口延迟与报价成功率", "外部告警通知投递"],
    }
