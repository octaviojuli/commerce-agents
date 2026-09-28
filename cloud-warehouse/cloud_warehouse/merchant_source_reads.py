"""Explicit supplier-admin order reads and scoped departure observations, without writes."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from . import merchant_business
from .integrations import SourceError
from .persistence import Forbidden, require_role, transaction
from .source_limits import open_connector


def scope(engine, actor, connection_id, *, orders=False):
    with transaction(engine, actor) as conn:
        require_role(
            conn,
            *(
                ("supplier_admin",)
                if orders
                else ("supplier_admin", "product_editor", "inventory_manager", "auditor")
            ),
        )
        row = (
            conn.execute(
                text(
                    "SELECT id,name,capabilities FROM supplier_connection WHERE id=:id AND supplier_org_id=warehouse_org_id() AND active AND connector_type='tour_b2b'"
                ),
                {"id": connection_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("B2B 来源不存在或无权读取")
        return dict(row)


async def read(engine, actor, connectors, connection_id, method, *, orders=False, **kwargs):
    before = await run_in_threadpool(scope, engine, actor, connection_id, orders=orders)
    factory = (connectors or {}).get(connection_id)
    if factory is None:
        raise SourceError("READ_CAPABILITY_DISABLED", retryable=False)
    if orders:
        allowed = before["capabilities"].get("order_read_departments", [])
        if not allowed:
            raise SourceError("ORDER_READ_SCOPE_NOT_CONFIGURED", retryable=False)
        kwargs["allowed_departments"] = allowed
    if method == "read_departure_observation":
        policy = before["capabilities"].get("settlement_pricing", {})
        if policy.get("mode") == "uniform":
            kwargs.pop("customer_id", None)
            version = policy.get("version")
            if type(version) is not int or version < 1:
                raise SourceError("UNIFORM_PRICE_CONFIGURATION_MISSING", retryable=False)
            kwargs["uniform_price_version"] = version
    try:
        async with asyncio.timeout(25):
            async with open_connector(engine, actor, connection_id, factory) as connector:
                handler = getattr(connector, method, None)
                if handler is None:
                    raise SourceError("READ_CAPABILITY_DISABLED", retryable=False)
                result = await handler(**kwargs)
    except TimeoutError:
        raise SourceError("SOURCE_READ_TIMEOUT") from None
    after = await run_in_threadpool(scope, engine, actor, connection_id, orders=orders)
    if before["capabilities"] != after["capabilities"]:
        raise Forbidden("来源读取范围已变更，请重新查询")
    if orders and method in {"read_orders", "read_order"}:
        result = await run_in_threadpool(link_orders, engine, actor, connection_id, result)
    return {
        **result,
        "source_name": after["name"],
        "connection_id": str(connection_id),
        "observed_at": datetime.now(UTC),
    }


def price_customer(engine, actor, connection_id, verification_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        row = (
            conn.execute(
                text("""SELECT customer_id,customer_code,customer_name
            FROM customer_verification WHERE id=:id AND connection_id=:source
            AND supplier_org_id=warehouse_org_id() AND created_by=warehouse_user_id()
            AND expires_at>now()"""),
                {"id": verification_id, "source": connection_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("客户核验已过期或不属于当前用户与供应源，请重新核验")
        return dict(row)


async def observation(engine, actor, connectors, identifier, verification_id=None):
    before = await run_in_threadpool(merchant_business.departure, engine, actor, identifier)
    if before["connector_type"] != "tour_b2b" or not before["company_id"]:
        raise SourceError("READ_CAPABILITY_DISABLED", retryable=False)
    customer = None
    options = {}
    if verification_id is not None:
        customer = await run_in_threadpool(
            price_customer, engine, actor, before["connection_id"], verification_id
        )
        options["customer_id"] = customer["customer_id"]
    result = await read(
        engine,
        actor,
        connectors,
        before["connection_id"],
        "read_departure_observation",
        departure_id=before["external_id"],
        company_id=before["company_id"],
        **options,
    )
    after = await run_in_threadpool(merchant_business.departure, engine, actor, identifier)
    if any(
        before[key] != after[key]
        for key in ("version", "connection_id", "company_id", "external_id")
    ):
        raise Forbidden("团期已更新，请重新查询")
    if customer is not None and result.get("price_scope") != "uniform":
        if customer != await run_in_threadpool(
            price_customer, engine, actor, before["connection_id"], verification_id
        ):
            raise Forbidden("客户价格上下文已变更，请重新查询")
        result["price_customer"] = {
            "code": customer["customer_code"],
            "name": customer["customer_name"],
        }
    return result


def link_orders(engine, actor, connection_id, result):
    rows = result.get("items", [result["order"]] if "order" in result else [])
    identifiers = [str(row["periodId"]) for row in rows if row.get("periodId")]
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        linked = {
            str(row["external_id"]): row
            for row in conn.execute(
                text(f"""
            SELECT d.external_id,d.id,p.id AS product_id FROM departure d
            JOIN product_listing p ON p.id=d.product_id JOIN supplier_connection c ON c.id=d.connection_id
            WHERE d.supplier_org_id=warehouse_org_id() AND d.connection_id=:source
              AND d.company_id=:department AND d.external_id=ANY(:identifiers)
              AND {merchant_business.ROUTE_SCOPE} AND {merchant_business.DEPARTURE_SCOPE}
            """),
                {
                    "source": connection_id,
                    "department": result["department"],
                    "identifiers": identifiers,
                },
            ).mappings()
        }
        for row in rows:
            match = linked.get(str(row.get("periodId")))
            row["warehouse_departure_id"] = str(match["id"]) if match else None
            row["warehouse_route_id"] = str(match["product_id"]) if match else None
    return result
