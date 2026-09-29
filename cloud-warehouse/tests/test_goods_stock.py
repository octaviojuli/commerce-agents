import copy
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from cloud_warehouse import catalog, quotes
from cloud_warehouse.goods_stock import (
    GoodsStockConnector,
    StockRow,
    batches,
    connect,
    scan,
    select_rows,
)
from cloud_warehouse.goods_stock_sync import identities, provision, publish_feed
from cloud_warehouse.integrations import SourceError
from cloud_warehouse.persistence import Principal, transaction
from cloud_warehouse.source_limits import open_connector

START, END = date(2030, 10, 1), date(2030, 10, 7)


def test_explicit_selection_does_not_adopt_new_routes_or_departures():
    rows = [
        StockRow.model_validate(row(1)),
        StockRow.model_validate(row(2)),
        StockRow.model_validate(row(3, 20)),
    ]
    selected = [{"supplier_company_id": 10, "goods_id": 100, "departure_ids": [1, 99]}]
    assert [r.id for r in select_rows(rows, selected)] == [1]
    assert select_rows([], selected) == []
    changed = rows[0].model_copy(update={"company_id": 20})
    with pytest.raises(SourceError, match="SELECTED_SOURCE_IDENTITY_CHANGED"):
        select_rows([changed], selected)


@pytest.mark.parametrize(
    "selection",
    [
        [],
        {},
        [{"supplier_company_id": 10, "goods_id": 100, "departure_ids": [1, 1]}],
        [{"supplier_company_id": 10, "goods_id": 100, "departure_ids": [0]}],
    ],
)
def test_empty_or_duplicate_selection_never_means_full_catalog(selection):
    with pytest.raises(SourceError, match="INVALID_CATALOG_SELECTION"):
        select_rows([StockRow.model_validate(row())], selection)


def row(identifier=1, supplier=10):
    return {
        "id": identifier,
        "goods_id": 100,
        "company_id": supplier,
        "put_company_id": 2,
        "group_no": f"ACME-{identifier}",
        "goods_name": "ACME Spanish tour",
        "start_date": "2030-10-01",
        "end_date": "2030-10-04",
        "days": 4,
        "stock": 8,
        "reserve_stock": 3,
        "used_stock": 5,
        "status": "normal",
        "adult_amount": "1500.00",
        "adult_settlement_price": "1200.00",
        "child_amount": "900.00",
        "child_settlement_price": "700.00",
        "goods": {"goods_name": "ACME Spanish tour", "days": 4, "trip_file": "/uploads/acme.pdf"},
        "company": {"name": f"ACME Supplier {supplier}", "short_name": f"ACME {supplier}"},
        "admin": {"mobile": "PRIVATE-CONTACT"},
        "paid_supplier": "PRIVATE-MONEY",
    }


def transport(rows, mutate=None):
    def handle(request):
        assert request.method == "POST"
        assert request.url.path == "/goods_stock/index"
        assert request.url.params["company_id"] == "2"
        assert request.url.params["limit"] == "100"
        assert request.content == b""
        page = int(request.url.params["page"])
        data = {"total": len(rows), "rows": copy.deepcopy(rows[(page - 1) * 100 : page * 100])}
        if mutate:
            mutate(page, data)
        return httpx.Response(200, json={"code": 1, "data": data})

    return httpx.MockTransport(handle)


async def test_complete_pages_stock_currency_and_private_field_projection():
    source = [row(i, 10 if i <= 100 else 20) for i in range(1, 102)]
    async with httpx.AsyncClient(transport=transport(source)) as client:
        rows, observed = await scan(client, "https://acme.example", 2, START, END)
    suppliers, grouped = batches(rows, observed, START, END)
    assert set(suppliers) == {10, 20}
    assert len(grouped[10].departures) == 100
    assert len(grouped[20].departures) == 1
    assert grouped[10].departures[0]["availableSeats"] == 8
    assert grouped[10].departures[0]["referencePrices"]["currency"] == "CNY"
    assert "PRIVATE" not in str(grouped)
    assert "routeAttachmentUrl" not in grouped[10].routes[0]


