"""Read-only quote snapshots. Buyer scope and supplier customer IDs are server-resolved."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Engine, text

from . import price_books, sales, source_limits
from .changes import Conflict, audit
from .concurrency import in_worker_thread
from .integrations import SourceError, canonical, fingerprint
from .persistence import Forbidden, Principal, require_role, transaction
from .pricing import PriceConnector, PriceSchedule, SourcePrices
from .sales import DEFAULT_BUSINESS_TIMEZONE
from .sales import business_zone as _business_zone
from .sales import departure_day_end as _departure_day_end

Connectors = dict[UUID, Callable[[], AbstractAsyncContextManager[PriceConnector]]]
SOURCE_PRICE_BUDGET_SECONDS = 8.0


class RoomAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doubles: int = Field(default=0, ge=0, le=100, strict=True)
    twins: int = Field(default=0, ge=0, le=100, strict=True)
    singles: int = Field(default=0, ge=0, le=100, strict=True)
    child_bed: bool | None = None
    raw: str = Field(default="", max_length=300)


class Party(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adults: int = Field(default=1, ge=0, le=100, strict=True)
    children: int = Field(default=0, ge=0, le=100, strict=True)
    seniors: int = Field(default=0, ge=0, le=100, strict=True)
    single_rooms: int = Field(default=0, ge=0, le=100, strict=True)
    child_ages: list[int] = Field(default_factory=list, max_length=100)
    room_type: str | None = Field(default=None, max_length=100)
    rooms: RoomAllocation | None = None

    @model_validator(mode="after")
    def quantities(self):
        total = self.adults + self.children + self.seniors
        if not 1 <= total <= 100 or self.single_rooms > total:
            raise ValueError("人数为 1–100，单房数量不能超过人数")
        if self.child_ages and (
            len(self.child_ages) != self.children
            or any(type(age) is not int or not 0 <= age <= 17 for age in self.child_ages)
        ):
            raise ValueError("儿童年龄数量及范围与儿童人数不一致")
        return self


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_id: UUID
    departure_date: date
    party: Party


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate(schedule: PriceSchedule, party: Party) -> dict:
    missing, market_lines, settlement_lines = [], [], []
    totals = {"market": Decimal(0), "settlement": Decimal(0)}
    known_counts = {"market": 0, "settlement": 0}
    for side, lines in (("market", market_lines), ("settlement", settlement_lines)):
        prices = getattr(schedule, side)
        for field, quantity in (
            ("adult", party.adults),
            ("child", party.children),
            ("senior", party.seniors),
            ("single_room", party.single_rooms),
        ):
            if not quantity:
                continue
            value = getattr(prices, field)
            if value is None:
                missing.append(side + "." + field)
                lines.append(
                    {"code": field, "quantity": quantity, "unit_amount": None, "total": None}
                )
            else:
                total = value * quantity
                totals[side] += total
                known_counts[side] += 1
                lines.append(
                    {
                        "code": field,
                        "quantity": quantity,
                        "unit_amount": _money(value),
                        "total": _money(total),
                    }
                )
        for charge in schedule.charges:
            quantity = (
                party.adults + party.children + party.seniors if charge.basis == "per_person" else 1
            )
            value = getattr(charge, side)
            if value is None:
                missing.append(side + ".charge." + charge.code)
            else:
                totals[side] += value * quantity
                known_counts[side] += 1
            lines.append(
                {
                    "code": charge.code,
                    "label": charge.label,
                    "quantity": quantity,
                    "unit_amount": _money(value) if value is not None else None,
                    "total": _money(value * quantity) if value is not None else None,
                }
            )
    if not schedule.fees_complete:
        missing.append("fees_not_fully_confirmed")
    if party.children:
        if schedule.child_occupies_seat is None:
            missing.append("child_seat_policy_unknown")
        if schedule.child_age_min is None:
            missing.append("child_age_policy_unknown")
        elif len(party.child_ages) != party.children:
            missing.append("child_ages_required")
        elif any(
            not schedule.child_age_min <= age <= schedule.child_age_max for age in party.child_ages
        ):
            missing.append("child_age_outside_price_rule")
    if party.room_type and party.room_type not in schedule.room_types:
        missing.append("room_type_not_confirmed")
    complete = not missing
    return {
        "currency": schedule.currency,
        "complete": complete,
        "missing_items": missing,
        "market_total": _money(totals["market"]) if complete else None,
        "settlement_total": _money(totals["settlement"]) if complete else None,
        "known_market_subtotal": _money(totals["market"]) if known_counts["market"] else None,
        "known_settlement_subtotal": _money(totals["settlement"])
        if known_counts["settlement"]
        else None,
        "market_lines": market_lines,
        "settlement_lines": settlement_lines,
        "included_items": schedule.included_items,
        "required_seats": None
        if party.children and schedule.child_occupies_seat is None
        else party.adults + party.seniors + (party.children if schedule.child_occupies_seat else 0),
    }


def _scope(conn, actor: Principal, request: QuoteRequest):
    require_role(conn, "advisor", "buyer_admin")
    row = (
        conn.execute(
            text("""SELECT o.id AS offer_id,o.version AS offer_version,o.name AS offer_name,o.service_description,o.inventory_pool_id,o.supplier_org_id,o.connection_id,d.id AS departure_id,
        d.external_id,d.company_id,d.depart_date,d.code AS departure_code,p.effective_name AS product_name,p.name_origin AS product_name_origin,c.name AS source_name,d.version AS departure_version,p.version AS product_version,p.display_version,
        d.sales_paused,d.local_booking_deadline,
        c.connector_type,c.capabilities->'settlement_pricing' AS settlement_pricing,
        COALESCE(c.capabilities->>'business_timezone',:default_zone) AS business_timezone,
        g.id AS grant_id,g.version AS grant_version,g.expires_at AS grant_expires_at,
        NULLIF(mode.sales_version,0) AS sales_policy_version
        FROM offer o JOIN departure d ON d.id=o.departure_id JOIN product_listing p ON p.id=d.product_id
        JOIN supplier_connection c ON c.id=o.connection_id
        CROSS JOIN (SELECT warehouse_sales_version() AS sales_version) mode
        LEFT JOIN distribution_grant g ON mode.sales_version=0 AND g.connection_id=o.connection_id
          AND g.buyer_org_id=warehouse_org_id() AND warehouse_live_grant(g.id)
        WHERE o.id=:id AND o.active AND d.status='published' AND p.status='published' AND c.active
          AND (mode.sales_version>0 OR g.id IS NOT NULL)"""),
            {"id": request.offer_id, "default_zone": DEFAULT_BUSINESS_TIMEZONE},
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        raise Forbidden("报价方案未上线、已停止供货或当前账号无权查询")
    if row["depart_date"] != request.departure_date:
        raise Conflict("出行日期与团期不一致")
    state = sales.evaluate(
        row["depart_date"],
        row["business_timezone"],
        paused=row["sales_paused"],
        deadline=row["local_booking_deadline"],
        now=datetime.now(UTC),
    )
    if not state["can_quote"]:
        raise Conflict(state["sales_status_label"])
    context = dict(row)
    if row["connector_type"] == "excel":
        context["price"], context["price_boundary"], context["grade"] = price_books.resolve(
            conn, request.offer_id, actor.organization_id, uniform=bool(row["sales_policy_version"])
        )
    elif (row["settlement_pricing"] or {}).get("mode") == "uniform":
        policy = row["settlement_pricing"]
        if type(policy.get("version")) is not int or policy["version"] < 1:
            raise Conflict("统一结算价策略尚未配置完成")
        context["binding"] = None
        context["uniform_price_version"] = policy["version"]
    elif row["sales_policy_version"]:
        context["binding"] = None
    else:
        binding = (
            conn.execute(
                text(
                    "SELECT b.id,b.customer_id,b.version FROM buyer_customer_binding b WHERE b.connection_id=:connection AND b.buyer_org_id=warehouse_org_id() AND b.active AND EXISTS(SELECT 1 FROM binding_acceptance a WHERE a.binding_id=b.id AND a.version=b.version)"
                ),
                {"connection": row["connection_id"]},
            )
            .mappings()
            .one_or_none()
        )
        context["binding"] = dict(binding) if binding else None
    return context


def _version_key(context: dict):
    return tuple(
        context.get(key)
        for key in (
            "offer_version",
            "inventory_pool_id",
            "departure_version",
            "product_version",
            "display_version",
            "grant_version",
            "external_id",
            "company_id",
            "business_timezone",
            "sales_paused",
            "local_booking_deadline",
            "uniform_price_version",
            "sales_policy_version",
        )
    ) + (
        context.get("binding"),
        context.get("price"),
        context.get("grade"),
        context.get("price_boundary"),
    )


def _read(conn, quote_id: UUID | None = None, idempotency_key: str | None = None):
    if (quote_id is None) == (idempotency_key is None):
        raise ValueError("Specify one quote identifier")
    # Separate point lookups let PostgreSQL reuse indexed plans. The organization
    # comes from this transaction; RLS still checks live grant and offer access.
    lookup = "id=:id" if quote_id is not None else "idempotency_key=:key"
    return (
        conn.execute(
            text(
                "SELECT * FROM quote_snapshot WHERE buyer_org_id=warehouse_org_id() AND actor_id=warehouse_user_id() AND "
                + lookup
            ),
            {"id": quote_id} if quote_id is not None else {"key": idempotency_key},
        )
        .mappings()
        .one_or_none()
    )


def _versions(context):
    result = {
        "offer": context["offer_version"],
        "pool": str(context["inventory_pool_id"]) if context["inventory_pool_id"] else None,
        "departure": context["departure_version"],
        "product": context["product_version"],
        "display": context["display_version"],
        "grant": context["grant_version"],
        "price": context["price"]["version"] if context.get("price") else None,
        "price_id": str(context["price"]["id"]) if context.get("price") else None,
        "grade": context["grade"]["version"] if context.get("grade") else None,
        "binding": context["binding"]["version"] if context.get("binding") else None,
    }
    if context.get("uniform_price_version"):
        result["uniform_price"] = context["uniform_price_version"]
    if context.get("sales_policy_version"):
        result["sales_policy"] = context["sales_policy_version"]
    return result


def _display(conn, actor, row):
    stale = row["fresh_until"] <= datetime.now(UTC)
    body = row["body"]
    try:
        current = _scope(
            conn,
            actor,
            QuoteRequest(
                offer_id=body["offer_id"],
                departure_date=body["departure_date"],
                party=body["party"],
            ),
        )
        stale = (
            stale
            or _versions(current) != body["versions"]
            or current["business_timezone"] != body.get("business_timezone")
        )
    except Conflict:
        stale = True
    result = {**body, "snapshot_stale": stale, "fresh_until": row["fresh_until"].isoformat()}
    if stale:
        result.update(
            {"available_seats": None, "availability_status": "stale", "capacity_sufficient": None}
        )
    seats = result.pop("available_seats", None)
    result["availability"] = (
        "unknown" if seats is None else "available" if seats > 0 else "unavailable"
    )
    return result


def get(engine: Engine, actor: Principal, quote_id: UUID) -> dict:
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        row = _read(conn, quote_id)
        if not row:
            raise Forbidden("报价不存在或授权已失效")
        return _display(conn, actor, row)


@in_worker_thread
def _prepare_quote(engine, actor, request, idempotency_key, request_hash):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        existing = _read(conn, idempotency_key=idempotency_key)
        if existing:
            if existing["request_hash"] != request_hash:
                raise Conflict("相同幂等键不能用于不同报价条件")
            return None, _display(conn, actor, existing)
        context = _scope(conn, actor, request)
    return context, None


@in_worker_thread
def _save_quote(
    engine,
    actor,
    request,
    idempotency_key,
    request_hash,
    context,
    source_kind,
    source,
    quote_id,
    body,
    fresh_until,
):
    with transaction(engine, actor) as conn:
        current = _scope(conn, actor, request)
        if _version_key(current) != _version_key(context):
            raise Conflict("报价查询期间授权、客户映射或产品版本变化，请重新查询")
        stock = conn.execute(
            text("SELECT total-sold-held-blocked AS available FROM inventory_pool WHERE id=:id"),
            {"id": context["inventory_pool_id"]},
        ).scalar_one_or_none()
        body["available_seats"] = (
            stock if source_kind == "contract_price" else source.available_seats if source else None
        )
        body["availability_status"] = "known" if body["available_seats"] is not None else "unknown"
        body["capacity_sufficient"] = (
            None
            if body["available_seats"] is None or body["required_seats"] is None
            else body["available_seats"] >= body["required_seats"]
        )
        conn.execute(
            text(
                "INSERT INTO quote_snapshot(id,buyer_org_id,actor_id,offer_id,grant_id,sales_policy_version,idempotency_key,request_hash,body,fresh_until) VALUES(:id,:buyer,:actor,:offer,:grant,:sales_version,:key,:hash,CAST(:body AS jsonb),:fresh) ON CONFLICT(buyer_org_id,actor_id,idempotency_key) DO NOTHING"
            ),
            {
                "id": quote_id,
                "buyer": actor.organization_id,
                "actor": actor.user_id,
                "offer": request.offer_id,
                "grant": context["grant_id"],
                "sales_version": context["sales_policy_version"],
                "key": idempotency_key,
                "hash": request_hash,
                "body": canonical(body),
                "fresh": fresh_until,
            },
        )
        saved = _read(conn, idempotency_key=idempotency_key)
        if not saved or saved["request_hash"] != request_hash:
            raise Conflict("幂等键已被不同请求使用，或授权已失效")
        if saved["id"] == quote_id:
            audit(
                conn,
                actor,
                "quote.created",
                quote_id,
                {"offer_id": str(request.offer_id), "complete": body["complete"]},
            )
        return _display(conn, actor, saved)


async def create(
    engine: Engine,
    actor: Principal,
    request: QuoteRequest,
    idempotency_key: str,
    connectors: Connectors | None = None,
) -> dict:
    if not 1 <= len(idempotency_key) <= 128 or any(ord(c) < 32 for c in idempotency_key):
        raise ValueError("报价幂等键格式无效")
    request_hash = fingerprint(request.model_dump(mode="json"))
    context, repeated = await _prepare_quote(engine, actor, request, idempotency_key, request_hash)
    if repeated is not None:
        return repeated
    observed, source, missing = datetime.now(UTC), None, []
    source_kind = "contract_price" if context["connector_type"] == "excel" else "supplier_api"
    if source_kind == "contract_price":
        price = context["price"]
        if price:
            source = SourcePrices(
                schedule=PriceSchedule.model_validate(price["schedule"]),
                observed_at=observed,
                expires_at=price["valid_until"],
                source_ref=price["source_ref"],
            )
        else:
            missing.append(
                "UNIFORM_PRICE_MISSING"
                if context["sales_policy_version"]
                else "BUYER_CONTRACT_PRICE_MISSING"
            )
    elif not context.get("uniform_price_version") and not context["binding"]:
        missing.append(
            "UNIFORM_PRICE_CONFIGURATION_MISSING"
            if context["sales_policy_version"]
            else "CUSTOMER_BINDING_MISSING_OR_NOT_ACCEPTED"
        )
    elif not context["company_id"]:
        missing.append("SOURCE_DEPARTMENT_MISSING")
    elif context["connection_id"] not in (connectors or {}):
        missing.append("SOURCE_PRICE_CONNECTOR_NOT_CONFIGURED")
    else:
        try:
            async with asyncio.timeout(SOURCE_PRICE_BUDGET_SECONDS) as deadline:
                async with source_limits.open_connector(
                    engine, actor, context["connection_id"], connectors[context["connection_id"]]
                ) as connector:
                    if context.get("uniform_price_version"):
                        method = getattr(connector, "read_uniform_prices", None)
                        if method is None:
                            raise SourceError(
                                "UNIFORM_PRICE_CONFIGURATION_MISSING", retryable=False
                            )
                        received = await method(
                            context["external_id"],
                            context["company_id"],
                            context["uniform_price_version"],
                        )
                    else:
                        received = await connector.read_prices(
                            context["external_id"],
                            context["binding"]["customer_id"],
                            context["company_id"],
                        )
            # A connector that returns while handling cancellation must not make
            # a late price usable. Accept only after its context has closed in budget.
            if deadline.expired():
                raise TimeoutError
            source = received
        except TimeoutError:
            missing.append("SOURCE_PRICE_TIMEOUT")
        except SourceError as error:
            missing.append(error.code)
        except ValidationError:
            missing.append("INVALID_SOURCE_PRICE")
        except Exception:
            missing.append("SOURCE_PRICE_UNAVAILABLE")
    if source:
        observed = source.observed_at
        body = calculate(source.schedule, request.party)
    else:
        body = {
            "currency": None,
            "complete": False,
            "market_total": None,
            "settlement_total": None,
            "known_market_subtotal": None,
            "known_settlement_subtotal": None,
            "market_lines": [],
            "settlement_lines": [],
            "missing_items": missing,
            "required_seats": None,
        }
    quote_id = uuid4()
    now = datetime.now(UTC)
    fresh_until = min(
        observed + timedelta(minutes=5),
        source.expires_at if source and source.expires_at else now + timedelta(minutes=5),
        context["grant_expires_at"] or now + timedelta(minutes=5),
        context["local_booking_deadline"] or now + timedelta(minutes=5),
        context.get("price_boundary") or now + timedelta(minutes=5),
        _departure_day_end(request.departure_date, _business_zone(context["business_timezone"])),
    )
    body.update(
        {
            "quote_id": str(quote_id),
            "offer_id": str(request.offer_id),
            "buyer_org_id": str(actor.organization_id),
            "departure_date": request.departure_date.isoformat(),
            "business_timezone": context["business_timezone"],
            "local_booking_deadline": context["local_booking_deadline"].isoformat()
            if context["local_booking_deadline"]
            else None,
            "product_name": context["product_name"],
            "product_name_origin": context["product_name_origin"],
            "departure_code": context["departure_code"],
            "source_name": context["source_name"],
            "party": request.party.model_dump(),
            "price_source": source_kind,
            "price_layer": context["price"]["layer"] if context.get("price") else None,
            "offer_name": context["offer_name"],
            "service_description": context["service_description"],
            "inventory_pool_id": str(context["inventory_pool_id"])
            if context["inventory_pool_id"]
            else None,
            "source_ref": source.source_ref if source else None,
            "observed_at": observed.isoformat(),
            "expires_at": source.expires_at.isoformat() if source and source.expires_at else None,
            "confirmation_required": True,
            "reservation_created": False,
            "versions": _versions(context),
        }
    )
    return await _save_quote(
        engine,
        actor,
        request,
        idempotency_key,
        request_hash,
        context,
        source_kind,
        source,
        quote_id,
        body,
        fresh_until,
    )


def customer_view(quote: dict) -> dict:
    """Allowlist projection for customer documents; never serialize the full buyer snapshot."""
    result = {
        key: quote.get(key)
        for key in (
            "quote_id",
            "product_name",
            "product_name_origin",
            "offer_name",
            "service_description",
            "departure_date",
            "business_timezone",
            "local_booking_deadline",
            "currency",
            "market_total",
            "known_market_subtotal",
            "complete",
            "included_items",
            "observed_at",
            "expires_at",
            "fresh_until",
            "snapshot_stale",
            "confirmation_required",
            "reservation_created",
        )
    }
    result["party"] = {
        key: quote.get("party", {}).get(key)
        for key in ("adults", "children", "seniors", "single_rooms", "room_type")
    }
    result["market_lines"] = [
        {key: line.get(key) for key in ("code", "label", "quantity", "unit_amount", "total")}
        for line in quote.get("market_lines", [])
    ]
    return result
