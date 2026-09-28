"""Read-only source quality audit. Print counts and field names, never raw records."""

import asyncio
import json
import os
import runpy
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from cloud_warehouse.integrations import DepartureRow, RouteRow

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples"))
audit_connector = runpy.run_path(str(Path(__file__).with_name("local_warehouse.py")))[
    "audit_connector"
]


async def main():
    os.umask(0o077)
    async with audit_connector() as connector:
        routes = await connector._pages("/route/list", {}, "routeId")
        departures = await connector._pages("/period/list", {}, "periodId")
    errors = Counter()
    routes_ids = {row["routeId"] for row in routes}
    orphaned = 0
    for kind, rows, model in (("route", routes, RouteRow), ("departure", departures, DepartureRow)):
        for row in rows:
            try:
                model.model_validate(row)
            except ValidationError as error:
                for item in error.errors(include_input=False):
                    errors[kind + ":" + ".".join(map(str, item["loc"])) + ":" + item["type"]] += 1
            if kind == "departure" and row.get("routeId") not in routes_ids:
                orphaned += 1
    # Local diagnostic snapshot stays ignored; lets validation be improved without repeated API reads.
    (ROOT / ".warehouse/source-audit.json").write_text(
        json.dumps({"routes": routes, "departures": departures}, ensure_ascii=False)
    )
    print(
        json.dumps(
            {
                "routes": len(routes),
                "departures": len(departures),
                "errors": errors,
                "orphan_departures": orphaned,
                "departure_fields": sorted({k for row in departures for k in row}),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
