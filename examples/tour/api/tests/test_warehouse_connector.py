import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from cloud_warehouse.integrations import SourceError
from tour.api.http_erp import HttpErpClient
from tour.api.warehouse_connector import TourWarehouseConnector

BASE = "https://b2b.acme.example/aicli"


def test_api_and_scoped_worker_use_the_same_secret_binding(tmp_path, monkeypatch):
    from tour.api import warehouse_app, warehouse_connector

    first, second = uuid4(), uuid4()
    config = tmp_path / "connections.json"
    config.write_text(
        json.dumps(
            [
                {"connection_id": str(first), "env_prefix": "SUPPLIER_A"},
                {"connection_id": str(second), "env_prefix": "SUPPLIER_B"},
            ]
        )
    )
    secrets = {
        f"{prefix}_{key}": f"ACME-{prefix}-{key}"
        for prefix in ("SUPPLIER_A", "SUPPLIER_B", "TOUR_ERP")
        for key in ("BASE_URL", "MOBILE", "PASSWORD")
    }
    monkeypatch.setattr(warehouse_connector, "connect", lambda settings: dict(settings))
    registry = warehouse_app.connector_registry(str(config), secrets)
    assert registry[first]()["TOUR_ERP_PASSWORD"] == "ACME-SUPPLIER_A-PASSWORD"
    assert registry[second]()["TOUR_ERP_PASSWORD"] == "ACME-SUPPLIER_B-PASSWORD"
    # A worker does not receive or require another supplier's secrets.
    only_second = {key: value for key, value in secrets.items() if not key.startswith("SUPPLIER_A")}
    scoped = warehouse_connector.connector_registry(str(config), only_second, connection_id=second)
    assert set(scoped) == {second} and scoped[second]() == registry[second]()
    with pytest.raises(ValueError, match="供应商连接绑定无效"):
        warehouse_app.connector_registry(str(config), only_second)


@pytest.mark.parametrize("fault", ["duplicate", "absent", "blank", "missing", "malformed", "extra"])
def test_binding_errors_never_fall_back_or_expose_configuration(tmp_path, fault):
    from tour.api.warehouse_connector import connector_registry

    identifier = uuid4()
    rows = [{"connection_id": str(identifier), "env_prefix": "SUPPLIER_A"}]
    environment = {
        f"{prefix}_{key}": "ACME-private-value"
        for prefix in ("SUPPLIER_A", "TOUR_ERP")
        for key in ("BASE_URL", "MOBILE", "PASSWORD")
    }
    if fault == "duplicate":
        rows *= 2
    elif fault == "absent":
        identifier = uuid4()
    elif fault == "blank":
        environment["SUPPLIER_A_PASSWORD"] = " "
    elif fault == "missing":
        del environment["SUPPLIER_A_PASSWORD"]
    elif fault == "extra":
        rows[0]["password"] = "ACME-private-value"
    config = tmp_path / "connections.json"
    config.write_text("ACME-private-value" if fault == "malformed" else json.dumps(rows))
    with pytest.raises(ValueError, match="供应商连接绑定无效") as caught:
        connector_registry(str(config), environment, connection_id=identifier)
    assert "ACME-private" not in str(caught.value)


def response(data):
    return httpx.Response(200, json={"code": 200, "message": "ok", "data": data})


async def test_goods_stock_registry_is_explicit_and_does_not_require_erp_credentials(tmp_path):
    from tour.api.warehouse_connector import connector_registry

    identifier = uuid4()
    path = tmp_path / "connections.json"
    row = {
        "connection_id": str(identifier),
        "env_prefix": "ACME",
        "adapter": "goods_stock",
        "supplier_company_id": 10,
    }
    path.write_text(json.dumps([row]))
    environment = {
        "ACME_BASE_URL": "https://acme.example",
        "ACME_QUERY_COMPANY_ID": "2",
        "ACME_START_DATE": "2030-10-01",
        "ACME_END_DATE": "2030-10-07",
    }
    registry = connector_registry(str(path), environment)
    async with registry[identifier]() as adapter:
        assert adapter.query_company == 2 and adapter.supplier_company == 10
        assert not hasattr(adapter, "create_order")
    del row["supplier_company_id"]
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match="供应商连接绑定无效"):
        connector_registry(str(path), environment)


