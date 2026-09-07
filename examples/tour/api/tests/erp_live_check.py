# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""A read-only smoke check of ``HttpErpClient`` against a real ERP, run by hand:

    python examples/tour/api/tests/erp_live_check.py

It reads ``examples/tour/.env`` itself, logs in, and calls every read once — routes, their
departures, one departure's detail with its 市场价, a 同业价 for ``TOUR_ERP_CUSTOMER_ID``, and
the salesperson's orders. Because the reads span every department the account is authorised
for while a quote is department-bound, it reports the department the login landed in, how many
the account has, and whether the quote it obtained needed a switch into another one. It never
calls ``POST /order/create``, and it prints counts and one trimmed sample per call: no
credential, no token, and no customer's name reaches the output. Not a test: pytest does not
collect this file, because nothing here runs without an ERP."""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXAMPLE.parent))

from tour.api.erp_client import DepartureRecord, ErpError, RouteQuery  # noqa: E402
from tour.api.http_erp import HttpErpClient  # noqa: E402

PAST_WINDOW = 365
FUTURE_WINDOW = 180
MAX_ROUTES = 8
MAX_QUOTES = 15


def read_env(path: Path) -> dict[str, str]:
    """``KEY=VALUE`` lines, ``#`` comments and blanks skipped, surrounding quotes stripped."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def trim(text: str, keep: int = 10) -> str:
    """A sample field, shortened: the check reports shape, not the agency's catalog."""
    return text if len(text) <= keep else text[:keep] + "…"


async def quoted(client: HttpErpClient, rows: list[DepartureRecord], customer_id: int) -> bool:
    """The 同业价 of the first departure the ERP has one for, its 市场价 beside it, reporting
    what the others said: a departure with no price row of its own is a gap in the catalog, not
    a broken call. Departures outside the login department come first, because a quote there is
    what proves the switch: the same call under the login token answers a bare nginx 404."""
    login_company = int(client.user_info.get("companyId") or 0)
    ordered = sorted(rows[:MAX_QUOTES], key=lambda row: row.company_id == login_company)
    skipped: list[str] = []
    for row in ordered:
        try:
            quote = await client.quote(row.period_id, customer_id, row.company_id)
        except ErpError as exc:
            skipped.append(f"{row.period_id}:{type(exc).__name__}")
            continue
        detail = await client.get_departure(row.period_id)
        market = detail.price if detail and detail.price else None
        switched = "yes" if row.company_id != login_company else "no"
        print(
            f"quote: period={row.period_id} company={row.company_id} switched={switched} "
            f"skipped={len(skipped)} type={quote.price_type} external={quote.is_external}"
        )
        print(
            f"  同业价 adult={quote.price.adult} child={quote.price.child} "
            f"single_room_diff={quote.price.single_room_diff}"
        )
        print(
            f"  市场价 adult={market.adult if market else '-'} "
            f"child={market.child if market else '-'}"
        )
        if skipped:
            print(f"  no price on: {' '.join(skipped)}")
        if switched == "no":
            print("  every priced departure is in the login department; no switch was exercised")
        return True
    print(
        f"quote: no departure priced for this customer; tried {len(skipped)}: {' '.join(skipped)}"
    )
    return False


async def main() -> int:
    env = read_env(EXAMPLE / ".env")
    allow_past = env.get("TOUR_ERP_ALLOW_PAST", "") in ("1", "true", "TRUE")
    today = date.today()
    start = today - timedelta(days=PAST_WINDOW) if allow_past else today
    window = (start, today + timedelta(days=FUTURE_WINDOW))
    client = HttpErpClient(
        env["TOUR_ERP_BASE_URL"], env["TOUR_ERP_MOBILE"], env["TOUR_ERP_PASSWORD"]
    )
    customer_id = int(env["TOUR_ERP_CUSTOMER_ID"])
    print(f"base_url={env['TOUR_ERP_BASE_URL']}")
    print(f"window={window[0]}..{window[1]} allow_past={allow_past}")

    routes = await client.search_routes(RouteQuery(depart_from=window[0], depart_to=window[1]))
    user = client.user_info
    print(
        f"login: userId={user.get('userId')} department={user.get('companyId')} "
        f"{user.get('companyName')} token=<redacted>"
    )
    print(f"departments: {len(client.companies)} authorised for this account")
    print(f"search_routes: {len(routes)} routes")
    if not routes:
        print("no routes visible to this department; nothing further to check")
        return 1
    route = routes[0]
    print(
        f"  route: id={route.route_id} code={trim(route.route_code)} days={route.days} "
        f"name={trim(route.route_name)} from_price={route.from_price} tags={len(route.tags)}"
    )

    rows: list[DepartureRecord] = []
    for one in routes[:MAX_ROUTES]:
        rows.extend(await client.list_departures(one.route_id, one.route_name, *window))
    departments = sorted({row.company_id for row in rows})
    print(f"list_departures: {len(rows)} departures across {min(len(routes), MAX_ROUTES)} routes")
    print(f"  departments on those departures: {departments}")
    if not rows:
        print("no departures in the window; skipping detail and price")
        return 1
    row = rows[0]
    print(
        f"  departure: id={row.period_id} route={row.route_id} code={trim(row.period_code, 16)} "
        f"depart={row.depart_date} seats={row.available_seats}/{row.plan_guests} "
        f"confirmed={row.confirm_count}/{row.min_group_size} reserve_hours={row.reserve_hours}"
    )

    detail = await client.get_departure(row.period_id)
    price = detail.price if detail and detail.price else None
    print(
        f"get_departure: {'found' if detail else 'none'} "
        f"price={price.adult if price else '-'}/{price.child if price else '-'} "
        f"reserve={detail.reserve_count if detail else '-'} "
        f"waitlist={detail.waitlist_count if detail else '-'}"
    )
    switched = await quoted(client, rows, customer_id)

    orders = await client.list_orders()
    print(f"list_orders: {len(orders)} orders")
    if orders:
        order = orders[0]
        print(
            f"  order: id={order.order_id} status={order.status}/{order.status_text} "
            f"total={order.total_amount} period={order.period_id}"
        )
    return 0 if switched else 1


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 - the check reports and fails, never traces
        print(f"FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
    print("OK" if code == 0 else "INCOMPLETE")
    sys.exit(code)
