"""Supplier route and departure read models; source fields retain their own meaning."""

from datetime import UTC, datetime

from sqlalchemy import text

from . import sales
from .persistence import Forbidden, require_role, transaction

# The operator's explicit acceptance selection is independent of publication and sales state.
ROUTE_SCOPE = "(NOT c.capabilities ? 'catalog_selection' OR c.capabilities->'catalog_selection'->'route_ids' ? p.external_id)"
DEPARTURE_SCOPE = "(NOT c.capabilities ? 'catalog_selection' OR c.capabilities->'catalog_selection'->'departure_ids' ? d.external_id)"
ROUTE_FIELDS = """p.id,p.code,p.effective_name AS name,p.status,p.effective_days AS days,p.effective_gateway AS gateway,
 p.days AS source_days,p.gateway AS source_gateway,p.days_origin,p.gateway_origin,p.facts_version,
 p.effective_description AS description,p.name_origin,p.description_origin,p.version,p.display_version,
 p.observed_at,p.connection_id,c.name AS source_name,c.connector_type,
 c.capabilities->'catalog_selection'->>'note' AS selection_note,
 p.source->'fromPrice' AS reference_price,p.source->>'currency' AS currency,
 p.source->'features' AS features,p.source->'tags' AS tags,p.approved_tags,p.content_version,
 p.source->'itineraryTags' AS itinerary_tags,p.source->>'firstImageUrl' AS image_url,
 p.source->'posterUrls' AS poster_urls,p.source->>'saleType' AS sale_type"""
DEPARTURE_FIELDS = """d.id,d.code,d.product_id,p.code AS route_code,p.effective_name AS route_name,
 d.depart_date,d.return_date,d.status,d.version,d.company_id,d.connection_id,c.name AS source_name,
 c.connector_type,d.available_seats,d.observed_at,d.availability_expires_at,
 d.source_quarantined,d.sales_paused,d.local_booking_deadline,
 coalesce(c.capabilities->>'business_timezone','Asia/Shanghai') AS business_timezone,
 d.source->'planGuests' AS plan_guests,d.source->'confirmCount' AS confirm_count,
 d.source->'reserveCount' AS reserve_count,d.source->'placeholderCount' AS placeholder_count,
 d.source->'waitlistCount' AS waitlist_count,d.source->'minGroupSize' AS min_group_size,
 d.source->>'companyName' AS department_name,
 i.id AS pool_id,i.total,i.sold,i.held,i.blocked,
 CASE WHEN i.id IS NOT NULL THEN i.total-i.sold-i.held-i.blocked ELSE d.available_seats END AS observed_available,
 (i.id IS NOT NULL OR d.availability_expires_at>now()) AS inventory_fresh"""
DEPARTURE_FROM = """FROM departure d JOIN product_listing p ON p.id=d.product_id
 JOIN supplier_connection c ON c.id=d.connection_id
 LEFT JOIN inventory_pool i ON i.departure_id=d.id"""


def _role(conn):
    require_role(conn, "supplier_admin", "product_editor", "inventory_manager", "auditor")


def _page(conn, fields, tables, predicates, params, page, limit, order):
    page, limit = max(1, min(page, 10000)), max(1, min(limit, 100))
    where = " AND ".join(predicates)
    total = conn.scalar(text(f"SELECT count(*) {tables} WHERE {where}"), params)
    items = [
        dict(row)
        for row in conn.execute(
            text(
                f"SELECT {fields} {tables} WHERE {where} ORDER BY {order} LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": (page - 1) * limit},
        ).mappings()
    ]
    return {"items": items, "total": total, "page": page, "limit": limit}


def routes(engine, actor, *, query="", status="", connection_id=None, page=1, limit=20):
    with transaction(engine, actor) as conn:
        _role(conn)
        result = _page(
            conn,
            ROUTE_FIELDS,
            "FROM product_listing p JOIN supplier_connection c ON c.id=p.connection_id",
            [
                "p.supplier_org_id=warehouse_org_id()",
                ROUTE_SCOPE,
                "(:query='' OR strpos(lower(p.code||' '||p.effective_name||' '||p.tags_search),lower(:query))>0)",
                "(:status='' OR p.status=:status)",
                "(CAST(:source AS uuid) IS NULL OR p.connection_id=:source)",
            ],
            {"query": query[:500], "status": status, "source": connection_id},
            page,
            limit,
            "p.code,p.id",
        )
        for row in result["items"]:
            row.update(
                dict(
                    conn.execute(
                        text(f"""SELECT count(*) AS departure_count,min(d.depart_date) FILTER(WHERE d.depart_date>=CURRENT_DATE) AS next_departure
                FROM departure d JOIN supplier_connection c ON c.id=d.connection_id
                WHERE d.product_id=:id AND {DEPARTURE_SCOPE}"""),
                        {"id": row["id"]},
                    )
                    .mappings()
                    .one()
                )
            )
        return result