def client(handler):
    return HttpErpClient.with_token(
        BASE,
        "ACME-test-token",
        datetime.now(UTC).timestamp() + 3600,
        {"companyId": 2},
        [{"companyId": 2}, {"companyId": 3}],
        transport=httpx.MockTransport(handler),
    )


async def test_all_customer_departments_are_checked_without_erp_order_writes():
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/switch-company"):
            assert json.loads(request.content)["companyId"] == 3
            return response({"token": "ACME-switched", "expiresIn": 3600})
        assert request.method == "GET" and request.url.path.endswith("/customer/list")
        rows = [
            {"customerId": 4101, "csCode": "ACME-C1", "companyName": "ACME buyer", "companyType": 1}
        ]
        return response({"list": rows, "total": 1, "totalPages": 1})

    upstream = client(handle)
    try:
        identity = await TourWarehouseConnector(upstream).resolve_customer("ACME-C1")
        assert identity.customer_id == "4101"
        assert sum(path.endswith("/customer/list") for _, path in calls) == 2
        assert all(path.endswith("/switch-company") for method, path in calls if method == "POST")
    finally:
        await upstream.aclose()


async def test_duplicate_customer_codes_across_departments_fail_closed():
    def handle(request):
        if request.url.path.endswith("/switch-company"):
            return response({"token": "ACME-switched", "expiresIn": 3600})
        identity = 4102 if "ACME-switched" in request.headers["Authorization"] else 4101
        return response(
            {
                "list": [
                    {
                        "customerId": identity,
                        "csCode": "ACME-C1",
                        "companyName": "ACME buyer",
                        "companyType": 1,
                    }
                ],
                "total": 1,
                "totalPages": 1,
            }
        )

    upstream = client(handle)
    try:
        with pytest.raises(SourceError, match="CUSTOMER_CODE_NOT_UNIQUE"):
            await TourWarehouseConnector(upstream).resolve_customer("ACME-C1")
    finally:
        await upstream.aclose()


async def test_market_and_settlement_prices_keep_missing_fields_unknown():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/period/detail"):
            return response(
                {
                    "periodId": 9,
                    "companyId": 2,
                    "availableSeats": 3,
                    "priceInfo": {"adultPrice": 1200, "childPrice": 0, "currency": "CNY"},
                }
            )
        assert request.url.path.endswith("/order/price")
        assert request.url.params["customerId"] == "4101"
        return response(
            {
                "priceType": "同行价",
                "priceInfo": {"adultPrice": 1100, "singleRoomDiff": 0, "currency": "CNY"},
            }
        )

    upstream = client(handle)
    try:
        price = await TourWarehouseConnector(upstream).read_prices("9", "4101", "2")
        assert price.schedule.market.child is None and price.schedule.market.single_room is None
        assert price.schedule.settlement.single_room == 0
        assert price.expires_at is None and price.schedule.fees_complete is False
        assert price.available_seats == 3 and len(calls) == 2
    finally:
        await upstream.aclose()


@pytest.mark.parametrize(
    "status,header,code,retryable,delay",
    [
        (429, "120", "SOURCE_RATE_LIMITED", True, 120),
        (429, "invalid", "SOURCE_RATE_LIMITED", True, 60),
        (429, "0", "SOURCE_RATE_LIMITED", True, 60),
        (503, "90", "SOURCE_UNAVAILABLE", True, 90),
        (403, None, "SOURCE_PERMISSION_DENIED", False, 0),
        (404, None, "SOURCE_NOT_FOUND", False, 0),
        (400, None, "SOURCE_REQUEST_REFUSED", False, 0),
        (429, "999999999999999999999", "SOURCE_COOLDOWN_REQUIRES_REVIEW", False, 0),
    ],
)
async def test_source_errors_keep_retry_policy_but_never_upstream_text(
    status, header, code, retryable, delay
):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="ACME private upstream diagnostic",
            headers={"Retry-After": header} if header else {},
        )

    upstream = client(handle)
    try:
        with pytest.raises(SourceError) as caught:
            await TourWarehouseConnector(upstream).read_catalog()
        error = caught.value
        assert error.code == code and error.retryable is retryable
        assert error.retry_after_seconds == delay
        assert "private" not in str(error)
        assert len(calls) == 1  # Never retry a throttled request within this scan.
    finally:
        await upstream.aclose()


