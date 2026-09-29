"""Operate a bounded goods-stock test feed; credentials are supplied only through env."""

import argparse
import asyncio
import fcntl
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx

from cloud_warehouse.goods_stock import batches, scan, select_rows, settings
from cloud_warehouse.goods_stock_sync import identities, provision, publish_feed
from cloud_warehouse.integrations import SourceError
from cloud_warehouse.persistence import engine_for, require_runtime_role


async def run(args):
    config = json.loads(Path(args.config).read_text())
    feed_id = UUID(config["feed_id"])
    start, end = date.fromisoformat(config["start_date"]), date.fromisoformat(config["end_date"])
    base, company = config["base_url"], config["query_company_id"]
    settings(base, company, start, end)
    state = Path(args.state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "sync.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        failures = 0
        while True:
            status_path = state / "status.json"
            previous = json.loads(status_path.read_text()) if status_path.exists() else {}
            wait = max(0, previous.get("retry_at", 0) - time.time())
            if wait:
                if args.command != "worker":
                    raise SourceError("SOURCE_COOLDOWN_ACTIVE", retry_after_seconds=int(wait) + 1)
                await asyncio.sleep(wait)
            try:
                async with (
                    asyncio.timeout(120),
                    httpx.AsyncClient(
                        timeout=20, follow_redirects=False, trust_env=False
                    ) as client,
                ):
                    rows, observed = await scan(client, base, company, start, end)
                scanned = len(rows)
                rows = select_rows(rows, config.get("selection"))
                suppliers, grouped = batches(rows, observed, start, end)
                if args.command == "inspect":
                    print(
                        json.dumps(
                            {
                                "suppliers": len(suppliers),
                                "products": sum(len(x.routes) for x in grouped.values()),
                                "departures": len(rows),
                                "scanned_departures": scanned,
                            }
                        )
                    )
                    return
                if args.command == "provision":
                    admin = engine_for(os.environ["WAREHOUSE_ADMIN_URL"])
                    try:
                        provision(admin, feed_id, company, suppliers)
                    finally:
                        admin.dispose()
                    config["approved_suppliers"] = sorted(suppliers)
                    target = Path(args.config)
                    temp = target.with_suffix(".tmp")
                    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2))
                    temp.replace(target)
                    registry = [
                        {
                            "connection_id": str(identities(feed_id, supplier)["connection"]),
                            "env_prefix": config["env_prefix"],
                            "adapter": "goods_stock",
                            "supplier_company_id": supplier,
                        }
                        for supplier in suppliers
                    ]
                    (state / "registry-additions.json").write_text(json.dumps(registry, indent=2))
                    print(json.dumps({"provisioned_suppliers": len(suppliers)}))
                    return
                runtime = engine_for(os.environ["WAREHOUSE_DATABASE_URL"])
                try:
                    require_runtime_role(runtime)
                    result = await publish_feed(
                        runtime,
                        feed_id,
                        config["approved_suppliers"],
                        grouped,
                        observed,
                        start,
                        end,
                    )
                finally:
                    runtime.dispose()
                status = result | {
                    "ok": True,
                    "observed_at": observed.isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                    "retry_at": time.time() + args.interval,
                }
                failures = 0
            except SourceError as error:
                failures += 1
                status = {
                    "ok": False,
                    "code": error.code,
                    "retry_at": time.time()
                    + max(
                        error.retry_after_seconds or 0,
                        min(3600, args.interval * 2 ** min(failures, 5)),
                    ),
                }
                temporary = status_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(status))
                temporary.replace(status_path)
                print(json.dumps(status), flush=True)
                if args.command != "worker" or not error.retryable or failures >= 5:
                    raise
                continue
            temporary = status_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(status))
            temporary.replace(status_path)
            print(json.dumps(status), flush=True)
            if args.command != "worker":
                return


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "provision", "sync", "worker"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--interval", type=int, default=180)
    args = parser.parse_args()
    if not 60 <= args.interval <= 86400:
        parser.error("interval must be 60..86400")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