def route(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        _role(conn)
        row = (
            conn.execute(
                text(f"""SELECT {ROUTE_FIELDS},p.published_document_id
            FROM product_listing p JOIN supplier_connection c ON c.id=p.connection_id
            WHERE p.id=:id AND p.supplier_org_id=warehouse_org_id() AND {ROUTE_SCOPE}"""),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("线路不存在、不在展示范围或无权限")
        result = dict(row)
        publication = (
            conn.execute(
                text(
                    "SELECT id,body,product_version,created_at,review_mode,quality_metrics FROM document_publication WHERE id=:id AND product_id=:product"
                ),
                {"id": row["published_document_id"], "product": identifier},
            )
            .mappings()
            .one_or_none()
        )
        result["document"] = dict(publication) if publication else None
        if publication and publication["review_mode"] == "test_auto":
            result["document"]["body"] = {
                **publication["body"],
                "publication_notice": "测试自动发布 · 未人工审核，仅供开发测试",
            }
        result["draft_document"] = None
        if not publication and conn.scalar(
            text("SELECT warehouse_has_role(ARRAY['supplier_admin','product_editor','auditor'])")
        ):
            draft = (
                conn.execute(
                    text("""SELECT p.id,p.body,a.product_version,a.file_name,p.created_at
                FROM document_asset a JOIN document_parse p ON p.asset_id=a.id
                AND p.generation=a.parse_generation
                WHERE a.product_id=:product AND a.supplier_org_id=warehouse_org_id()
                AND a.status='parsed' ORDER BY a.created_at DESC,a.id DESC LIMIT 1"""),
                    {"product": identifier},
                )
                .mappings()
                .one_or_none()
            )
            result["draft_document"] = dict(draft) if draft else None
        return result


def _departure(row):
    result = dict(row)
    result.update(
        sales.evaluate(
            row["depart_date"],
            row["business_timezone"],
            paused=row["sales_paused"],
            deadline=row["local_booking_deadline"],
        )
    )
    if row["source_quarantined"]:
        result["sales_status_label"] = "上游关联异常，已隔离"
    elif row["status"] != "published":
        result["sales_status_label"] = "云仓未发布"
    result["inventory_authority"] = "云仓自管" if row["pool_id"] else "B2B 上游"
    return result


def departures(
    engine,
    actor,
    *,
    query="",
    product_id=None,
    connection_id=None,
    start=None,
    end=None,
    page=1,
    limit=20,
):
    with transaction(engine, actor) as conn:
        _role(conn)
        result = _page(
            conn,
            DEPARTURE_FIELDS,
            DEPARTURE_FROM,
            [
                "d.supplier_org_id=warehouse_org_id()",
                ROUTE_SCOPE,
                DEPARTURE_SCOPE,
                "(:query='' OR strpos(lower(d.code||' '||p.code||' '||p.effective_name),lower(:query))>0)",
                "(CAST(:product AS uuid) IS NULL OR d.product_id=:product)",
                "(CAST(:source AS uuid) IS NULL OR d.connection_id=:source)",
                "(CAST(:start AS date) IS NULL OR d.depart_date>=:start)",
                "(CAST(:end AS date) IS NULL OR d.depart_date<=:end)",
            ],
            {
                "query": query[:500],
                "product": product_id,
                "source": connection_id,
                "start": start,
                "end": end,
            },
            page,
            limit,
            "d.depart_date,d.code,d.id",
        )
        result["items"] = [_departure(row) for row in result["items"]]
        return result


def departure(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        _role(conn)
        row = (
            conn.execute(
                text(f"""SELECT {DEPARTURE_FIELDS},d.external_id
            {DEPARTURE_FROM} WHERE d.id=:id AND d.supplier_org_id=warehouse_org_id()
            AND {ROUTE_SCOPE} AND {DEPARTURE_SCOPE}"""),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("团期不存在、不在展示范围或无权限")
        result = _departure(row)
        result["offers"] = [
            dict(item)
            for item in conn.execute(
                text("""SELECT o.id,o.code,o.name,o.active,o.inventory_pool_id,
            cp.schedule,cp.valid_from,cp.valid_until FROM offer o
            LEFT JOIN LATERAL (SELECT schedule,valid_from,valid_until FROM contract_price
              WHERE offer_id=o.id AND layer='standard' AND active AND valid_from<=now() AND valid_until>now()
              ORDER BY valid_from DESC,id LIMIT 1) cp ON true
            WHERE o.departure_id=:id ORDER BY o.code,o.id"""),
                {"id": identifier},
            ).mappings()
        ]
        result["read_at"] = datetime.now(UTC)
        return result
