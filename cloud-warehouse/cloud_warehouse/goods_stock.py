"""Read-only, bounded goods-stock feeds with explicit platform and supplier identities."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text

from .concurrency import in_worker_thread
from .integrations import CatalogBatch, SourceError, fingerprint
from .persistence import transaction
from .pricing import PriceSchedule, SourcePrices, UnitPrices


class StockRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int = Field(gt=0)
    goods_id: int = Field(gt=0)
    company_id: int = Field(gt=0)
    put_company_id: int = Field(gt=0)
    group_no: str = Field(min_length=1)
    goods_name: str = Field(min_length=1)
    start_date: date
    end_date: date
    days: int = Field(gt=0)
    stock: int = Field(ge=0)
    status: str
    adult_amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    child_amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    adult_settlement_price: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    child_settlement_price: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    trip_file: str = ""
    goods: dict | None = None
    company: dict


class StockSelection(BaseModel):
    """Operator-selected identities, never a keyword filter that may widen later."""

    model_config = ConfigDict(extra="forbid", strict=True)
    supplier_company_id: int = Field(gt=0, strict=True)
    goods_id: int = Field(gt=0, strict=True)
    departure_ids: list[int] = Field(min_length=1, max_length=400)


def select_rows(rows: list[StockRow], selection: list[dict] | None):
    """Missing selected departures expire normally; identity changes fail closed."""
    if selection is None:
        return rows
    if not isinstance(selection, list) or not 1 <= len(selection) <= 100:
        raise SourceError("INVALID_CATALOG_SELECTION", retryable=False)
    try:
        items = [StockSelection.model_validate(item) for item in selection]
    except ValidationError:
        raise SourceError("INVALID_CATALOG_SELECTION", retryable=False) from None
    expected, products = {}, set()
    for item in items:
        key = (item.supplier_company_id, item.goods_id)
        if key in products:
            raise SourceError("INVALID_CATALOG_SELECTION", retryable=False)
        products.add(key)
        for identifier in item.departure_ids:
            if type(identifier) is not int or identifier < 1 or identifier in expected:
                raise SourceError("INVALID_CATALOG_SELECTION", retryable=False)
            expected[identifier] = key
    if len(expected) > 400:
        raise SourceError("INVALID_CATALOG_SELECTION", retryable=False)
    result = []
    for row in rows:
        if row.id not in expected:
            continue
        if expected[row.id] != (row.company_id, row.goods_id):
            raise SourceError("SELECTED_SOURCE_IDENTITY_CHANGED", retryable=False)
        result.append(row)
    return result


def settings(base_url: str, query_company: int, start: date, end: date):
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/", "/api", "/api/"}
        or query_company < 1
        or end < start
        or (end - start).days > 366
    ):
        raise ValueError("Invalid goods-stock source settings")


async def scan(client, base_url, query_company, start, end, *, max_pages=200, guard=None):
    """Fail closed on partial, duplicated or shifting pages; never infer missing rows."""
    settings(base_url, query_company, start, end)
    observed = datetime.now(UTC)
    params = {
        "company_id": query_company,
        "start_date": start.isoformat(),
        "start_date_end": end.isoformat(),
        "limit": 100,
    }

    async def page(number):
        try:
            if guard is not None:
                await guard()
            response = await client.post(
                base_url.rstrip("/") + "/goods_stock/index", params=params | {"page": number}
            )
            if response.status_code == 429:
                delay = 300
                header = response.headers.get("Retry-After", "")
                with suppress(ValueError, TypeError, OverflowError):
                    delay = max(
                        delay,
                        int(header)
                        if header.isdigit()
                        else int(
                            (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
                        )
                        + 1,
                    )
                if guard is not None:
                    await guard(delay)
                raise SourceError("SOURCE_RATE_LIMITED", retry_after_seconds=delay)
            response.raise_for_status()
            result = response.json()
            if type(result.get("code")) is not int or result["code"] != 1:
                raise SourceError("SOURCE_REQUEST_REFUSED", retryable=False)
            data = result["data"]
            if (
                type(data.get("total")) is not int
                or data["total"] < 0
                or not isinstance(data.get("rows"), list)
            ):
                raise ValueError
            return data
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise SourceError("INVALID_SOURCE_RESPONSE") from None

    first = await page(1)
    total = first["total"]
    pages = max(1, (total + 99) // 100)
    if pages > max_pages:
        raise SourceError("PAGINATION_LIMIT_OR_INVALID")
    raw = list(first["rows"])
    if len(raw) != min(100, total):
        raise SourceError("INCOMPLETE_SOURCE_SCAN")
    for number in range(2, pages + 1):
        current = await page(number)
        if current["total"] != total:
            raise SourceError("SOURCE_CHANGED_DURING_SCAN")
        if len(current["rows"]) != min(100, total - (number - 1) * 100):
            raise SourceError("INCOMPLETE_SOURCE_SCAN")
        raw.extend(current["rows"])
    if pages > 1:
        anchor = await page(1)
        if anchor["total"] != total or fingerprint(anchor["rows"]) != fingerprint(first["rows"]):
            raise SourceError("SOURCE_CHANGED_DURING_SCAN")
    try:
        rows = [StockRow.model_validate(row) for row in raw]
    except ValidationError:
        raise SourceError("INVALID_SOURCE_DATA") from None
    if len({r.id for r in rows}) != total:
        raise SourceError("DUPLICATE_SOURCE_ID")
    if any(
        r.put_company_id != query_company
        or not start <= r.start_date <= end
        or r.end_date < r.start_date
        or r.status != "normal"
        for r in rows
    ):
        raise SourceError("SOURCE_SCOPE_MISMATCH")
    if [(r.start_date, r.id) for r in rows] != sorted((r.start_date, r.id) for r in rows):
        raise SourceError("SOURCE_ORDER_MISMATCH")
    return rows, observed


def batches(rows, observed, start, end):
    """One platform scan becomes separate supplier-owned catalog batches."""
    suppliers, routes, departures = {}, {}, {}
    for row in rows:
        supplier = row.company_id
        name = row.company.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SourceError("SUPPLIER_NAME_MISSING")
        identity = {"name": name.strip(), "short_name": row.company.get("short_name")}
        if supplier in suppliers and suppliers[supplier] != identity:
            raise SourceError("SUPPLIER_IDENTITY_CONFLICT")
        suppliers[supplier] = identity
        goods = row.goods or {}
        route = {
            "routeId": row.goods_id,
            "routeCode": str(row.goods_id),
            "routeName": goods.get("goods_name") or row.goods_name,
            "days": goods.get("days") or row.days,
            "departCityName": str(goods["departure_city"]) if goods.get("departure_city") else None,
            # Retain unverified relative paths, but do not enqueue a guessed public URL.
            "goodsStockAttachment": goods.get("trip_file") or "",
            "goodsStockPoster": goods.get("poster_image") or "",
            "queryCompanyId": row.put_company_id,
            "supplierCompanyId": supplier,
            "testCatalog": True,
        }
        parents = routes.setdefault(supplier, {})
        if row.goods_id in parents and parents[row.goods_id] != route:
            raise SourceError("PRODUCT_FIELDS_CONFLICT")
        parents[row.goods_id] = route
        departures.setdefault(supplier, []).append(
            {
                "periodId": row.id,
                "routeId": row.goods_id,
                "periodCode": row.group_no,
                "departDate": row.start_date.isoformat(),
                "returnDate": row.end_date.isoformat(),
                "companyId": supplier,
                "availableSeats": row.stock,
                "queryCompanyId": row.put_company_id,
                "goodsStockAttachment": row.trip_file or goods.get("trip_file") or "",
                "referencePrices": {
                    "currency": "CNY",
                    "market_adult": str(row.adult_amount) if row.adult_amount is not None else None,
                    "market_child": str(row.child_amount) if row.child_amount is not None else None,
                    "settlement_adult": str(row.adult_settlement_price)
                    if row.adult_settlement_price is not None
                    else None,
                    "settlement_child": str(row.child_settlement_price)
                    if row.child_settlement_price is not None
                    else None,
                },
            }
        )
    result = {}
    for supplier in suppliers:
        batch = CatalogBatch(
            list(routes[supplier].values()), departures[supplier], start, end, observed
        )
        batch.validate()
        result[supplier] = batch
    return suppliers, result


@in_worker_thread
def reference_price(scope, departure_id, supplier_company, query_company, start, end):
    with transaction(scope.engine, scope.actor) as conn:
        row = (
            conn.execute(
                text("""SELECT d.source,d.observed_at,d.availability_expires_at,d.available_seats
                FROM departure d JOIN supplier_connection c ON c.id=d.connection_id
                WHERE d.connection_id=:connection AND d.external_id=:departure
                  AND d.company_id=:supplier AND d.status='published' AND c.active
                  AND c.connector_type='goods_stock'
                  AND d.depart_date BETWEEN :start AND :end
                  AND d.source->>'queryCompanyId'=:query
                  AND d.availability_expires_at>now()"""),
                {
                    "connection": scope.connection_id,
                    "departure": str(departure_id),
                    "supplier": str(supplier_company),
                    "query": str(query_company),
                    "start": start,
                    "end": end,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise SourceError("SOURCE_REFERENCE_PRICE_UNAVAILABLE", retryable=False)
        price = row["source"]["referencePrices"]

        def amount(key):
            value = Decimal(price[key]) if price.get(key) is not None else None
            return value or None

        return SourcePrices(
            schedule=PriceSchedule(
                currency="CNY",
                market=UnitPrices(adult=amount("market_adult"), child=amount("market_child")),
                settlement=UnitPrices(
                    adult=amount("settlement_adult"), child=amount("settlement_child")
                ),
                fees_complete=False,
            ),
            observed_at=row["observed_at"],
            expires_at=row["availability_expires_at"],
            source_ref=f"goods_stock reference snapshot; query_company={query_company}; supplier={supplier_company}",
            available_seats=row["available_seats"],
        )


class GoodsStockConnector:
    """Only list reads; no booking, hold, order, customer mapping or settlement writes."""

    def __init__(self, base_url, query_company, supplier_company, start, end):
        settings(base_url, query_company, start, end)
        self.query_company, self.supplier_company = query_company, supplier_company
        self.start, self.end = start, end

    async def read_uniform_prices(self, departure_id, company_id, policy_version):
        from .source_limits import current_scope

        if str(company_id) != str(self.supplier_company) or policy_version != 1:
            raise SourceError("SOURCE_SCOPE_MISMATCH", retryable=False)
        return await reference_price(
            current_scope(),
            departure_id,
            self.supplier_company,
            self.query_company,
            self.start,
            self.end,
        )


@asynccontextmanager
async def connect(base_url, query_company, supplier_company, start, end):
    yield GoodsStockConnector(base_url, query_company, supplier_company, start, end)
