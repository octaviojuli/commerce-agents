"""Explicit offline administration and read-only synchronization commands."""

import argparse
import asyncio
import importlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

from . import jobs
from .admin import grant_auth, grant_runtime, migrate, onboard
from .catalog import synchronize
from .integrations import SourceError
from .persistence import Principal, engine_for, require_runtime_role


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    group = sub.add_parser("group-source-cooldowns")
    group.add_argument("--group", type=UUID, required=True)
    group.add_argument("--connection", type=UUID, action="append", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("destination", type=Path)
    backup.add_argument("--objects", type=Path, required=True)
    cycle = sub.add_parser("backup-cycle")
    cycle.add_argument("--root", type=Path, required=True)
    cycle.add_argument("--objects", type=Path, required=True)
    cycle.add_argument("--target-database", required=True)
    cycle.add_argument("--max-bytes", type=int, required=True)
    cycle.add_argument("--reserve-bytes", type=int, required=True)
    backup_status = sub.add_parser("backup-status")
    backup_status.add_argument("--root", type=Path, required=True)
    backup_status.add_argument("--max-age-seconds", type=int, required=True)
    inspect = sub.add_parser("verify-backup")
    inspect.add_argument("bundle", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("bundle", type=Path)
    restore.add_argument("--objects", type=Path, required=True)
    restore.add_argument("--target-database", required=True)
    restore.add_argument("--runtime-role", required=True)
    restore.add_argument("--auth-role", required=True)
    verify = sub.add_parser("verify-restore")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--objects", type=Path, required=True)
    objects = sub.add_parser("collect-objects")
    objects.add_argument("--objects", type=Path, required=True)
    objects.add_argument("--target-database", required=True)
    objects.add_argument("--journal", type=Path, required=True)
    objects.add_argument("--retention-days", type=int, default=7)
    objects.add_argument("--apply", action="store_true")
    legacy = sub.add_parser("import-legacy-case")
    legacy.add_argument("bundle", type=Path)
    legacy.add_argument("--target-database", required=True)
    legacy.add_argument("--note", required=True)
    legacy.add_argument("--apply", action="store_true")
    sub.add_parser("migrate")
    platform = sub.add_parser("platform-sales")
    platform.add_argument("--enabled", choices=("true", "false"), required=True)
    advisor = sub.add_parser("create-advisor")
    advisor.add_argument("--email", required=True)
    advisor.add_argument("--password-stdin", action="store_true", required=True)
    grant = sub.add_parser("grant-runtime")
    grant.add_argument("role")
    grant_auth_command = sub.add_parser("grant-auth")
    grant_auth_command.add_argument("role")
    setup = sub.add_parser("onboard")
    setup.add_argument("--supplier", required=True)
    setup.add_argument("--buyer", required=True)
    setup.add_argument("--worker-email", required=True)
    sync = sub.add_parser("sync")
    sync.add_argument("--connection", type=UUID, required=True)
    sync.add_argument("--organization", type=UUID, required=True)
    sync.add_argument("--user", type=UUID, required=True)
    sync.add_argument("--factory")
    sync.add_argument("--connections", default=os.environ.get("WAREHOUSE_CONNECTORS_CONFIG"))
    sync.add_argument("--start", type=date.fromisoformat)
    sync.add_argument("--end", type=date.fromisoformat)
    for name in ("worker", "retry-sync"):
        worker = sub.add_parser(name)
        worker.add_argument("--connection", type=UUID, required=True)
        worker.add_argument("--organization", type=UUID, required=True)
        worker.add_argument("--user", type=UUID, required=True)
        if name == "worker":
            worker.add_argument("--factory")
            worker.add_argument(
                "--connections", default=os.environ.get("WAREHOUSE_CONNECTORS_CONFIG")
            )
            worker.add_argument("--interval", type=int, default=jobs.DEFAULT_INTERVAL_SECONDS)
            worker.add_argument("--once", action="store_true")
    reparse = sub.add_parser("reparse-diff")
    reparse.add_argument("--organization", type=UUID, required=True)
    reparse.add_argument("--user", type=UUID, required=True)
    reparse.add_argument("--apply", action="store_true")
    reparse.add_argument("--after", type=UUID)
    reparse.add_argument("--limit", type=int, default=100)
    reparse.add_argument(
        "--report", type=Path, default=Path(".warehouse/reparse-diff.private.json")
    )
    document_worker = sub.add_parser("document-worker")
    document_worker.add_argument("--organization", type=UUID, required=True)
    document_worker.add_argument("--user", type=UUID, required=True)
    document_worker.add_argument("--once", action="store_true")
    test_publisher = sub.add_parser("route-test-publish")
    test_publisher.add_argument("--organization", type=UUID, required=True)
    test_publisher.add_argument("--user", type=UUID, required=True)
    test_publisher.add_argument("--connection", type=UUID, required=True)
    test_publisher.add_argument("--once", action="store_true")
    content_worker = sub.add_parser("route-content-worker")
    content_worker.add_argument("--organization", type=UUID, required=True)
    content_worker.add_argument("--user", type=UUID, required=True)
    content_worker.add_argument("--once", action="store_true")
    attachment_worker = sub.add_parser("attachment-worker")
    attachment_worker.add_argument("--connection", type=UUID, required=True)
    attachment_worker.add_argument("--organization", type=UUID, required=True)
    attachment_worker.add_argument("--user", type=UUID, required=True)
    attachment_worker.add_argument("--once", action="store_true")
    for name in ("outbox-worker", "outbox-status", "rebuild-search", "retry-outbox", "operations"):
        operation = sub.add_parser(name)
        operation.add_argument("--organization", type=UUID, required=True)
        operation.add_argument("--user", type=UUID, required=True)
        if name == "outbox-worker":
            operation.add_argument("--once", action="store_true")
        if name == "retry-outbox":
            operation.add_argument("--event", type=UUID, required=True)
    args = parser.parse_args()
    selected_factory = None
    if args.command in {"backup-cycle", "backup-status"}:
        from sqlalchemy.exc import SQLAlchemyError

        from . import backup_jobs

        try:
            result = (
                backup_jobs.run(
                    os.environ["WAREHOUSE_ADMIN_URL"],
                    args.objects,
                    args.root,
                    expected_database=args.target_database,
                    max_bytes=args.max_bytes,
                    reserve_bytes=args.reserve_bytes,
                )
                if args.command == "backup-cycle"
                else backup_jobs.status(args.root, max_age_seconds=args.max_age_seconds)
            )
            print(json.dumps(result))
            if args.command == "backup-status" and (
                not result["fresh"] or result["status"] in {"failed", "interrupted"}
            ):
                parser.exit(1)
        except backup_jobs.BackupJobError as error:
            parser.exit(1, f"{error}\n")
        except (OSError, ValueError, SQLAlchemyError, KeyError):
            parser.exit(1, "BACKUP_JOB_UNCONFIRMED\n")
        return
    if args.command in ("sync", "worker", "group-source-cooldowns"):
        from . import source_limits

    if args.command in ("sync", "worker"):
        if args.connections is not None:
            if args.factory is not None:
                parser.error("连接绑定配置不能与 --factory 混用")
            from tour.api.warehouse_connector import connector_registry

            try:
                selected_factory = connector_registry(
                    args.connections, connection_id=args.connection
                )[args.connection]
            except ValueError as error:
                parser.exit(1, f"{error}\n")
        else:
            module, symbol = (args.factory or "tour.api.warehouse_connector:connect").split(":", 1)
            factory = getattr(importlib.import_module(module), symbol)
            settings = dict(os.environ)

            def selected_factory():
                return factory(settings)

    if args.command == "collect-objects":
        from sqlalchemy.exc import SQLAlchemyError

        from . import object_gc

        engine = None
        try:
            engine = engine_for(os.environ["WAREHOUSE_ADMIN_URL"])
            result = object_gc.collect(
                engine,
                args.objects,
                expected_database=args.target_database,
                journal=args.journal,
                apply=args.apply,
                days=args.retention_days,
            )
            print(json.dumps(result))
        except object_gc.CollectionError as error:
            parser.exit(1, f"{error}\n")
        except (OSError, ValueError, SQLAlchemyError, KeyError):
            parser.exit(1, "对象检查或清理未完成；请核对私有配置及清理日志，勿假定已回滚。\n")
        finally:
            if engine is not None:
                engine.dispose()
        return
    if args.command in {"backup", "verify-backup", "restore", "verify-restore"}:
        from sqlalchemy.exc import SQLAlchemyError

        from . import recovery

        try:
            if args.command == "backup":
                manifest = recovery.backup(
                    os.environ["WAREHOUSE_ADMIN_URL"], args.objects, args.destination
                )
                result = {
                    "backup_created": True,
                    "revision": manifest.revision,
                    "tables": len(manifest.tables),
                    "objects": len(manifest.objects),
                    "elapsed_seconds": manifest.elapsed_seconds,
                }
            elif args.command == "restore":
                result = recovery.restore(
                    os.environ["WAREHOUSE_RESTORE_ADMIN_URL"],
                    args.bundle,
                    args.objects,
                    expected_database=args.target_database,
                    runtime_role=args.runtime_role,
                    auth_role=args.auth_role,
                )
            else:
                manifest = recovery.inspect_bundle(args.bundle)
                result = (
                    recovery.verify(
                        os.environ["WAREHOUSE_RESTORE_ADMIN_URL"], args.objects, manifest
                    )
                    if args.command == "verify-restore"
                    else {"bundle_verified": True, "objects": len(manifest.objects)}
                )
            print(json.dumps(result))
        except recovery.RecoveryError as error:
            parser.exit(1, f"{error}\n")
        except (OSError, ValueError, SQLAlchemyError, KeyError):
            parser.exit(1, "备份或恢复未通过；请核对私有配置、文件权限及数据库状态。\n")
        return
    # URLs contain secrets; accept only environment, never command-line or printed config.
    admin = args.command not in (
        "sync",
        "worker",
        "retry-sync",
        "document-worker",
        "route-test-publish",
        "route-content-worker",
        "attachment-worker",
        "outbox-worker",
        "outbox-status",
        "rebuild-search",
        "retry-outbox",
        "operations",
    )
    url = os.environ["WAREHOUSE_ADMIN_URL" if admin else "WAREHOUSE_DATABASE_URL"]
    if args.command == "migrate":
        migrate(url)
        print(json.dumps({"migration": "head"}))
        return
    engine = engine_for(url)
    try:
        if args.command in {"platform-sales", "create-advisor"}:
            from . import platform_sales

            if args.command == "platform-sales":
                print(json.dumps(platform_sales.configure(engine, enabled=args.enabled == "true")))
            else:
                actor = platform_sales.create_advisor(
                    engine, email=args.email, password=sys.stdin.readline().rstrip("\r\n")
                )
                print(
                    json.dumps(
                        {"user_id": str(actor.user_id), "workspace_id": str(actor.organization_id)}
                    )
                )
        elif args.command == "group-source-cooldowns":
            source_limits.group_connections(engine, args.group, args.connection)
            print(json.dumps({"connections_grouped": len(args.connection)}))
        elif args.command == "import-legacy-case":
            from sqlalchemy.exc import SQLAlchemyError

            from . import legacy_cases

            try:
                if (
                    not args.bundle.is_file()
                    or args.bundle.is_symlink()
                    or args.bundle.stat().st_mode & 0o077
                ):
                    raise ValueError("历史迁移包必须是私有普通文件")
                with args.bundle.open("rb") as source:
                    raw = source.read(legacy_cases.MAX_BYTES + 1)
                if len(raw) > legacy_cases.MAX_BYTES:
                    raise ValueError("历史迁移包超过上限")
                bundle = legacy_cases.Bundle.model_validate_json(raw)
                result = legacy_cases.import_case(
                    engine,
                    bundle,
                    expected_database=args.target_database,
                    note=args.note,
                    apply=args.apply,
                )
                print(json.dumps(result))
            except (OSError, ValueError, SQLAlchemyError):
                parser.exit(1, "历史导入失败；请核对专用管理连接、归属、私有迁移包及版本冲突。\n")
        elif args.command == "reparse-diff":
            from tour.api.warehouse_documents import parse

            from . import reparse_diff
            from .assets import LocalObjectStore

            require_runtime_role(engine)
            args.report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(
                os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                "w",
                encoding="utf-8",
            ) as output:
                os.chmod(args.report, 0o600)
                result = reparse_diff.run(
                    engine,
                    Principal(args.user, args.organization),
                    LocalObjectStore(Path(os.environ["WAREHOUSE_OBJECT_ROOT"])),
                    parse,
                    apply=args.apply,
                    limit=args.limit,
                    after=args.after,
                )
                json.dump(result, output, ensure_ascii=False, indent=2)
            print(
                json.dumps(
                    {
                        "compared": len(result["items"]),
                        "queued": sum(item["queued"] for item in result["items"]),
                        "next_cursor": result["next_cursor"],
                        "report": str(args.report),
                        "published": False,
                    }
                )
            )
        elif args.command == "operations":
            from . import operations

            require_runtime_role(engine)
            print(
                json.dumps(
                    operations.snapshot(engine, Principal(args.user, args.organization)),
                    default=str,
                )
            )
        elif args.command in ("outbox-worker", "outbox-status", "rebuild-search", "retry-outbox"):
            import time

            from . import outbox

            require_runtime_role(engine)
            actor = Principal(args.user, args.organization)
            if args.command == "outbox-status":
                print(json.dumps(outbox.status(engine, actor), default=str))
            elif args.command == "rebuild-search":
                print(json.dumps({"changed_products": outbox.rebuild(engine, actor)}))
            elif args.command == "retry-outbox":
                print(json.dumps({"retried": outbox.retry(engine, actor, args.event)}))
            else:
                while True:
                    result = outbox.run_once(engine, actor)
                    if args.once:
                        print(json.dumps(result))
                        break
                    time.sleep(2 if not result.get("consumed") else 0.1)
        elif args.command == "attachment-worker":
            import time

            from . import document_fetches
            from .assets import LocalObjectStore

            require_runtime_role(engine)
            hosts = {
                host.strip().lower()
                for host in os.environ.get("WAREHOUSE_ATTACHMENT_HOSTS", "").split(",")
                if host.strip()
            }
            if not hosts:
                parser.exit(1, "需配置 WAREHOUSE_ATTACHMENT_HOSTS 精确主机列表\n")
            actor = Principal(args.user, args.organization)
            store = LocalObjectStore(Path(os.environ["WAREHOUSE_OBJECT_ROOT"]))
            document_fetches.seed(engine, actor, args.connection)
            while True:
                result = document_fetches.run_once(engine, actor, args.connection, store, hosts)
                if args.once:
                    print(json.dumps({"result": result}))
                    break
                time.sleep(5 if result is None else 1)
        elif args.command == "route-content-worker":
            import time

            from tour.api.route_editor_model import ModelEditor

            from . import route_candidates

            require_runtime_role(engine)
            actor = Principal(args.user, args.organization)
            editor = ModelEditor()
            last_seed = 0
            try:
                while True:
                    if time.monotonic() - last_seed > 60:
                        route_candidates.seed(engine, actor, editor.model)
                        last_seed = time.monotonic()
                    result = route_candidates.run_once(engine, actor, editor)
                    if args.once:
                        print(json.dumps({"result": result}))
                        break
                    if result is None:
                        time.sleep(5)
            finally:
                editor.close()
        elif args.command == "route-test-publish":
            import time

            from .route_test_publication import run_once

            require_runtime_role(engine)
            actor = Principal(args.user, args.organization)
            while True:
                result = run_once(engine, actor, args.connection)
                if result is not None or args.once:
                    print(json.dumps({"result": result}), flush=True)
                if args.once:
                    break
                time.sleep(15 if result is None else 1)
        elif args.command == "document-worker":
            import time

            from tour.api.warehouse_documents import parse

            from .assets import LocalObjectStore
            from .documents import run_once

            require_runtime_role(engine)
            store = LocalObjectStore(Path(os.environ["WAREHOUSE_OBJECT_ROOT"]))
            actor = Principal(args.user, args.organization)
            while True:
                result = run_once(engine, actor, store, parse)
                if args.once:
                    print(json.dumps({"result": result}))
                    break
                if result is None:
                    time.sleep(5)
        elif args.command == "grant-runtime":
            grant_runtime(engine, args.role)
            print(json.dumps({"granted": args.role}))
        elif args.command == "grant-auth":
            grant_auth(engine, args.role)
            print(json.dumps({"granted": args.role}))
        elif args.command == "onboard":
            print(json.dumps(onboard(engine, args.supplier, args.buyer, args.worker_email)))
        elif args.command == "retry-sync":
            require_runtime_role(engine)
            print(
                json.dumps(
                    {
                        "retried": jobs.retry(
                            engine, Principal(args.user, args.organization), args.connection
                        )
                    }
                )
            )
        elif args.command in ("sync", "worker"):
            require_runtime_role(engine)
            assert selected_factory is not None

            async def run():
                if args.command == "worker":
                    return await jobs.work(
                        engine,
                        Principal(args.user, args.organization),
                        {args.connection: selected_factory},
                        interval_seconds=args.interval,
                        once=args.once,
                    )
                async with source_limits.open_connector(
                    engine,
                    Principal(args.user, args.organization),
                    args.connection,
                    selected_factory,
                ) as connector:
                    return await synchronize(
                        engine,
                        Principal(args.user, args.organization),
                        args.connection,
                        connector,
                        start=args.start,
                        end=args.end,
                    )

            try:
                result = asyncio.run(run())
                print(json.dumps(result))
                if result and result.get("status") == "failed":
                    raise SystemExit(1)
            except SourceError as error:
                parser.exit(1, f"同步失败：{error.code}\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