async def test_login_rate_limit_and_http_date_reach_customer_lookup(monkeypatch):
    from email.utils import format_datetime

    from tour.api import http_erp

    now = datetime(2026, 9, 23, tzinfo=UTC)
    monkeypatch.setattr(http_erp.time, "time", lambda: now.timestamp())
    retry = format_datetime(datetime(2026, 9, 23, 0, 3, tzinfo=UTC), usegmt=True)
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path.endswith("/login")
        return httpx.Response(
            429,
            json={"code": 429, "message": "ACME secret diagnostic"},
            headers={"Retry-After": retry},
        )

    upstream = HttpErpClient.from_login(
        BASE, "ACME-mobile", "ACME-password", transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(SourceError) as caught:
            await TourWarehouseConnector(upstream).resolve_customer("ACME-C1")
        assert caught.value.code == "SOURCE_RATE_LIMITED"
        assert caught.value.retry_after_seconds == 180 and len(calls) == 1
    finally:
        await upstream.aclose()


async def test_failed_login_does_not_become_generic_sync_error():
    upstream = HttpErpClient.from_login(
        BASE,
        "ACME-mobile",
        "ACME-password",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400, json={"code": 400, "message": "ACME wrong password"}
            )
        ),
    )
    try:
        with pytest.raises(SourceError) as caught:
            await TourWarehouseConnector(upstream).read_catalog()
        assert caught.value.code == "SOURCE_AUTH_FAILED" and not caught.value.retryable
    finally:
        await upstream.aclose()


async def test_merchant_orders_complete_pagination_department_scope_and_field_allowlist():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/order/detail"):
            return response(
                {
                    "orderId": 51,
                    "orderNo": "ACME-O51",
                    "totalAmount": 100,
                    "contactMobile": "ACME-private",
                    "adultCount": 2,
                }
            )
        assert request.url.path.endswith("/order/list")
        page = int(request.url.params["pageNum"])
        return response(
            {
                "total": 51,
                "totalPages": 2,
                "pageNum": page,
                "list": [
                    {
                        "orderId": n,
                        "orderNo": f"ACME-O{n}",
                        "periodId": 9,
                        "totalAmount": 100,
                        "contactMobile": "ACME-private",
                    }
                    for n in range((page - 1) * 50 + 1, min(page * 50, 51) + 1)
                ],
            }
        )

    upstream = client(handle)
    try:
        connector = TourWarehouseConnector(upstream)
        with pytest.raises(SourceError, match="SOURCE_PERMISSION_DENIED"):
            await connector.read_orders(allowed_departments=["2"], company_id=3)
        assert not calls
        page = await connector.read_orders(
            allowed_departments=["2"], company_id=2, page=3, departure_id=9
        )
        assert page["total"] == 51 and len(page["items"]) == 11
        assert all("contactMobile" not in row for row in page["items"])
        assert any(request.url.params.get("pageNum") == "2" for request in calls)
        detail = await connector.read_order(allowed_departments=["2"], company_id=2, order_id=51)
        assert detail["order"]["adultCount"] == 2 and detail["order"]["settlementType"] is None
        assert "contactMobile" not in detail["order"]
        with pytest.raises(SourceError, match="SOURCE_NOT_FOUND"):
            await connector.read_order(allowed_departments=["2"], company_id=2, order_id=999)
    finally:
        await upstream.aclose()


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("同行价", "ready"),
        ("结算价", "ready"),
        ("市场价", "type_unconfirmed"),
        (None, "type_unconfirmed"),
    ],
)
async def test_merchant_price_observation_keeps_raw_zero_and_validates_type(kind, expected):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/period/detail"):
            return response(
                {
                    "periodId": 9,
                    "companyId": 2,
                    "availableSeats": 7,
                    "reserveCount": 2,
                    "placeholderCount": 3,
                    "priceInfo": {"adultPrice": 13999, "childPrice": 0, "currency": "CNY"},
                }
            )
        assert request.url.params["customerId"] == "4101"
        return response(
            {
                "priceType": kind,
                "priceInfo": {"adultPrice": 12700, "childPrice": 0, "currency": "CNY"},
            }
        )

    upstream = client(handle)
    try:
        connector = TourWarehouseConnector(upstream)
        market = await connector.read_departure_observation(departure_id="9", company_id="2")
        assert market["settlement_status"] == "customer_required" and len(calls) == 1
        result = await connector.read_departure_observation(
            departure_id="9", company_id="2", customer_id="4101"
        )
        assert result["settlement_status"] == expected
        assert (
            result["market_prices"]["childPrice"] == 0
            and result["market_prices"]["elderPrice"] is None
        )
        assert result["inventory"]["availableSeats"] == 7
        assert (
            result["inventory"]["reserveCount"] == 2
            and result["inventory"]["placeholderCount"] == 3
        )
        if expected == "ready":
            assert result["settlement_prices"]["adultPrice"] == 12700
            assert result["settlement_prices"]["childPrice"] == 0
        else:
            assert result["settlement_prices"] is None
            with pytest.raises(SourceError, match="SOURCE_PRICE_TYPE_UNCONFIRMED"):
                await connector.read_prices("9", "4101", "2")
    finally:
        await upstream.aclose()