@pytest.mark.parametrize(
    "fault", ["count", "short", "duplicate", "scope", "date", "status", "stock", "order", "anchor"]
)
async def test_invalid_or_changing_scan_is_not_publishable(fault):
    source = [row(i) for i in range(1, 102)]
    calls = 0

    def mutate(page, data):
        nonlocal calls
        calls += 1
        if fault == "count" and page == 2:
            data["total"] += 1
        if fault == "short" and page == 2:
            data["rows"] = []
        if fault == "duplicate" and page == 2:
            data["rows"][0]["id"] = 1
        if fault == "scope":
            data["rows"][0]["put_company_id"] = 3
        if fault == "date":
            data["rows"][0]["start_date"] = "2030-11-01"
        if fault == "status":
            data["rows"][0]["status"] = "cancel"
        if fault == "stock":
            data["rows"][0]["stock"] = -1
        if fault == "order" and page == 1:
            data["rows"].reverse()
        if fault == "anchor" and calls == 3:
            data["rows"][0]["stock"] = 99

    async with httpx.AsyncClient(transport=transport(source, mutate)) as client:
        with pytest.raises(SourceError):
            await scan(client, "https://acme.example", 2, START, END)


async def test_reference_prices_require_server_scope():
    connector = GoodsStockConnector("https://acme.example", 2, 10, START, END)
    with pytest.raises(SourceError, match="SOURCE_LIMIT_CONTEXT_MISSING"):
        await connector.read_uniform_prices("1", "10", 1)
    with pytest.raises(SourceError, match="SOURCE_SCOPE_MISMATCH"):
        await connector.read_uniform_prices("1", "20", 1)


def test_same_product_fields_cannot_arbitrarily_use_first_departure():
    source = [row(1), row(2)]
    source[1]["goods"]["days"] = 9
    with pytest.raises(SourceError, match="PRODUCT_FIELDS_CONFLICT"):
        batches([StockRow.model_validate(r) for r in source], datetime.now(UTC), START, END)


async def test_provision_publish_rls_idempotence_and_missing_supplier_expiry(database, tenant):
    admin, runtime = database
    feed = uuid4()
    observed = datetime.now(UTC)
    suppliers, grouped = batches(
        [StockRow.model_validate(row(1)), StockRow.model_validate(row(2, 20))], observed, START, END
    )
    provision(admin, feed, 2, suppliers)
    provision(admin, feed, 2, suppliers)
    report = await publish_feed(runtime, feed, [10, 20], grouped, observed, START, END)
    assert report == {"suppliers": 2, "products": 2, "departures": 2}
    first, second = identities(feed, 10), identities(feed, 20)
    with admin.begin() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM warehouse_user WHERE id IN (:a,:b) AND password_hash IS NOT NULL"
                ),
                {"a": first["user"], "b": second["user"]},
            )
            == 0
        )
        conn.execute(
            text(
                "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id) VALUES(:id,:supplier,:buyer,:source)"
            ),
            {
                "id": uuid4(),
                "supplier": first["org"],
                "buyer": tenant.buyer.organization_id,
                "source": first["connection"],
            },
        )
    visible = catalog.list_departures(runtime, tenant.buyer)
    assert len(visible) == 1 and visible[0]["supplier_org_id"] == first["org"]

    def factory():
        return connect("https://acme.example", 2, 10, START, END)

    async with open_connector(runtime, tenant.buyer, first["connection"], factory) as adapter:
        price = await adapter.read_uniform_prices("1", "10", 1)
        assert price.schedule.currency == "CNY" and price.available_seats == 8
        assert str(price.schedule.settlement.adult) == "1200.00"
        assert quotes.calculate(price.schedule, quotes.Party(adults=2))["complete"] is False
        with pytest.raises(SourceError, match="SOURCE_REFERENCE_PRICE_UNAVAILABLE"):
            await adapter.read_uniform_prices("2", "10", 1)
    await publish_feed(runtime, feed, [10, 20], grouped, observed, START, END)
    with transaction(runtime, Principal(first["user"], first["org"])) as conn:
        assert conn.scalar(text("SELECT count(*) FROM departure")) == 1
        assert conn.scalar(text("SELECT version FROM departure")) == 1
        assert conn.scalar(text("SELECT count(*) FROM inventory_pool")) == 0
        assert conn.scalar(text("SELECT count(*) FROM product_search")) == 1
    await publish_feed(runtime, feed, [10, 20], {}, observed - timedelta(seconds=1), START, END)
    with transaction(runtime, Principal(first["user"], first["org"])) as conn:
        assert conn.scalar(text("SELECT availability_expires_at<now() FROM departure"))
    async with open_connector(runtime, tenant.buyer, first["connection"], factory) as adapter:
        with pytest.raises(SourceError, match="SOURCE_REFERENCE_PRICE_UNAVAILABLE"):
            await adapter.read_uniform_prices("1", "10", 1)
    with pytest.raises(SourceError, match="SUPPLIER_PROVISIONING_REQUIRED"):
        await publish_feed(runtime, feed, [10], grouped, observed, START, END)
