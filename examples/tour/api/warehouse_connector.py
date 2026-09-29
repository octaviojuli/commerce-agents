"""Compose the existing B2B authentication transport with the cloud's strict read adapter."""

import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import wraps
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cloud_warehouse import source_limits
from cloud_warehouse.integrations import B2BConnector, SourceError
from cloud_warehouse.pricing import CustomerIdentity, PriceSchedule, SourcePrices, UnitPrices

from . import erp_client as erp
from .http_erp import LIST_TIMEOUT, READ_TIMEOUT, HttpErpClient, _data


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    env_prefix: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,60}$")
    adapter: Literal["tour_b2b", "goods_stock"] = "tour_b2b"
    supplier_company_id: int | None = Field(default=None, gt=0)


def connector_registry(
    config_path: str,
    environment: dict[str, str] | None = None,
    *,
    connection_id: UUID | None = None,
):
    """Bind API and worker reads to operator-selected secrets, never request input.

    A scoped worker validates the registry but loads only its own credentials.
    Configuration errors expose no file content, environment values or validation input.
    """
    environment = dict(os.environ) if environment is None else environment
    try:
        raw = json.loads(Path(config_path).read_text())
        if not isinstance(raw, list):
            raise ValueError
        rows = [ConnectionConfig.model_validate(row) for row in raw]
        identifiers = [row.connection_id for row in rows]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError
        if connection_id is not None and connection_id not in identifiers:
            raise ValueError
        registry = {}
        for row in rows:
            if connection_id is not None and row.connection_id != connection_id:
                continue
            if row.adapter == "goods_stock":
                from cloud_warehouse.goods_stock import connect as stock_connect
                from cloud_warehouse.goods_stock import settings as stock_settings

                prefix = row.env_prefix
                stock_config = (
                    environment[prefix + "_BASE_URL"],
                    int(environment[prefix + "_QUERY_COMPANY_ID"]),
                    row.supplier_company_id,
                    date.fromisoformat(environment[prefix + "_START_DATE"]),
                    date.fromisoformat(environment[prefix + "_END_DATE"]),
                )
                if row.supplier_company_id is None:
                    raise ValueError
                stock_settings(stock_config[0], stock_config[1], stock_config[3], stock_config[4])

                def stock_factory(settings=stock_config):
                    return stock_connect(*settings)

                registry[row.connection_id] = stock_factory
                continue
            if row.supplier_company_id is not None:
                raise ValueError
            config = {
                "TOUR_ERP_" + suffix: environment[row.env_prefix + "_" + suffix]
                for suffix in ("BASE_URL", "MOBILE", "PASSWORD")
            }
            if any(not value.strip() for value in config.values()):
                raise ValueError
            for suffix in ("STANDARD_CUSTOMER_ID", "STANDARD_PRICE_VERSION"):
                value = environment.get(row.env_prefix + "_" + suffix)
                if value is not None:
                    if not value.isdigit() or int(value) < 1:
                        raise ValueError
                    config["TOUR_ERP_" + suffix] = value
            if ("TOUR_ERP_STANDARD_CUSTOMER_ID" in config) != (
                "TOUR_ERP_STANDARD_PRICE_VERSION" in config
            ):
                raise ValueError

            def factory(settings=config):
                return connect(settings)

            registry[row.connection_id] = factory
        return registry
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("供应商连接绑定无效或凭据缺失；请核对私有配置") from None


def source_errors(function):
    """Never persist upstream messages, credentials or raw response bodies."""

    @wraps(function)
    async def read(*args, **kwargs):
        try:
            return await function(*args, **kwargs)
        except erp.ErpError as error:
            delay = error.retry_after_seconds or 0
            if delay > 7 * 86400:
                raise SourceError("SOURCE_COOLDOWN_REQUIRES_REVIEW", retryable=False) from None
            if isinstance(error, erp.ErpThrottled):
                raise SourceError(
                    "SOURCE_RATE_LIMITED", retry_after_seconds=max(60, delay)
                ) from None
            if isinstance(error, erp.ErpAuth):
                code = "SOURCE_PERMISSION_DENIED" if error.status == 403 else "SOURCE_AUTH_FAILED"
                raise SourceError(code, retryable=False) from None
            if isinstance(error, erp.ErpNotFound):
                raise SourceError("SOURCE_NOT_FOUND", retryable=False) from None
            if isinstance(error, erp.ErpUnavailable):
                raise SourceError("SOURCE_UNAVAILABLE", retry_after_seconds=delay) from None
            raise SourceError("SOURCE_REQUEST_REFUSED", retryable=False) from None

    return read