async def test_failed_settlement_read_preserves_successful_market_and_inventory():
    def handle(request):
        if request.url.path.endswith("/period/detail"):
            return response(
                {
                    "periodId": 9,
                    "companyId": 2,
                    "availableSeats": 7,
                    "priceInfo": {"adultPrice": 13999, "currency": "CNY"},
                }
            )
        return httpx.Response(503, text="private upstream diagnostic")

    upstream = client(handle)
    try:
        result = await TourWarehouseConnector(upstream).read_departure_observation(
            departure_id="9", company_id="2", customer_id="4101"
        )
        assert result["settlement_status"] == "source_error" and result["settlement_prices"] is None
        assert (
            result["inventory"]["availableSeats"] == 7
            and result["market_prices"]["adultPrice"] == 13999
        )
        assert "private" not in str(result)
    finally:
        await upstream.aclose()


async def test_uniform_prices_only_use_operator_bound_identity_and_policy_version():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/period/detail"):
            return response(
                {
                    "periodId": 9,
                    "companyId": 2,
                    "availableSeats": 3,
                    "priceInfo": {"adultPrice": 1200, "currency": "CNY"},
                }
            )
        assert request.url.params["customerId"] == "4101"
        return response(
            {
                "priceType": "结算价",
                "priceInfo": {"adultPrice": 1100, "childPrice": 0, "currency": "CNY"},
            }
        )

    upstream = client(handle)
    connector = TourWarehouseConnector(
        upstream, standard_customer_id="4101", standard_price_version="3"
    )
    try:
        with pytest.raises(SourceError, match="UNIFORM_PRICE_CONFIGURATION_MISSING"):
            await connector.read_uniform_prices("9", "2", 2)
        assert calls == []
        prices = await connector.read_uniform_prices("9", "2", 3)
        assert prices.schedule.settlement.adult == 1100 and prices.schedule.settlement.child is None
        observation = await connector.read_departure_observation(
            departure_id="9", company_id="2", customer_id="9999", uniform_price_version=3
        )
        assert (
            observation["price_scope"] == "uniform" and observation["settlement_status"] == "ready"
        )
        assert "customer_id" not in observation and "price_customer" not in observation
    finally:
        await upstream.aclose()
