"""Version-bound retail quotes and explicitly manual sales/receipt records."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field
from sqlalchemy import text

from . import copilot_records as records
from . import quote_shares, quotes
from .changes import Conflict
from .persistence import transaction


class Retail(records.Command):
    sales_total: records.Money | None = None
    loss_confirmed: bool = False


class Sale(records.Command):
    retail_quote_id: UUID
    supplier_order_number: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=2000)


class Receipt(records.Command):
    sale_id: UUID
    amount: records.Money = Field(gt=0)
    category: str = Field(pattern="^(deposit|balance|refund)$")
    received_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reference: str = Field(min_length=1, max_length=500)


def current_retail(conn, actor, identifier, record_id, *, require_fresh=True):
    from . import copilot_dependencies

    record = records.record(conn, record_id, kind="retail_quote", deal_id=identifier)
    brief, version = records.trip_brief.load(conn, identifier)
    body = record["body"]
    stale = (
        bool(copilot_dependencies.changed(body["dependencies"], brief))
        if "dependencies" in body
        else record["brief_version"] != version or str(brief.quote_id) != body["quote_id"]
    )
    stale = stale or datetime.fromisoformat(body["expires_at"]) <= datetime.now(UTC)
    row = quotes._read(conn, UUID(body["quote_id"]))
    view = quotes._display(conn, actor, row) if row else None
    stale = stale or view is None or view["quote_expired"]
    if require_fresh and stale:
        raise Conflict("报价或需求已经变化，请重新核价并确认")
    return record, view, stale


def retail(engine, actor, identifier, request):
    from . import copilot_dependencies, copilot_engine

    with transaction(engine, actor) as conn:
        previous = (
            conn.execute(
                text("SELECT * FROM advisor_record WHERE request_key=:key"),
                {"key": str(request.request_id)},
            )
            .mappings()
            .one_or_none()
        )
        submitted = request.model_dump(mode="json", exclude={"request_id"})
        if previous:
            if (
                previous["deal_id"] != identifier
                or previous["kind"] != "retail_quote"
                or previous["body"].get("submitted") != submitted
            ):
                raise Conflict("销售报价请求编号已用于其他内容")
            return dict(previous)
        brief, version = records.checked(conn, identifier, request.expected_version)
        confirmation = copilot_engine.current_confirmation(conn, identifier, brief, version)
        if (
            not confirmation
            or brief.quote_id is None
            or (brief.quote_brief_version or 0) < brief.quote_fields_version
        ):
            raise Conflict("请先重新核价，并逐项核对当前版本确认单")
        row = quotes._read(conn, brief.quote_id)
        if row is None:
            raise Conflict("结算报价不可用，请重新询价")
        quote = quotes._display(conn, actor, row)
        if (
            quote["quote_expired"]
            or not quote.get("complete")
            or quote.get("settlement_total") is None
        ):
            raise Conflict("结算价不完整或已过期，请重新核实；不能生成正式销售报价")
        sales = (
            request.sales_total
            if request.sales_total is not None
            else Decimal(quote["market_total"])
        )
        settlement = Decimal(quote["settlement_total"])
        profit = sales - settlement
        if profit < 0 and not request.loss_confirmed:
            raise Conflict("销售价低于结算价，请明确确认亏损销售")
        body = {
            "submitted": submitted,
            "quote_id": str(brief.quote_id),
            "sales_total": quotes._money(sales),
            "settlement_total": quotes._money(settlement),
            "profit": quotes._money(profit),
            "margin": str((profit / sales * 100).quantize(Decimal("0.1"))) if sales else None,
            "additional_items": [
                r
                for r in conn.execute(
                    text(
                        "SELECT jsonb_build_object('text',body->>'text','amount',body->>'amount','currency',body->>'currency') FROM advisor_record WHERE deal_id=:id AND kind='note' AND body->>'category'='fee' ORDER BY created_at"
                    ),
                    {"id": identifier},
                ).scalars()
            ],
            "currency": quote["currency"],
            "loss_confirmed": request.loss_confirmed,
            "expires_at": quote["quote_valid_until"],
            "dependencies": copilot_dependencies.stamp(brief),
            "fresh_until": quote["fresh_until"],
            "product_name": quote["product_name"],
            "departure_date": quote["departure_date"],
            "reservation_created": False,
            "manual": True,
        }
        return records.append(
            conn, actor, identifier, "retail_quote", body, version, str(request.request_id)
        )


def customer_projection(conn, actor, record_id, quote):
    row = records.record(conn, record_id, kind="retail_quote")
    _, _, stale = current_retail(conn, actor, row["deal_id"], record_id, require_fresh=False)
    body = row["body"]
    result = quotes.customer_view(quote)
    result.update(
        {
            "additional_items": body.get("additional_items", []),
            "market_total": body["sales_total"],
            "known_market_subtotal": body["sales_total"],
            "market_lines": [
                {
                    "code": "retail_total",
                    "label": "旅行方案总价",
                    "quantity": 1,
                    "unit_amount": body["sales_total"],
                    "total": body["sales_total"],
                }
            ],
            "snapshot_stale": stale,
            "retail_quote": True,
            "retail_expires_at": body["expires_at"],
            "quote_valid_until": body["expires_at"],
            "quote_expired": stale,
        }
    )
    return result


def share(engine, actor, identifier, record_id, request):
    # quote_shares.create repeats all checks under its own issuance transaction.
    with transaction(engine, actor) as conn:
        records.checked(conn, identifier, request.expected_version)
        row, _, _ = current_retail(conn, actor, identifier, record_id)
    return quote_shares.create(
        engine,
        actor,
        UUID(row["body"]["quote_id"]),
        quote_shares.Create(request_id=request.request_id, hours=24, advisor_record_id=record_id),
    )


def sale(engine, actor, identifier, request):
    with transaction(engine, actor) as conn:
        _, version = records.checked(conn, identifier, request.expected_version)
        retail_row, _, _ = current_retail(conn, actor, identifier, request.retail_quote_id)
        if conn.scalar(
            text("SELECT EXISTS(SELECT 1 FROM advisor_record WHERE deal_id=:id AND kind='sale')"),
            {"id": identifier},
        ):
            old = (
                conn.execute(
                    text(
                        "SELECT * FROM advisor_record WHERE request_key=:key AND deal_id=:id AND kind='sale'"
                    ),
                    {"key": str(request.request_id), "id": identifier},
                )
                .mappings()
                .one_or_none()
            )
            if (
                old
                and old["body"].get("supplier_order_number") == request.supplier_order_number
                and old["body"].get("retail_quote_id") == str(request.retail_quote_id)
                and old["body"].get("note") == request.note
            ):
                return dict(old)
            raise Conflict("此跟单已有线下成交记录，请勿重复登记")
        return records.append(
            conn,
            actor,
            identifier,
            "sale",
            {
                **retail_row["body"],
                "retail_quote_id": str(request.retail_quote_id),
                "supplier_order_number": request.supplier_order_number,
                "note": request.note,
                "status": "顾问登记的线下成交",
                "inventory_deducted": False,
                "supplier_confirmed": False,
            },
            version,
            str(request.request_id),
        )


def receipt(engine, actor, identifier, request):
    with transaction(engine, actor) as conn:
        _, version = records.checked(conn, identifier, request.expected_version, private_only=True)
        sale_row = records.record(conn, request.sale_id, kind="sale", deal_id=identifier)
        from datetime import date

        date.fromisoformat(request.received_on)
        previous = (
            conn.execute(
                text("SELECT * FROM advisor_record WHERE deal_id=:id AND kind='receipt'"),
                {"id": identifier},
            )
            .mappings()
            .all()
        )
        net = sum(
            Decimal(r["body"]["amount"]) * (-1 if r["body"]["category"] == "refund" else 1)
            for r in previous
            if r["body"]["sale_id"] == str(request.sale_id)
            and r["request_key"] != str(request.request_id)
        )
        next_net = net + request.amount * (-1 if request.category == "refund" else 1)
        if not 0 <= next_net <= Decimal(sale_row["body"]["sales_total"]):
            raise Conflict("累计收款须介于零与成交销售总价之间")
        return records.append(
            conn,
            actor,
            identifier,
            "receipt",
            {
                **request.model_dump(mode="json", exclude={"request_id", "expected_version"}),
                "currency": sale_row["body"]["currency"],
                "recorded_by_advisor": True,
                "platform_collected": False,
            },
            version,
            str(request.request_id),
        )