class TourWarehouseConnector(B2BConnector):
    def __init__(
        self, client: HttpErpClient, *, standard_customer_id=None, standard_price_version=None
    ):
        self.client = client
        self.standard_customer_id = standard_customer_id
        self.standard_price_version = standard_price_version

        async def fetch(path: str, params: dict) -> dict:
            if path not in {"/route/list", "/period/list"}:
                raise SourceError("READ_CAPABILITY_DISABLED")
            return await self._get(path, params, LIST_TIMEOUT)

        super().__init__(fetch)

    @source_errors
    async def _get(self, path, params, timeout=READ_TIMEOUT, company_id=None):
        if path not in {
            "/route/list",
            "/period/list",
            "/period/detail",
            "/customer/list",
            "/order/price",
            "/order/list",
            "/order/detail",
        }:
            raise SourceError("READ_CAPABILITY_DISABLED")
        data = await self.client._request(
            "GET", path, params, timeout, company_id, retry_timeout=timeout == LIST_TIMEOUT
        )
        if not isinstance(data, dict):
            raise SourceError("INVALID_SOURCE_ENVELOPE")
        return data

    @source_errors
    async def read_order_departments(self, *, allowed_departments):
        await self.client._token_for(None)
        authorized = {
            str(row["companyId"]) for row in self.client.companies if row.get("companyId")
        }
        authorized.add(str(self.client.user_info["companyId"]))
        return {
            "departments": sorted(
                authorized & {str(value) for value in allowed_departments}, key=int
            )
        }

    async def read_orders(
        self, *, allowed_departments, company_id, page=1, query="", departure_id=None
    ):
        scope = await self.read_order_departments(allowed_departments=allowed_departments)
        if str(company_id) not in scope["departments"]:
            raise SourceError("SOURCE_PERMISSION_DENIED", retryable=False)

        # Fetch complete department pagination before local departure filtering. Never
        # assume undocumented periodId filtering or silently stop on the first page.
        async def fetch(path, params):
            return await self._get(path, params, LIST_TIMEOUT, int(company_id))

        rows = await B2BConnector(fetch)._pages(
            "/order/list", {"orderNo": query} if query else {}, "orderId"
        )
        if departure_id is not None:
            rows = [row for row in rows if str(row.get("periodId")) == str(departure_id)]
        start = (page - 1) * 20
        return {
            "items": [self._order_fields(row) for row in rows[start : start + 20]],
            "total": len(rows),
            "page": page,
            "limit": 20,
            "department": str(company_id),
            "complete": True,
        }

    async def read_order(self, *, allowed_departments, company_id, order_id):
        scope = await self.read_order_departments(allowed_departments=allowed_departments)
        if str(company_id) not in scope["departments"]:
            raise SourceError("SOURCE_PERMISSION_DENIED", retryable=False)

        # Confirm the ID belongs to this token's order list before asking for detail.
        async def fetch(path, params):
            return await self._get(path, params, LIST_TIMEOUT, int(company_id))

        rows = await B2BConnector(fetch)._pages("/order/list", {}, "orderId")
        if not any(str(row["orderId"]) == str(order_id) for row in rows):
            raise SourceError("SOURCE_NOT_FOUND", retryable=False)
        detail = await self._get(
            "/order/detail", {"orderId": int(order_id)}, company_id=int(company_id)
        )
        if str(detail.get("orderId")) != str(order_id):
            raise SourceError("SOURCE_ORDER_SCOPE_MISMATCH", retryable=False)
        return {"order": self._order_fields(detail), "department": str(company_id)}

    @staticmethod
    def _order_fields(row):
        # No traveler/contact data, raw bodies, inferred prices or guessed settlement.
        keys = (
            "orderId",
            "orderNo",
            "periodId",
            "periodCode",
            "routeName",
            "customerId",
            "customerName",
            "totalAmount",
            "receivedAmount",
            "unreceivedAmount",
            "orderStatus",
            "orderStatusText",
            "createTime",
            "adultCount",
            "childCount",
            "elderCount",
            "roomCount",
            "singleRoomDiffCount",
            "originalAmount",
            "adjustAmount",
            "refundedAmount",
            "departDate",
            "returnDate",
            "reserveExpireAt",
            "currency",
            "settlementType",
        )
        return {key: row.get(key) for key in keys}

    async def read_departure_observation(
        self, *, departure_id, company_id, customer_id=None, uniform_price_version=None
    ):
        detail = await self._get(
            "/period/detail", {"periodId": int(departure_id)}, company_id=int(company_id)
        )
        if str(detail.get("periodId")) != str(departure_id) or str(detail.get("companyId")) != str(
            company_id
        ):
            raise SourceError("SOURCE_DEPARTURE_SCOPE_MISMATCH")
        fields = (
            "planGuests",
            "availableSeats",
            "confirmCount",
            "reserveCount",
            "placeholderCount",
            "waitlistCount",
            "minGroupSize",
        )
        prices = detail.get("priceInfo") or {}
        result = {
            "inventory": {key: detail.get(key) for key in fields},
            "market_prices": {
                key: prices.get(key)
                for key in ("currency", "adultPrice", "childPrice", "elderPrice", "singleRoomDiff")
            },
            "settlement_prices": None,
            "settlement_status": "customer_required",
            "source_price_type": None,
        }
        if uniform_price_version is not None:
            try:
                customer_id = self._standard_customer(uniform_price_version)
            except SourceError as error:
                result.update(settlement_status="source_error", settlement_error=error.code)
                return result
            result["price_scope"] = "uniform"
        if customer_id is not None:
            try:
                trade = await self._get(
                    "/order/price",
                    {"periodId": int(departure_id), "customerId": int(customer_id)},
                    company_id=int(company_id),
                )
            except SourceError as error:
                result.update(settlement_status="source_error", settlement_error=error.code)
            else:
                kind = trade.get("priceType")
                # Only the supplier's explicit settlement label establishes its meaning.
                result["source_price_type"] = str(kind)[:60] if kind is not None else None
                result["settlement_status"] = (
                    "ready" if self._is_settlement(kind) else "type_unconfirmed"
                )
                if result["settlement_status"] == "ready":
                    info = trade.get("priceInfo") or {}
                    result["settlement_prices"] = {
                        key: info.get(key)
                        for key in (
                            "currency",
                            "adultPrice",
                            "childPrice",
                            "elderPrice",
                            "singleRoomDiff",
                        )
                    }
        return result

    def _standard_customer(self, version):
        if not self.standard_customer_id or str(version) != str(self.standard_price_version):
            raise SourceError("UNIFORM_PRICE_CONFIGURATION_MISSING", retryable=False)
        return self.standard_customer_id

    async def read_uniform_prices(self, departure_id, company_id, policy_version):
        return await self.read_prices(
            departure_id, self._standard_customer(policy_version), company_id
        )

    @staticmethod
    def _is_settlement(kind):
        return kind in ("同行价", "同业价", "结算价", "同行结算价")

    @source_errors
    async def resolve_customer(self, code: str) -> CustomerIdentity:
        # Authenticate once to discover the authorized department list. Tokens stay inside
        # HttpErpClient; cross-department duplicate codes must not select the first match.
        await self.client._token_for(None)
        departments = {
            int(row["companyId"]) for row in self.client.companies if row.get("companyId")
        }
        departments.add(int(self.client.user_info["companyId"]))
        matches = {}
        for department in sorted(departments):

            async def fetch(path, params, company=department):
                return await self._get(path, params, LIST_TIMEOUT, company)

            rows = await B2BConnector(fetch)._pages(
                "/customer/list", {"keyword": code}, "customerId"
            )
            for row in rows:
                if row.get("csCode") == code:
                    if row.get("companyType") != 1:
                        raise SourceError("CUSTOMER_IS_NOT_TRADE_BUYER")
                    identity = CustomerIdentity(
                        customer_id=str(row["customerId"]), code=code, name=row["companyName"]
                    )
                    previous = matches.setdefault(identity.customer_id, identity)
                    if previous != identity:
                        raise SourceError("CUSTOMER_IDENTITY_CONFLICT")
        if len(matches) != 1:
            raise SourceError("CUSTOMER_CODE_NOT_UNIQUE")
        return next(iter(matches.values()))

    async def read_prices(
        self, departure_id: str, customer_id: str, company_id: str
    ) -> SourcePrices:
        observed = datetime.now(UTC)
        detail = await self._get(
            "/period/detail", {"periodId": int(departure_id)}, company_id=int(company_id)
        )
        if (
            str(detail.get("periodId")) != departure_id
            or str(detail.get("companyId")) != company_id
        ):
            raise SourceError("SOURCE_DEPARTURE_SCOPE_MISMATCH")
        trade = await self._get(
            "/order/price",
            {"periodId": int(departure_id), "customerId": int(customer_id)},
            company_id=int(company_id),
        )
        if not self._is_settlement(trade.get("priceType")):
            raise SourceError("SOURCE_PRICE_TYPE_UNCONFIRMED", retryable=False)
        market_info, trade_info = detail.get("priceInfo") or {}, trade.get("priceInfo") or {}
        currencies = {
            info["currency"] for info in (market_info, trade_info) if info.get("currency")
        }
        if len(currencies) != 1:
            raise SourceError("PRICE_CURRENCY_MISSING_OR_CONFLICTING")

        def units(raw):
            # The existing ERP represents unpublished per-person prices with zero. Missing
            # fields remain unknown; a present zero single-room supplement is meaningful.
            values = {}
            for key, upstream in (
                ("adult", "adultPrice"),
                ("child", "childPrice"),
                ("senior", "elderPrice"),
                ("single_room", "singleRoomDiff"),
            ):
                value = raw.get(upstream)
                amount = Decimal(str(value)) if value is not None else None
                values[key] = (
                    None if amount is not None and amount == 0 and key != "single_room" else amount
                )
            return UnitPrices(**values)

        return SourcePrices(
            schedule=PriceSchedule(
                currency=currencies.pop(),
                market=units(market_info),
                settlement=units(trade_info),
                fees_complete=False,
            ),
            observed_at=observed,
            source_ref="B2B /period/detail + /order/price",
            available_seats=detail.get("availableSeats"),
        )


class CooldownHttpErpClient(HttpErpClient):
    async def _exchange(self, method, path, **kwargs):
        await source_limits.check_or_record()
        response = await super()._exchange(method, path, **kwargs)
        try:
            _data(response)
        except erp.ErpError as error:
            delay = error.retry_after_seconds or 0
            if isinstance(error, erp.ErpThrottled):
                await source_limits.check_or_record(max(60, delay))
            elif isinstance(error, erp.ErpUnavailable) and delay:
                await source_limits.check_or_record(delay)
        return response


@asynccontextmanager
async def connect(config: dict[str, str]):
    client = CooldownHttpErpClient.from_login(
        config["TOUR_ERP_BASE_URL"], config["TOUR_ERP_MOBILE"], config["TOUR_ERP_PASSWORD"]
    )

    try:
        yield TourWarehouseConnector(
            client,
            standard_customer_id=config.get("TOUR_ERP_STANDARD_CUSTOMER_ID"),
            standard_price_version=config.get("TOUR_ERP_STANDARD_PRICE_VERSION"),
        )
    finally:
        await client.aclose()
