"""Read-only Tour StorefrontBackend over cloud catalog and buyer-scoped quotes."""

import base64
import json
from contextlib import contextmanager
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from shopping_agent import (
    Cart,
    NotOffered,
    Product,
    ProductDetails,
    ShoppingSessionContext,
    StorefrontBackend,
    UserPreferences,
)

from . import advisor_matching, documents, offers, quotes, sales, search, travel_search
from .changes import Conflict
from .concurrency import in_worker_thread
from .integrations import fingerprint
from .persistence import Forbidden, Principal, require_role, transaction

TRANSACTIONS_DISABLED = "云仓一期仅查询产品、报价和库存；占位、下单和支付未开放。"


def product_id(value: UUID) -> str:
    return "WP-" + str(value)


def departure_id(value: UUID) -> str:
    return "WD-" + str(value)


def parse_id(value: str, prefix: str) -> UUID:
    try:
        if not value.startswith(prefix):
            raise ValueError
        return UUID(value[len(prefix) :])
    except ValueError as error:
        raise NotOffered("请使用云仓当前查询返回的全局编号；旧 ERP 编号需经过明确映射。") from error


class WarehouseAdvisorBackend(StorefrontBackend):
    def __init__(
        self, engine: Engine, actor: Principal, connectors: quotes.Connectors | None = None
    ):
        self.engine, self.actor, self.connectors = engine, actor, connectors

    @contextmanager
    def _read(self, session: ShoppingSessionContext):
        if session.user_id != str(self.actor.user_id):
            raise Forbidden("顾问会话与登录用户不匹配")
        with transaction(self.engine, self.actor) as conn:
            require_role(conn, "advisor", "buyer_admin")
            yield conn

    @staticmethod
    def _product(row):
        return Product(
            product_id=product_id(row["id"]),
            title=row["name"],
            price=None,
            currency="XXX",
            category="tour",
            in_stock=None,
            short_description=row.get("description") or None,
            attributes={
                "supplier_id": str(row["supplier_org_id"]),
                # The short name advisors use for this supplier; never shown to customers.
                "supplier_name": row.get("supplier_name") or "",
                "source_name": row["source_name"],
                "name_origin": row["name_origin"],
                "version": str(row["version"]),
                "days": str(row["days"]) if row["days"] is not None else "未知",
                "depart_city": row["gateway"] or "未知",
                "description_origin": row["description_origin"],
                "price_status": "选择具体团期和人数后询价",
                "inventory_status": "以团期查询结果为准",
                "transactions": "disabled",
                "departure_date_check": "matched"
                if row.get("search_start") or row.get("search_end")
                else "not_checked",
                "matched_depart_from": str(row["search_start"]) if row.get("search_start") else "",
                "matched_depart_to": str(row["search_end"]) if row.get("search_end") else "",
            },
            # A route remains a family even before its departures have been paged in.
            options={"团期": []},
        )

    @staticmethod
    def _departure(row):
        seats = row["available"]
        calendar_days = (
            (row["return_date"] - row["depart_date"]).days + 1 if row["return_date"] else None
        )
        route_days = row.get("route_days")
        selling = sales.evaluate(
            row["depart_date"],
            row["business_timezone"],
            paused=row["sales_paused"],
            deadline=row["local_booking_deadline"],
        )
        return Product(
            product_id=departure_id(row["id"]),
            title=f"{row['name']} · {row['depart_date']} · {row['code']}",
            price=None,
            currency="XXX",
            category="tour",
            in_stock=False
            if not selling["can_quote"]
            else seats > 0
            if seats is not None
            else None,
            variant_of=product_id(row["product_id"]),
            option_values={"团期": row["code"]},
            attributes={
                "depart_date": str(row["depart_date"]),
                "return_date": str(row["return_date"]),
                "route_days": str(route_days) if route_days is not None else "",
                "calendar_days": str(calendar_days) if calendar_days is not None else "",
                "duration_check": "different_calendar_span"
                if route_days and calendar_days and route_days != calendar_days
                else "matched"
                if route_days and calendar_days
                else "unknown",
                "availability": "unknown"
                if seats is None
                else "available"
                if seats > 0
                else "unavailable",
                "inventory_status": row["availability_status"],
                "inventory_observed_at": row["observed_at"].isoformat(),
                "supplier_id": str(row["supplier_org_id"]),
                # The short name advisors use for this supplier; never shown to customers.
                "supplier_name": row.get("supplier_name") or "",
                "source_name": row["source_name"],
                "name_origin": row["name_origin"],
                "version": str(row["version"]),
                "offer_id": str(row["offer_id"]) if row["offer_id"] else "",
                "price_status": "未询价",
                "transactions": "disabled",
                **{
                    key: str(value).lower() if isinstance(value, bool) else value or ""
                    for key, value in selling.items()
                },
            },
        )

    @in_worker_thread
    def search_products(self, session, query, filters=None, limit=8):
        rows = self.catalog_page(session, query=query, filters=filters, limit=limit)
        return rows["items"]

    def catalog_page(
        self, session, *, query="", filters=None, limit=50, after: UUID | str | None = None
    ):
        limit = max(1, min(limit, 100))
        attrs = filters.attributes if filters else {}
        ranked = any(key in attrs for key in ("days_min", "days_max", "no_shopping", "ranked"))
        if set(attrs) - {
            "depart_from",
            "depart_to",
            "days",
            "days_min",
            "days_max",
            "depart_city",
            "no_shopping",
            "ranked",
            "destination_examples",
            "excluded_destinations",
        }:
            raise NotOffered(
                "当前目录支持出发日期、天数、出发地筛选；酒店、购物和行程细节须以已复核文档为准。"
            )
        if filters and (
            filters.min_price is not None
            or filters.max_price is not None
            or filters.min_rating is not None
            or filters.sort != "relevance"
        ):
            raise NotOffered(
                "目录没有可比较的实时总价或评分；请先选团期和人数询价，不能用未知价格筛选。"
            )
        if ranked:
            statement, parameters = advisor_matching.query_sql(query, attrs, after)
            parameters["limit"] = max(1, min(limit, 100)) + 1
            with self._read(session) as conn:
                if filters and filters.category not in (None, "tour"):
                    return {"items": [], "next_cursor": None}
                rows = conn.execute(statement, parameters).mappings().all()
                facts = search.destination_facts(conn, [row["id"] for row in rows[:limit]])
                stats = advisor_matching.card_facts(conn, [row["id"] for row in rows[:limit]])
                items, matches = [], {}
                for row in rows[:limit]:
                    product = self._product(
                        {
                            **row,
                            "search_start": parameters["start"] if row["next_date"] else None,
                            "search_end": parameters["end"] if row["next_date"] else None,
                        }
                    )
                    matches[product.product_id] = advisor_matching.reasons(
                        row, attrs, query, facts.get(row["id"])
                    )
                    import json

                    product.attributes["destination_facts"] = json.dumps(
                        facts.get(row["id"], {}), ensure_ascii=False
                    )
                    product.attributes["match_reasons"] = json.dumps(
                        matches[product.product_id], ensure_ascii=False
                    )
                    product.attributes["next_departure"] = (
                        str(row["next_date"]) if row["next_date"] else ""
                    )
                    product.attributes.update(stats.get(row["id"], {}))
                    items.append(product)
                return {
                    "items": items,
                    "match_reasons": matches,
                    "funnel": advisor_matching.funnel(conn, query, attrs)
                    if after is None
                    else None,
                    "next_cursor": advisor_matching.cursor(rows[limit - 1], query, attrs)
                    if len(rows) > limit
                    else None,
                }
        try:
            start = date.fromisoformat(attrs["depart_from"]) if attrs.get("depart_from") else None
            end = date.fromisoformat(attrs["depart_to"]) if attrs.get("depart_to") else None
            days = int(attrs["days"]) if attrs.get("days") else None
        except ValueError as error:
            raise NotOffered("日期使用 YYYY-MM-DD，天数使用正整数。") from error
        if (start and end and end < start) or (days is not None and days <= 0):
            raise NotOffered("日期范围或天数无效")
        limit = max(1, min(limit, 100))
        parameters = {
            "after": after,
            **travel_search.parameters(query),
            **travel_search.exclusions(attrs.get("excluded_destinations", "[]")),
            "days": days,
            "city": attrs.get("depart_city", ""),
            "start": start,
            "end": end,
            "limit": limit + 1,
        }
        # Only fixed SQL fragments are selected here; all input values stay bound.
        # Distinct filter shapes can reuse plans without optional-parameter ORs.
        predicates = ["p.connection_id=c.id", "p.status='published'"]
        for key, predicate in (("after", "p.id>:after"), ("days", "p.effective_days=:days")):
            if parameters[key] is not None:
                predicates.append(predicate)
        if parameters["city"] != "":
            predicates.append("p.effective_gateway=:city")
        search_join = search.JOIN if parameters["excluded_patterns"] else ""
        if parameters["excluded_patterns"]:
            predicates.append(travel_search.EXCLUDE)
        if parameters["query"]:
            search_join = search.JOIN
            predicates.append(travel_search.MATCH)
        dates = []
        if start is not None:
            dates.append("d.depart_date>=:start")
        if end is not None:
            dates.append("d.depart_date<=:end")
        if dates:
            predicates.append(
                "EXISTS(SELECT 1 FROM departure d WHERE d.product_id=p.id "
                "AND d.status='published' AND " + " AND ".join(dates) + ")"
            )
        where = " AND ".join(predicates)
        with self._read(session) as conn:
            if filters and filters.category not in (None, "tour"):
                return {"items": [], "next_cursor": None}
            rows = (
                conn.execute(
                    # Each source contributes at most one page of candidates. The
                    # global first N cannot contain a source's (N+1)th result.
                    # Keep filters/cursor inside that window and RLS on every table.
                    text(f"""SELECT candidate.*,c.name AS source_name,warehouse_supplier_name(c.supplier_org_id) AS supplier_name
                FROM supplier_connection c CROSS JOIN LATERAL (
                SELECT p.id,p.supplier_org_id,p.effective_name AS name,p.effective_days AS days,p.effective_gateway AS gateway,p.effective_description AS description,p.name_origin,p.description_origin,p.version
                FROM product_listing p {search_join} WHERE {where}
                ORDER BY p.id LIMIT :limit) candidate
                ORDER BY candidate.id LIMIT :limit"""),
                    parameters,
                )
                .mappings()
                .all()
            )
            return {
                "items": [
                    self._product({**row, "search_start": start, "search_end": end})
                    for row in rows[:limit]
                ],
                "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            }

    def departures_page(
        self,
        session,
        route_id: str,
        *,
        start: date | None = None,
        end: date | None = None,
        limit=25,
        after: str | None = None,
        party_total: int | None = None,
        include_out_of_window: bool = False,
    ):
        parent = parse_id(route_id, "WP-")
        if start and end and end < start:
            raise NotOffered("结束日期不得早于开始日期")
        limit = max(1, min(limit, 100))
        scope = fingerprint(
            {
                "actor": str(self.actor.user_id),
                "org": str(self.actor.organization_id),
                "route": route_id,
                "start": str(start),
                "end": str(end),
                "party": party_total,
                "outside": include_out_of_window,
            }
        )
        paging = {"page_start": start, "page_end": end}
        if after:
            try:
                anchor = json.loads(base64.urlsafe_b64decode(str(after).encode()))
                if (
                    anchor["scope"] != scope
                    or type(anchor["outside"]) is not int
                    or anchor["outside"] not in (0, 1)
                ):
                    raise ValueError
                paging.update(
                    cursor_outside=anchor["outside"],
                    cursor_date=date.fromisoformat(anchor["date"]),
                    cursor_id=UUID(anchor["id"]),
                )
            except (ValueError, TypeError, KeyError) as error:
                raise NotOffered("团期查询条件已变化或游标无效，请从第一页重新查询") from error
        with self._read(session) as conn:
            outside_total = conn.scalar(
                text("""SELECT count(*) FROM departure d JOIN product_listing p ON p.id=d.product_id
                WHERE d.product_id=:parent AND d.status='published' AND p.status='published'
                AND ((CAST(:start AS date) IS NOT NULL AND d.depart_date<:start)
                  OR (CAST(:end AS date) IS NOT NULL AND d.depart_date>:end))"""),
                {"parent": parent, "start": start, "end": end},
            )
            rows = self._departures(
                conn,
                parent=parent,
                start=None if include_out_of_window else start,
                end=None if include_out_of_window else end,
                limit=limit + 1,
                paging=paging,
            )
            items = []
            for row in rows[:limit]:
                product = self._departure(row)
                capacity = (
                    "out_of_window"
                    if (start and row["depart_date"] < start) or (end and row["depart_date"] > end)
                    else "unknown"
                    if party_total is None
                    or row["available"] is None
                    or row["availability_status"] == "stale"
                    else "ok"
                    if row["available"] >= party_total
                    else "short"
                )
                product.attributes["capacity_match"] = capacity
                product.attributes["requested_party_total"] = (
                    str(party_total) if party_total else ""
                )
                items.append(product)
            return {
                "items": items,
                "next_cursor": base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "scope": scope,
                            "outside": rows[limit - 1]["outside_rank"],
                            "date": str(rows[limit - 1]["depart_date"]),
                            "id": str(rows[limit - 1]["id"]),
                        }
                    ).encode()
                ).decode()
                if len(rows) > limit
                else None,
                "ordering": "window_date_id",
                "out_of_window_total": outside_total,
                "reservation_created": False,
            }

    @staticmethod
    def _departures(
        conn,
        *,
        parent=None,
        departure=None,
        start=None,
        end=None,
        limit=25,
        after=None,
        paging=None,
    ):
        predicates = ["d.status='published'", "p.status='published'"]
        if departure is None:
            predicates += [
                "NOT d.sales_paused",
                "(d.local_booking_deadline IS NULL OR d.local_booking_deadline>now())",
                "d.depart_date >= (now() AT TIME ZONE COALESCE(c.capabilities->>'business_timezone','Asia/Shanghai'))::date",
            ]
        for value, predicate in (
            (parent, "d.product_id=:parent"),
            (departure, "d.id=:departure"),
            (after, "d.id>:after"),
            (start, "d.depart_date>=:start"),
            (end, "d.depart_date<=:end"),
        ):
            if value is not None:
                predicates.append(predicate)
        outside = (
            "CASE WHEN (CAST(:page_start AS date) IS NOT NULL AND d.depart_date<:page_start) OR (CAST(:page_end AS date) IS NOT NULL AND d.depart_date>:page_end) THEN 1 ELSE 0 END"
            if paging is not None
            else "0"
        )
        ordering = f"{outside},d.depart_date,d.id" if paging is not None else "d.id"
        if paging and "cursor_id" in paging:
            predicates.append(
                f"({outside},d.depart_date,d.id) > (:cursor_outside,:cursor_date,:cursor_id)"
            )
        where = " AND ".join(predicates)
        return (
            conn.execute(
                text(f"""SELECT {outside} AS outside_rank,d.id,d.product_id,d.supplier_org_id,d.code,d.depart_date,d.return_date,
          d.version,d.observed_at,d.sales_paused,d.local_booking_deadline,p.effective_name AS name,p.name_origin,p.effective_days AS route_days,o.id AS offer_id,c.name AS source_name,warehouse_supplier_name(c.supplier_org_id) AS supplier_name,
          COALESCE(c.capabilities->>'business_timezone','Asia/Shanghai') AS business_timezone,
          CASE WHEN i.id IS NOT NULL THEN i.total-i.sold-i.held-i.blocked
            WHEN d.availability_expires_at>now() THEN d.available_seats END AS available,
          CASE WHEN i.id IS NOT NULL THEN 'managed' WHEN d.availability_expires_at<=now() THEN 'stale'
            WHEN d.available_seats IS NULL THEN 'unknown' ELSE 'known' END AS availability_status
          FROM departure d JOIN product_listing p ON p.id=d.product_id
          JOIN supplier_connection c ON c.id=d.connection_id
          LEFT JOIN inventory_pool i ON i.departure_id=d.id
          LEFT JOIN offer o ON o.departure_id=d.id AND o.code='base' AND o.active
          WHERE {where}
          ORDER BY {ordering} LIMIT :limit"""),
                {
                    "parent": parent,
                    "departure": departure,
                    "start": start,
                    "end": end,
                    "after": after,
                    "limit": limit,
                    **(paging or {}),
                },
            )
            .mappings()
            .all()
        )

    @in_worker_thread
    def get_product_details(self, session, identifier):
        if identifier.startswith("WD-"):
            dep_id = parse_id(identifier, "WD-")
            with self._read(session) as conn:
                rows = self._departures(conn, departure=dep_id, limit=1)
                if not rows:
                    return None
                document = documents.current_in_transaction(
                    conn, rows[0]["product_id"], departure_id=dep_id, sales=True
                )
                return ProductDetails(
                    **self._departure(rows[0]).model_dump(), specs=self._document_specs(document)
                )
        route_id = parse_id(identifier, "WP-")
        with self._read(session) as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT p.id,p.supplier_org_id,p.effective_name AS name,p.effective_description AS description,p.name_origin,p.description_origin,p.effective_days AS days,p.effective_gateway AS gateway,p.version,c.name AS source_name,warehouse_supplier_name(c.supplier_org_id) AS supplier_name FROM product_listing p JOIN supplier_connection c ON c.id=p.connection_id WHERE p.id=:id AND p.status='published'"
                    ),
                    {"id": route_id},
                )
                .mappings()
                .one_or_none()
            )
            if not row:
                return None
            details = self._product(row).model_dump()
            # Route content is independent from dated stock. Reading an itinerary
            # must not implicitly fetch every departure for a model to enumerate.
            document = documents.current_in_transaction(conn, route_id, sales=True)
            specs = self._document_specs(document)
            return ProductDetails(
                **details,
                long_description=row["description"] or None,
                specs=specs,
            )

    @staticmethod
    def _document_specs(document):
        import json

        from .route_content import customer_projection

        if not document:
            return {"文档状态": "这条线路尚未发布行程。不能据名称推断游览范围、节奏、门票或航班。"}
        content = customer_projection(
            document["body"], review_mode=document.get("review_mode", "human")
        )
        overview = {
            "title": content["title"],
            "days": [{"day": d["day"], "title": d["title"]} for d in content.get("days", [])][:40],
        }
        return {
            "文档状态": (
                "测试自动发布，未人工审核；仅供开发测试，价格与余位须另行查询。"
                if document.get("review_mode") == "test_auto"
                else "已复核并发布；仅证明行程内容，价格与余位须另行查询。"
            ),
            "文档版本ID": str(document["id"]),
            "产品版本": str(document["product_version"]),
            "内容完整性": "这里只显示概览；未显示部分不可据此比较或承诺。使用 read_route_itinerary 按天或费用条款读取完整内容并继续分页。",
            "已复核行程": json.dumps(overview, ensure_ascii=False),
        }

    def offers_page(self, session, identifier: str, *, after=None, limit=25):
        with self._read(session):
            pass
        return offers.list_for_departure(
            self.engine, self.actor, parse_id(identifier, "WD-"), after=after, limit=limit
        )

    @in_worker_thread
    def _quote_request(self, session, identifier, party, offer_id):
        dep_id = parse_id(identifier, "WD-")
        with self._read(session) as conn:
            rows = self._departures(conn, departure=dep_id, limit=1)
            if not rows:
                raise NotOffered("该团期不存在、未发布或未授权")
            row = rows[0]
            if offer_id is None:
                available = list(
                    conn.execute(
                        text(
                            "SELECT id FROM offer WHERE departure_id=:id AND active ORDER BY id LIMIT 2"
                        ),
                        {"id": dep_id},
                    ).scalars()
                )
                if not available:
                    raise NotOffered("该团期没有可用报价方案")
                if len(available) > 1:
                    raise Conflict("该团期有多个报价方案，请先读取方案并明确选择后询价")
                offer_id = available[0]
            elif not conn.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM offer WHERE id=:offer AND departure_id=:departure AND active)"
                ),
                {"offer": offer_id, "departure": dep_id},
            ):
                raise NotOffered("报价方案不属于所选团期或无权限")
        return quotes.QuoteRequest(
            offer_id=offer_id, departure_date=row["depart_date"], party=party
        )

    async def quote_departure(
        self,
        session,
        identifier: str,
        party: quotes.Party,
        *,
        offer_id: UUID | None = None,
        idempotency_key: str | None = None,
    ):
        request = await self._quote_request(session, identifier, party, offer_id)
        return await quotes.create(
            self.engine,
            self.actor,
            request,
            idempotency_key or "advisor:" + str(uuid4()),
            self.connectors,
        )

    @in_worker_thread
    def get_preferences(self, session):
        with self._read(session):
            return UserPreferences(
                user_id=str(self.actor.user_id),
                preferences={"采购组织": str(self.actor.organization_id)},
            )

    @in_worker_thread
    def get_account_context(self, session):
        with self._read(session) as conn:
            from .trip_brief import envelope, load

            try:
                identifier = UUID(session.session_id)
            except ValueError:
                brief_context = {}
            else:
                brief, version = load(conn, identifier)
                brief_context = envelope(brief, version)
                brief_context["body"].pop("quote", None)
                brief_context["body"].pop("share_token", None)
            return {
                "trip_brief": brief_context,
                "buyer_org_id": str(self.actor.organization_id),
                "transactions_enabled": False,
                "limitations": "没有查到的价格、库存及未复核的行程条件必须明确为未知；报价不是预留或成交。",
            }

    @in_worker_thread
    def get_cart(self, session):
        with self._read(session):
            return Cart(currency="XXX")

    @in_worker_thread
    def add_to_cart(self, session, product_id, quantity):
        with self._read(session):
            raise NotOffered(TRANSACTIONS_DISABLED)

    @in_worker_thread
    def update_cart_item(self, session, product_id, quantity):
        with self._read(session):
            raise NotOffered(TRANSACTIONS_DISABLED)

    @in_worker_thread
    def remove_from_cart(self, session, product_id):
        with self._read(session):
            raise NotOffered(TRANSACTIONS_DISABLED)

    @in_worker_thread
    def checkout_handoff(self, session, cart):
        with self._read(session):
            raise NotOffered(TRANSACTIONS_DISABLED)

    @in_worker_thread
    def get_orders(self, session, limit=5):
        with self._read(session):
            raise NotOffered("云仓一期未接入顾问订单，不能把没有接入当作没有订单。")

    async def get_order(self, session, order_id):
        return await self.get_orders(session)

    @in_worker_thread
    def search_policies(self, session, query):
        with self._read(session):
            raise NotOffered("当前尚无已复核的供应商退改及履约规则，请向供应商确认。")

    @in_worker_thread
    def get_fulfillment_options(self, session, product_ids):
        with self._read(session):
            raise NotOffered(TRANSACTIONS_DISABLED)
