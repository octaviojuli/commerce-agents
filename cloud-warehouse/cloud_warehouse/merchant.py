"""Original MerchantBackend protocol backed by tenant-scoped warehouse transactions."""

from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from merchant_agent.backend import MerchantBackend
from merchant_agent.changes import ChangeNotApplicable
from merchant_agent.types import (
    ActorKind,
    BusinessSnapshot,
    ChangeKind,
    InventoryAlert,
    Listing,
    ListingDetails,
    MerchantSessionContext,
    MetricSeries,
    PricingContext,
    StagedChange,
)

from . import (
    catalog_edits,
    changes,
    inventory,
    merchant_commands,
    pricing,
    product_display,
    sales,
    search,
)
from .concurrency import in_worker_thread
from .persistence import Forbidden, Principal, require_role, transaction

SELECTION = " AND EXISTS(SELECT 1 FROM supplier_connection c WHERE c.id=p.connection_id AND (NOT c.capabilities ? 'catalog_selection' OR c.capabilities->'catalog_selection'->'route_ids' ? p.external_id)) "


class WarehouseChange(StagedChange):
    payload_hash: str
    buyer_org_ids: list[UUID] = []


class WarehouseMerchantBackend(MerchantBackend):
    """A host creates one backend per authenticated organization and price context.

    Neither organization nor buyer price context can be changed by a model tool argument.
    SQL analysis, campaigns and order management remain unsupported in phase one.
    """

    def __init__(self, engine: Engine, actor: Principal, *, buyer_org_id: UUID | None = None):
        self.engine, self.actor, self.buyer_org_id = engine, actor, buyer_org_id

    @contextmanager
    def _read(self, session: MerchantSessionContext):
        if session.merchant_id != str(self.actor.organization_id) or session.operator != str(
            self.actor.user_id
        ):
            raise Forbidden("商户会话与登录身份不匹配")
        with transaction(self.engine, self.actor) as conn:
            require_role(conn, "supplier_admin", "product_editor", "inventory_manager", "auditor")
            yield conn

    def _catalog(self, conn, product_ids=None):
        products = (
            conn.execute(
                text(
                    "SELECT id,effective_name AS name,status,effective_description AS description,name_origin,description_origin,name_override,description_override,display_version,version,effective_days AS days,effective_gateway AS gateway,(SELECT connector_type FROM supplier_connection c WHERE c.id=p.connection_id) AS connector_type FROM product_listing p WHERE supplier_org_id=warehouse_org_id() AND EXISTS(SELECT 1 FROM supplier_connection sc WHERE sc.id=p.connection_id AND (NOT sc.capabilities ? 'catalog_selection' OR sc.capabilities->'catalog_selection'->'route_ids' ? p.external_id)) AND (CAST(:ids AS uuid[]) IS NULL OR id=ANY(CAST(:ids AS uuid[]))) ORDER BY id"
                ),
                {"ids": product_ids},
            )
            .mappings()
            .all()
        )
        departures = (
            conn.execute(
                text("""SELECT d.id,d.product_id,d.code,d.depart_date,d.return_date,d.status,d.version,
          d.sales_paused,d.local_booking_deadline,
          COALESCE(source.capabilities->>'business_timezone','Asia/Shanghai') AS business_timezone,
          i.id AS pool_id,i.version AS pool_version,
          CASE WHEN i.id IS NOT NULL THEN i.total-i.sold-i.held-i.blocked
               WHEN d.availability_expires_at>now() THEN d.available_seats END AS stock,
          c.schedule,c.id AS contract_id,c.version AS price_version
          FROM departure d JOIN supplier_connection source ON source.id=d.connection_id
          LEFT JOIN inventory_pool i ON i.departure_id=d.id
          LEFT JOIN offer o ON o.departure_id=d.id AND o.code='base' AND o.active
          LEFT JOIN LATERAL warehouse_effective_price(o.id,CAST(:buyer AS uuid)) c ON true
          WHERE d.supplier_org_id=warehouse_org_id() AND (NOT source.capabilities ? 'catalog_selection' OR source.capabilities->'catalog_selection'->'departure_ids' ? d.external_id) AND (CAST(:ids AS uuid[]) IS NULL OR d.product_id=ANY(CAST(:ids AS uuid[]))) ORDER BY d.depart_date,d.id"""),
                {"buyer": self.buyer_org_id, "ids": product_ids},
            )
            .mappings()
            .all()
        )
        grouped = {}
        for row in departures:
            grouped.setdefault(row["product_id"], []).append(row)
        listings = []
        for product in products:
            variants = []
            for row in grouped.get(product["id"], []):
                schedule = (
                    pricing.PriceSchedule.model_validate(row["schedule"])
                    if row["schedule"]
                    else None
                )
                price = schedule.settlement.adult if schedule else None
                selling = sales.evaluate(
                    row["depart_date"],
                    row["business_timezone"],
                    paused=row["sales_paused"],
                    deadline=row["local_booking_deadline"],
                )
                variants.append(
                    Listing(
                        listing_id=str(row["id"]),
                        title=f"{product['name']} · {row['depart_date']} · {row['code']}",
                        status=self._status(
                            (row["status"] if selling["can_quote"] else "paused")
                            if product["status"] == "published"
                            else product["status"]
                        ),
                        price=float(price) if price is not None else None,
                        stock=row["stock"],
                        currency=schedule.currency if schedule else "XXX",
                        variant_of=str(product["id"]),
                        option_values={"团期": row["code"]},
                        attributes={
                            "出发日期": str(row["depart_date"]),
                            "返回日期": str(row["return_date"]),
                            "版本": str(row["version"]),
                            "库存口径": "云仓托管" if row["pool_id"] else "上游快照，过期为未知",
                            "销售状态": selling["sales_status_label"],
                            "云仓报名截止": selling["local_booking_deadline"]
                            or "未设置；仍需核实上游条件",
                        },
                    )
                )
            currencies = {v.currency for v in variants}
            price_known = (
                bool(variants)
                and len(currencies) == 1
                and all(v.price is not None for v in variants)
            )
            listings.append(
                ListingDetails(
                    listing_id=str(product["id"]),
                    title=product["name"],
                    status=self._status(product["status"]),
                    price=min(v.price for v in variants) if price_known else None,
                    stock=sum(v.stock for v in variants)
                    if variants and all(v.stock is not None for v in variants)
                    else None,
                    currency=next(iter(currencies)) if len(currencies) == 1 else "XXX",
                    options={"团期": [v.option_values["团期"] for v in variants]}
                    if variants
                    else {},
                    variants=variants,
                    long_description=product["description"],
                    category="旅游线路",
                    attributes={
                        "版本": str(product["version"]),
                        "展示版本": str(product["display_version"]),
                        "来源类型": product["connector_type"],
                        "线路名称来源": "云仓展示补充"
                        if product["name_origin"] == "warehouse_display"
                        else "供应源",
                        "线路介绍来源": "云仓展示补充"
                        if product["description_origin"] == "warehouse_display"
                        else "供应源",
                        "天数": str(product["days"] or "未知"),
                        "出发地": product["gateway"] or "未知",
                    },
                )
            )
        return listings

    @staticmethod
    def _status(value):
        return {"published": "active", "paused": "paused", "archived": "paused", "draft": "draft"}[
            value
        ]

    @in_worker_thread
    def search_listings(self, session, query, filters=None, limit=8):
        with self._read(session) as conn:
            ids = list(
                conn.execute(
                    text(f"""SELECT p.id FROM product_listing p {search.JOIN}
              WHERE p.supplier_org_id=warehouse_org_id() {SELECTION} AND {search.MATCH} ORDER BY p.id"""),
                    search.parameters(query),
                ).scalars()
            )
            rows = self._catalog(conn, ids) if ids else []
        if filters:
            for field in ("status", "category", "content_quality"):
                value = getattr(filters, field)
                if value is not None:
                    rows = [row for row in rows if getattr(row, field) == value]
            if filters.max_stock is not None:
                rows = [
                    row for row in rows if row.stock is not None and row.stock <= filters.max_stock
                ]
            if filters.sort == "sales_desc":
                raise ChangeNotApplicable("一期没有订单销售额统计，不能按销售排名")
            if filters.sort in {"price_asc", "price_desc", "stock_asc"}:
                field = "stock" if filters.sort == "stock_asc" else "price"
                direction = -1 if filters.sort == "price_desc" else 1
                rows.sort(
                    key=lambda row: (
                        getattr(row, field) is None,
                        direction * (getattr(row, field) or 0),
                        row.listing_id,
                    )
                )
        return [Listing.model_validate(row.model_dump()) for row in rows[: max(1, min(limit, 100))]]

    def catalog_page(self, session, *, query="", after=None, limit=25):
        limit = max(1, min(limit, 100))
        with self._read(session) as conn:
            ids = list(
                conn.execute(
                    text(
                        f"SELECT p.id FROM product_listing p {search.JOIN} WHERE p.supplier_org_id=warehouse_org_id() {SELECTION} AND (CAST(:after AS uuid) IS NULL OR p.id>:after) AND {search.MATCH} ORDER BY p.id LIMIT :limit"
                    ),
                    {"after": after, **search.parameters(query), "limit": limit + 1},
                ).scalars()
            )
            data = self._catalog(conn, ids[:limit]) if ids else []
            return {
                "items": [Listing.model_validate(row.model_dump()) for row in data],
                "next_cursor": str(ids[limit - 1]) if len(ids) > limit else None,
            }

    @in_worker_thread
    def get_listing(self, session, listing_id):
        return self._get_listing(session, listing_id)

    def _get_listing(self, session, listing_id):
        with self._read(session) as conn:
            for family in self._catalog(conn):
                if family.listing_id == listing_id:
                    return family
                for variant in family.variants:
                    if variant.listing_id == listing_id:
                        return ListingDetails(
                            **variant.model_dump(), long_description=family.long_description
                        )
        return None

    @in_worker_thread
    def get_business_snapshot(self, session, period=None):
        with self._read(session):
            return BusinessSnapshot(
                period=period or "当前",
                currency="XXX",
                note="一期未接入订单、收款和流量统计；销售登记人数不能当作订单数或销售额。",
            )

    @in_worker_thread
    def query_metrics(self, session, metric, period=None, granularity="day", segment=None):
        with self._read(session):
            return MetricSeries(
                metric=metric,
                period=period,
                granularity=granularity,
                segment=segment,
                note="一期未接入该经营统计；请通过库存流水核对供应商销售登记。",
            )

    @in_worker_thread
    def get_campaign_performance(self, session, campaign_id=None):
        with self._read(session):
            raise ChangeNotApplicable("一期未接入营销活动")

    @in_worker_thread
    def get_order_issues(self, session):
        with self._read(session):
            raise ChangeNotApplicable("一期未接入订单，无法判断订单异常")

    @in_worker_thread
    def get_inventory_alerts(self, session):
        with self._read(session) as conn:
            rows = self._catalog(conn)
        return [
            InventoryAlert(listing_id=v.listing_id, title=v.title, kind="low_stock", stock=v.stock)
            for row in rows
            for v in row.variants
            if v.status == "active" and v.stock is not None and v.stock <= 5
        ]

    @in_worker_thread
    def get_pricing_context(self, session, listing_id):
        row = self._get_listing(session, listing_id)
        if row is None:
            return None
        return PricingContext(
            listing_id=row.listing_id,
            current_price=row.price,
            currency=row.currency,
            max_price_delta_pct=merchant_commands.POLICY.max_price_delta_pct,
            variants=[
                PricingContext(listing_id=v.listing_id, current_price=v.price, currency=v.currency)
                for v in row.variants
            ],
        )

    def _stage(self, session, kind, bodies, note):
        commands = [merchant_commands.Command(kind=kind, body=body) for body in bodies]
        diffs, currencies = [], set()
        with self._read(session) as conn:
            for command in commands:
                _, _, _, items, currency = merchant_commands.inspect_command(
                    conn, self.actor, command
                )
                diffs.extend(items)
                if currency:
                    currencies.add(currency)
        if len(currencies) > 1:
            raise ChangeNotApplicable("不同币种需要分别提议")
        batch = merchant_commands.Batch(
            kind=kind,
            summary=(
                note
                or {
                    ChangeKind.LISTING_UPDATE: "修改线路内容",
                    ChangeKind.PRICE_UPDATE: "修改所选采购方成人同业价",
                    ChangeKind.INVENTORY_ACTION: "修改供应商库存或发布状态",
                }[kind]
            )[:200],
            commands=commands,
            items=diffs,
            currency=next(iter(currencies), None),
        )
        proposal = changes.stage(self.engine, self.actor, "merchant", batch.model_dump(mode="json"))
        return self._change(session, UUID(proposal["id"]))

    @in_worker_thread
    def stage_listing_update(self, session, listing_id, fields, note=None):
        if not fields or set(fields) - {"title", "long_description"}:
            raise ChangeNotApplicable(
                "线路编辑支持 title、long_description；价格、库存和日期使用各自流程"
            )
        if any(
            not isinstance(value, str)
            or len(value) > merchant_commands.POLICY.max_listing_field_chars
            for value in fields.values()
        ):
            raise ChangeNotApplicable("内容必须是文字，且每个字段不超过商户助手配置的长度上限")
        row = self._get_listing(session, listing_id)
        if not row or row.variant_of:
            raise ChangeNotApplicable("请在主线路编辑共享内容")
        with self._read(session) as conn:
            source = product_display.current(conn, UUID(listing_id))
            if source["connector_type"] != "excel":
                display = product_display.capture(
                    conn,
                    product_display.Command(
                        target_id=UUID(listing_id),
                        expected_version=int(row.attributes["版本"]),
                        expected_display_version=int(row.attributes["展示版本"]),
                        name_override=fields.get("title", source["name_override"]),
                        description_override=fields.get(
                            "long_description", source["description_override"]
                        ),
                        note=note or "商户助手提议云仓展示补充",
                    ),
                )
                body = {"display": display.model_dump(mode="json")}
            else:
                body = None
        if body is not None:
            return self._stage(session, ChangeKind.LISTING_UPDATE, [body], note)
        command = catalog_edits.CatalogEdit(
            target_id=UUID(listing_id),
            expected_version=int(row.attributes["版本"]),
            name=fields.get("title"),
            description=fields.get("long_description"),
        )
        return self._stage(
            session,
            ChangeKind.LISTING_UPDATE,
            [command.model_dump(mode="json", exclude_none=True)],
            note,
        )

    @in_worker_thread
    def stage_price_update(self, session, items, note=None):
        if not self.buyer_org_id:
            raise ChangeNotApplicable("请先在商户界面选择采购组织，不能推断同业价适用客户")
        bodies = []
        with self._read(session) as conn:
            for item in items:
                row = (
                    conn.execute(
                        text("""SELECT c.* FROM contract_price c JOIN offer o ON o.id=c.offer_id
                  WHERE o.departure_id=:id AND o.code='base' AND o.active AND c.buyer_org_id=:buyer
                    AND c.active AND c.valid_from<=now() AND c.valid_until>now() AND warehouse_live_grant(c.grant_id)"""),
                        {"id": UUID(item.listing_id), "buyer": self.buyer_org_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                if not row:
                    raise ChangeNotApplicable(
                        "该团期没有所选采购方的有效协议价；API 来源价格由上游维护"
                    )
                schedule = pricing.PriceSchedule.model_validate(row["schedule"])
                data = schedule.model_dump(mode="json")
                data["settlement"]["adult"] = str(Decimal(str(item.new_price)))
                command = pricing.ContractProposal(
                    offer_id=row["offer_id"],
                    price_id=row["id"],
                    buyer_org_id=self.buyer_org_id,
                    expected_version=row["version"],
                    schedule=data,
                    source_ref=row["source_ref"],
                    valid_from=row["valid_from"],
                    valid_until=row["valid_until"],
                )
                bodies.append(
                    pricing.capture_grant(conn, self.actor, command.model_dump(mode="json"))
                )
        return self._stage(session, ChangeKind.PRICE_UPDATE, bodies, note)

    @in_worker_thread
    def stage_inventory_action(self, session, items, note=None):
        bodies = []
        for item in items:
            if item.action in {"pause", "activate"}:
                row = self._get_listing(session, item.listing_id)
                if not row or row.variant_of:
                    raise ChangeNotApplicable("当前上下架按主线路管理；团期单独状态维护尚未开放")
                bodies.append(
                    catalog_edits.CatalogEdit(
                        target_id=UUID(item.listing_id),
                        expected_version=int(row.attributes["版本"]),
                        status="paused" if item.action == "pause" else "published",
                    ).model_dump(mode="json", exclude_none=True)
                )
            else:
                with self._read(session) as conn:
                    row = (
                        conn.execute(
                            text(
                                "SELECT id,version FROM inventory_pool WHERE departure_id=:id AND supplier_org_id=warehouse_org_id()"
                            ),
                            {"id": UUID(item.listing_id)},
                        )
                        .mappings()
                        .one_or_none()
                    )
                if not row or not item.quantity:
                    raise ChangeNotApplicable("补库存需要已托管的具体团期和正整数数量")
                bodies.append(
                    inventory.InventoryCommand(
                        action="adjust",
                        target_id=row["id"],
                        expected_version=row["version"],
                        quantity=item.quantity,
                        business_key="merchant:" + str(uuid4()),
                        reason=note or "商户审批补充团期库存",
                    ).model_dump(mode="json")
                )
        return self._stage(session, ChangeKind.INVENTORY_ACTION, bodies, note)

    @in_worker_thread
    def stage_promotion(self, session, promotion):
        with self._read(session):
            raise ChangeNotApplicable("一期未开放促销；请按采购组织维护协议价")

    @in_worker_thread
    def stage_campaign(self, session, campaign):
        with self._read(session):
            raise ChangeNotApplicable("一期未开放营销活动")

    @staticmethod
    def _display_change(row):
        batch = merchant_commands.Batch.model_validate(row["payload"])
        return WarehouseChange(
            change_id=str(row["id"]),
            payload_hash=row["payload_hash"],
            buyer_org_ids=sorted(
                {
                    UUID(command.body["buyer_org_id"])
                    for command in batch.commands
                    if "buyer_org_id" in command.body
                }
            ),
            kind=batch.kind,
            status=row["status"],
            summary=batch.summary,
            items=batch.items,
            currency=batch.currency,
            created_at=row["created_at"],
            created_by=str(row["created_by"]),
            applied_at=row["applied_at"],
            applied_by=str(row["applied_by"]) if row["applied_by"] else None,
            discarded_at=row["discarded_at"],
            discarded_by=str(row["discarded_by"]) if row["discarded_by"] else None,
            discarded_by_kind=row["discarded_by_kind"],
            guardrail_notes=["需要供应商管理员在主机审批界面确认内容哈希；对话同意不能替代审批。"],
        )

    def _change(self, session, change_id):
        with self._read(session) as conn:
            row = (
                conn.execute(
                    text("SELECT * FROM change_request WHERE id=:id AND kind='merchant'"),
                    {"id": change_id},
                )
                .mappings()
                .one_or_none()
            )
            if not row:
                raise ChangeNotApplicable("商户变更不存在或无权限")
            return self._display_change(row)

    @in_worker_thread
    def get_pending_changes(self, session):
        # MerchantBackend returns the complete pending set, with no cursor contract.
        # A fixed SQL limit would silently hide changes from the host and agent.
        with self._read(session) as conn:
            return [
                self._display_change(row)
                for row in conn.execute(
                    text(
                        "SELECT * FROM change_request WHERE kind='merchant' AND status='staged' ORDER BY created_at,id"
                    )
                ).mappings()
            ]

    @in_worker_thread
    def apply_change(self, session, change_id):
        self._change(session, UUID(change_id))
        changes.apply(self.engine, self.actor, UUID(change_id))
        return self._change(session, UUID(change_id))

    @in_worker_thread
    def discard_change(self, session, change_id, actor_kind=ActorKind.OPERATOR):
        self._change(session, UUID(change_id))
        changes.discard(self.engine, self.actor, UUID(change_id), actor_kind.value)
        return self._change(session, UUID(change_id))

    @in_worker_thread
    def get_merchant_context(self, session):
        with self._read(session):
            return {
                "merchant_id": str(self.actor.organization_id),
                "buyer_org_id": str(self.buyer_org_id) if self.buyer_org_id else None,
                "limitations": [
                    {"source": "orders", "note": "一期没有顾问占位、订单、支付或营销数据。"},
                    {
                        "source": "pricing",
                        "note": "价格是界面所选采购组织的成人同业价；未选客户或无协议价时未知。",
                    },
                    {
                        "source": "catalog",
                        "note": "API 来源事实由上游维护；API 线路文字修改形成独立的云仓展示补充，经审批生效。上下架仅面向 Excel 来源。",
                    },
                ],
            }
