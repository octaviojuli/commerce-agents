"""Operate the isolated local warehouse without placing secrets on the command line."""

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import text

from cloud_warehouse.admin import grant_auth, grant_runtime, migrate, onboard
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.integrations import SourceError
from cloud_warehouse.persistence import Principal, engine_for, require_runtime_role

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".warehouse"


def source_settings():
    from dotenv import dotenv_values

    return {
        key: value
        for key, value in {**dotenv_values(ROOT / "examples/tour/.env"), **os.environ}.items()
        if value is not None
    }


def source_registry(context, settings):
    from tour.api.warehouse_connector import connect, connector_registry

    identifier = UUID(context["connection_id"])
    configured = os.environ.get("WAREHOUSE_CONNECTORS_CONFIG")
    local = STATE / "connections.json"
    if configured is not None or local.exists():
        return connector_registry(
            configured if configured is not None else str(local),
            settings,
            connection_id=identifier,
        )
    # Existing local single-source setups remain usable until explicitly bound.
    return {identifier: lambda: connect(settings)}


@asynccontextmanager
async def audit_connector():
    """Use the same registered source and cooldown context for offline read audits."""
    from cloud_warehouse.source_limits import open_connector

    config = json.loads((STATE / "database.json").read_text())
    context = json.loads((STATE / "b2b-context.json").read_text())
    engine = engine_for(config["runtime_url"])
    try:
        require_runtime_role(engine)
        source = UUID(context["connection_id"])
        actor = Principal(UUID(context["worker_id"]), UUID(context["supplier_id"]))
        registry = source_registry(context, source_settings())
        async with open_connector(engine, actor, source, registry[source]) as connector:
            yield connector
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "migrate",
            "onboard",
            "sync",
            "worker",
            "worker-once",
            "retry-sync",
            "status",
            "verify-sync",
            "test",
            "serve",
            "attachments",
            "parse-documents",
            "attachment-status",
            "outbox",
            "outbox-status",
            "rebuild-search",
            "operations",
        ),
    )
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("limit must be 1..1000")
    os.umask(0o077)
    config = json.loads((STATE / "database.json").read_text())
    if args.command == "serve":
        sys.path.insert(0, str(ROOT / "examples"))
        import uvicorn

        from cloud_warehouse.api import create_app
        from cloud_warehouse.assets import LocalObjectStore
        from cloud_warehouse.http_observation import configure_logging
        from tour.api.warehouse_chat import factory

        settings = source_settings()
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "TOUR_MODEL",
        ):
            if settings.get(key):
                os.environ[key] = settings[key]
        context = json.loads((STATE / "b2b-context.json").read_text())
        connectors = source_registry(context, settings)
        runtime, authentication = engine_for(config["runtime_url"]), engine_for(config["auth_url"])
        app = create_app(
            runtime,
            authentication,
            connectors,
            metrics_token=settings.get("WAREHOUSE_METRICS_TOKEN", config.get("metrics_token")),
            object_store=LocalObjectStore(STATE / "objects"),
            agent_factory=factory(runtime, connectors)
            if settings.get("ANTHROPIC_API_KEY") or settings.get("ANTHROPIC_AUTH_TOKEN")
            else None,
        )
        try:
            configure_logging()
            uvicorn.run(app, host="127.0.0.1", port=8005, proxy_headers=False, access_log=False)
        finally:
            runtime.dispose()
            authentication.dispose()
        return
    if args.command == "test":
        import pytest
        from sqlalchemy.engine import make_url

        for key, variable in (
            ("admin_url", "WAREHOUSE_TEST_ADMIN_URL"),
            ("runtime_url", "WAREHOUSE_TEST_DATABASE_URL"),
            ("auth_url", "WAREHOUSE_TEST_AUTH_URL"),
        ):
            os.environ[variable] = (
                make_url(config[key])
                .set(database="warehouse_test")
                .render_as_string(hide_password=False)
            )
        raise SystemExit(pytest.main([str(ROOT / "cloud-warehouse/tests"), "-q", "--tb=short"]))
    if args.command == "migrate":
        migrate(config["admin_url"])
        admin = engine_for(config["admin_url"])
        try:
            grant_runtime(admin, "warehouse_runtime")
            grant_auth(admin, "warehouse_auth")
        finally:
            admin.dispose()
        print("Migration and restricted runtime grants applied")
        return
    context_path = STATE / "b2b-context.json"
    if args.command == "onboard":
        if context_path.exists():
            print("Existing B2B context retained")
            return
        admin = engine_for(config["admin_url"])
        try:
            context = onboard(
                admin, "当前 B2B 授权源", "内部采购测试组织", "b2b-sync@warehouse.invalid"
            )
            context_path.write_text(json.dumps(context))
        finally:
            admin.dispose()
        print("B2B source and explicit internal buyer grant created")
        return
    engine = engine_for(config["runtime_url"])
    try:
        require_runtime_role(engine)
        context = json.loads(context_path.read_text())
        actor = Principal(UUID(context["worker_id"]), UUID(context["supplier_id"]))
        if args.command == "operations":
            from cloud_warehouse import operations

            print(json.dumps(operations.snapshot(engine, actor), default=str))
        elif args.command in ("outbox", "outbox-status", "rebuild-search"):
            from cloud_warehouse import outbox

            if args.command == "rebuild-search":
                print(json.dumps({"changed_products": outbox.rebuild(engine, actor)}))
            else:
                if args.command == "outbox":
                    for _ in range(args.limit):
                        result = outbox.run_once(engine, actor)
                        print(json.dumps(result), flush=True)
                        if not result.get("consumed"):
                            break
                print(json.dumps(outbox.status(engine, actor), default=str))
        elif args.command in ("attachments", "parse-documents"):
            import time

            from cloud_warehouse import document_fetches, documents
            from cloud_warehouse.assets import LocalObjectStore

            store = LocalObjectStore(STATE / "objects")
            if args.command == "attachments":
                policy = json.loads((STATE / "attachment-policy.json").read_text())
                hosts = set(policy["allowed_hosts"])
                document_fetches.seed(engine, actor, UUID(context["connection_id"]))
            else:
                sys.path.insert(0, str(ROOT / "examples"))
                from tour.api.warehouse_documents import parse
            counts = {}
            for _ in range(args.limit):
                result = (
                    document_fetches.run_once(
                        engine, actor, UUID(context["connection_id"]), store, hosts
                    )
                    if args.command == "attachments"
                    else documents.run_once(engine, actor, store, parse)
                )
                if result is None:
                    break
                status = (
                    result
                    if args.command == "attachments"
                    else result["status"]
                    if isinstance(result, dict)
                    else "parsed"
                )
                counts[status] = counts.get(status, 0) + 1
                print(json.dumps({"processed": sum(counts.values()), "status": status}), flush=True)
                if args.command == "attachments":
                    time.sleep(1)
            print(json.dumps({"counts": counts}))
        elif args.command == "attachment-status":
            from cloud_warehouse.persistence import transaction

            with transaction(engine, actor) as conn:
                summary = {}
                for table in ("document_fetch", "document_asset"):
                    summary[table] = dict(
                        conn.execute(
                            text(
                                f"SELECT status,count(*) FROM {table} WHERE connection_id=:id GROUP BY status"
                            ),
                            {"id": UUID(context["connection_id"])},
                        ).all()
                    )
                summary["published"] = conn.scalar(
                    text("SELECT count(*) FROM document_publication WHERE connection_id=:id"),
                    {"id": UUID(context["connection_id"])},
                )
            print(json.dumps(summary))
        elif args.command == "retry-sync":
            from cloud_warehouse.jobs import retry

            print(json.dumps({"retried": retry(engine, actor, UUID(context["connection_id"]))}))
        elif args.command in ("sync", "worker", "worker-once"):
            sys.path.insert(0, str(ROOT / "examples"))

            connectors = source_registry(context, source_settings())

            async def run():
                if args.command != "sync":
                    from cloud_warehouse.jobs import work

                    return await work(
                        engine,
                        actor,
                        connectors,
                        once=args.command == "worker-once",
                    )
                from cloud_warehouse.source_limits import open_connector

                async with open_connector(
                    engine,
                    actor,
                    UUID(context["connection_id"]),
                    connectors[UUID(context["connection_id"])],
                ) as connector:
                    return await synchronize(
                        engine, actor, UUID(context["connection_id"]), connector
                    )

            try:
                result = asyncio.run(run())
                print(json.dumps(result))
                if result and result.get("status") == "failed":
                    raise SystemExit(1)
            except SourceError as error:
                parser.exit(1, f"同步失败：{error.code}\n")
        elif args.command == "verify-sync":
            from cloud_warehouse.persistence import transaction

            with transaction(engine, actor) as conn:
                runs = (
                    conn.execute(
                        text(
                            "SELECT id,product_count,departure_count,source_departure_count,quarantined_count FROM sync_run WHERE status='published' ORDER BY started_at DESC LIMIT 2"
                        )
                    )
                    .mappings()
                    .all()
                )
                if len(runs) < 2:
                    raise SystemExit("Two completed synchronizations are required")
                newest, previous = runs
                current_rows = (
                    conn.execute(
                        text(
                            "SELECT entity_type,external_id,body,body_hash FROM source_snapshot WHERE sync_run_id=:id"
                        ),
                        {"id": newest["id"]},
                    )
                    .mappings()
                    .all()
                )
                old_rows = (
                    conn.execute(
                        text(
                            "SELECT entity_type,external_id,body_hash FROM source_snapshot WHERE sync_run_id=:id"
                        ),
                        {"id": previous["id"]},
                    )
                    .mappings()
                    .all()
                )
                old_hashes = {
                    (r["entity_type"], r["external_id"]): r["body_hash"] for r in old_rows
                }
                expected_products = {
                    uuid5(UUID(context["connection_id"]), "route:" + r["external_id"])
                    for r in current_rows
                    if r["entity_type"] == "route"
                }
                expected_departures = {
                    uuid5(UUID(context["connection_id"]), "departure:" + r["external_id"])
                    for r in current_rows
                    if r["entity_type"] == "departure" and r["body"]["routeId"] != 0
                }
                products = set(
                    conn.execute(
                        text("SELECT id FROM supplier_product WHERE sync_run_id=:id"),
                        {"id": newest["id"]},
                    ).scalars()
                )
                departures = set(
                    conn.execute(
                        text("SELECT id FROM departure WHERE sync_run_id=:id"), {"id": newest["id"]}
                    ).scalars()
                )
                issues = conn.scalar(
                    text("SELECT count(*) FROM source_issue WHERE sync_run_id=:id"),
                    {"id": newest["id"]},
                )
                assert products == expected_products and departures == expected_departures
                assert (
                    len(products) == newest["product_count"]
                    and len(departures) == newest["departure_count"]
                )
                assert (
                    issues == newest["quarantined_count"]
                    and len(departures) + issues == newest["source_departure_count"]
                )
                assert (
                    len(current_rows) == newest["product_count"] + newest["source_departure_count"]
                )
                result = {
                    "verified": True,
                    "products": len(products),
                    "published_departures": len(departures),
                    "quarantined_departures": issues,
                    "all_source_departures": newest["source_departure_count"],
                    "stable_source_id_sets": set(old_hashes)
                    == {(r["entity_type"], r["external_id"]) for r in current_rows},
                    "changed_source_records": sum(
                        old_hashes.get((r["entity_type"], r["external_id"])) != r["body_hash"]
                        for r in current_rows
                    ),
                }
                (STATE / "sync-verification.json").write_text(json.dumps(result))
                print(json.dumps(result))
        else:
            from cloud_warehouse.persistence import transaction

            with transaction(engine, actor) as conn:
                runs = (
                    conn.execute(
                        text(
                            "SELECT id,status,product_count,departure_count,source_departure_count,quarantined_count,error_code,started_at,completed_at FROM sync_run ORDER BY started_at DESC LIMIT 5"
                        )
                    )
                    .mappings()
                    .all()
                )
                stats = (
                    conn.execute(
                        text(
                            "SELECT count(*) AS departures,min(depart_date) AS first_departure,max(depart_date) AS last_departure,count(*) FILTER(WHERE available_seats IS NULL) AS unknown_inventory FROM departure"
                        )
                    )
                    .mappings()
                    .one()
                )
                print(
                    json.dumps(
                        {"runs": [dict(r) for r in runs], "catalog": dict(stats)}, default=str
                    )
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
