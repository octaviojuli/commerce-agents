"""Read current configured B2B customer and price contracts without publishing mappings."""

import asyncio
import json
import runpy
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples"))
audit_connector = runpy.run_path(str(Path(__file__).with_name("local_warehouse.py")))[
    "audit_connector"
]


async def main():
    config = dict(dotenv_values(ROOT / "examples/tour/.env"))
    if not config.get("TOUR_ERP_CUSTOMER_CODE"):
        raise SystemExit("No configured B2B customer code; no mapping was inferred")
    source = json.loads((ROOT / ".warehouse/source-audit.json").read_text())
    candidates = [
        row for row in source["departures"] if row.get("routeId") and row.get("companyId")
    ]
    candidates.sort(key=lambda row: row["departDate"], reverse=True)
    async with audit_connector() as connector:
        identity = await connector.resolve_customer(config["TOUR_ERP_CUSTOMER_CODE"])
        row = candidates[0]
        price = await connector.read_prices(
            str(row["periodId"]), identity.customer_id, str(row["companyId"])
        )
    print(
        json.dumps(
            {
                "customer_uniquely_verified": True,
                "market_adult_present": price.schedule.market.adult is not None,
                "settlement_adult_present": price.schedule.settlement.adult is not None,
                "currency": price.schedule.currency,
                "inventory_known": price.available_seats is not None,
                "upstream_price_expiry_provided": price.expires_at is not None,
                "full_fee_terms_verified": price.schedule.fees_complete,
                "orders_created": 0,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
