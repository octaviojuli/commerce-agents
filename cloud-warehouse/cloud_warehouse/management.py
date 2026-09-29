"""Supplier workbench reads; credentials and raw upstream bodies never enter summaries."""

from sqlalchemy import text

from .changes import Conflict
from .persistence import Forbidden, require_role, transaction


def label_changes(conn, items, authentication):
    """Attach readable, scoped labels without changing the immutable approved payload."""
    targets = set()
    for item in items:
        payload = item["payload"]
        targets.update(
            str(payload[key])
            for key in (
                "target_id",
                "batch_id",
                "offer_id",
                "verification_id",
                "connection_id",
                "departure_id",
            )
            if payload.get(key)
        )
        targets.update(
            str(diff["target"]) for diff in payload.get("items", []) if diff.get("target")
        )
    if not targets:
        return
    labels = dict(
        conn.execute(
            text("""SELECT id::text,effective_name AS name FROM product_listing
      WHERE supplier_org_id=warehouse_org_id() AND id::text=ANY(:ids)
      UNION ALL SELECT d.id::text,p.effective_name || ' · ' || d.code || ' · ' || d.depart_date::text
      FROM departure d JOIN product_listing p ON p.id=d.product_id
      WHERE d.supplier_org_id=warehouse_org_id() AND d.id::text=ANY(:ids)
      UNION ALL SELECT i.id::text,p.effective_name || ' · ' || d.code || ' · ' || d.depart_date::text
      FROM inventory_pool i JOIN departure d ON d.id=i.departure_id JOIN product_listing p ON p.id=d.product_id
      WHERE i.supplier_org_id=warehouse_org_id() AND i.id::text=ANY(:ids)
      UNION ALL SELECT id::text,file_name FROM import_batch WHERE id::text=ANY(:ids)
      UNION ALL SELECT o.id::text,p.effective_name || ' · ' || d.code || ' · ' || d.depart_date::text || ' · ' || o.name || ' · ' || c.name
      FROM offer o JOIN departure d ON d.id=o.departure_id JOIN product_listing p ON p.id=d.product_id
      JOIN supplier_connection c ON c.id=o.connection_id
      WHERE o.supplier_org_id=warehouse_org_id() AND o.id::text=ANY(:ids)
      UNION ALL SELECT id::text,name FROM supplier_connection
      WHERE supplier_org_id=warehouse_org_id() AND id::text=ANY(:ids)
      UNION ALL SELECT v.id::text,v.customer_name || '（' || v.customer_code || '） · ' || c.name
      FROM customer_verification v JOIN supplier_connection c ON c.id=v.connection_id WHERE v.id::text=ANY(:ids)"""),
            {"ids": list(targets)},
        ).all()
    )
    for item in items:
        payload = item["payload"]
        own = {str(diff["target"]) for diff in payload.get("items", []) if diff.get("target")}
        item["target_names"] = {key: labels[key] for key in own if key in labels}
        target = (
            payload.get("target_id")
            or payload.get("batch_id")
            or payload.get("departure_id")
            or payload.get("offer_id")
            or payload.get("verification_id")
            or payload.get("connection_id")
        )
        item["target_label"] = labels.get(str(target)) if target else None
        if item["kind"] == "document":
            source = (
                conn.execute(
                    text("""SELECT a.id AS asset_id,a.file_name,a.file_hash,a.product_version,p.id AS parse_id,p.parser_version
                FROM document_parse p JOIN document_asset a ON a.id=p.asset_id
                WHERE p.id::text=:parse AND p.product_id::text=:target AND p.supplier_org_id=warehouse_org_id()"""),
                    {"parse": payload.get("parse_id"), "target": payload.get("target_id")},
                )
                .mappings()
                .one_or_none()
            )
            item["document_source"] = dict(source) if source else None
        if payload.get("reverses_id"):
            movement = (
                conn.execute(
                    text(
                        "SELECT business_key,delta_total,delta_sold,delta_blocked,reason FROM inventory_movement WHERE id::text=:id"
                    ),
                    {"id": payload["reverses_id"]},
                )
                .mappings()
                .one_or_none()
            )
            item["reversed_movement"] = dict(movement) if movement else None
    # Buyer names are resolved only from the supplier's persisted grant relationships.
    buyers = list(
        conn.execute(
            text(
                "SELECT DISTINCT buyer_org_id FROM distribution_grant WHERE supplier_org_id=warehouse_org_id()"
            )
        ).scalars()
    )
    names = organization_names(authentication, buyers)
    for item in items:
        item["buyer_name"] = names.get(str(item["payload"].get("buyer_org_id")))
        identifiers = {
            str(command["body"]["buyer_org_id"])
            for command in item["payload"].get("commands", [])
            if command.get("body", {}).get("buyer_org_id")
        }
        item["buyer_names"] = {
            identifier: names[identifier] for identifier in identifiers if identifier in names
        }


def organization_names(authentication, identifiers):
    if not identifiers:
        return {}
    with authentication.connect() as conn:
        return {
            str(row.id): row.name
            for row in conn.execute(
                text("SELECT id,name FROM organization WHERE id=ANY(:ids)"), {"ids": identifiers}
            )
        }


def offers(engine, actor, *, query="", after=None, limit=25):
    limit = max(1, min(limit, 100))
    data = rows(
        engine,
        actor,
        """SELECT o.id,o.name,o.connection_id,o.departure_id,o.version,
      d.code AS departure_code,d.depart_date,p.effective_name AS product_name,c.connector_type,c.name AS source_name
      FROM offer o JOIN departure d ON d.id=o.departure_id JOIN product_listing p ON p.id=d.product_id
      JOIN supplier_connection c ON c.id=o.connection_id
      WHERE o.supplier_org_id=warehouse_org_id() AND o.active AND c.active
        AND (CAST(:after AS uuid) IS NULL OR o.id>:after)
        AND (:query='' OR strpos(lower(p.effective_name||' '||d.code),lower(:query))>0)
      ORDER BY o.id LIMIT :limit""",
        {"after": after, "query": query[:500], "limit": limit + 1},
    )
    return {
        "items": data[:limit],
        "next_cursor": str(data[limit - 1]["id"]) if len(data) > limit else None,
    }


def contract(engine, actor, offer_id, buyer_org_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        offer = (
            conn.execute(
                text("""SELECT o.id,o.connection_id,c.connector_type FROM offer o
          JOIN supplier_connection c ON c.id=o.connection_id
          WHERE o.id=:id AND o.supplier_org_id=warehouse_org_id() AND o.active AND c.active"""),
                {"id": offer_id},
            )
            .mappings()
            .one_or_none()
        )
        if not offer:
            from .persistence import Forbidden

            raise Forbidden("报价方案不存在或无权限")
        from .pricing import _grant

        grant = _grant(conn, offer["connection_id"], buyer_org_id)
        saved = (
            conn.execute(
                text("""SELECT id,version,schedule,source_ref,valid_from,valid_until,active
          FROM contract_price WHERE offer_id=:offer AND buyer_org_id=:buyer
          ORDER BY (active AND valid_from<=now() AND valid_until>now()) DESC,valid_from DESC,id LIMIT 1"""),
                {"offer": offer_id, "buyer": buyer_org_id},
            )
            .mappings()
            .one_or_none()
        )
        return {
            "offer_id": offer_id,
            "buyer_org_id": buyer_org_id,
            "connector_type": offer["connector_type"],
            "grant_snapshot": dict(grant),
            "contract": dict(saved) if saved else None,
        }


def rows(engine, actor, query, params=None):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "inventory_manager", "auditor")
        return [dict(row) for row in conn.execute(text(query), params or {}).mappings()]


def overview(engine, actor):
    return rows(
        engine,
        actor,
        """SELECT
        (SELECT count(*) FROM supplier_product p JOIN supplier_connection c ON c.id=p.connection_id WHERE p.supplier_org_id=warehouse_org_id() AND (NOT c.capabilities ? 'catalog_selection' OR c.capabilities->'catalog_selection'->'route_ids' ? p.external_id)) AS products,
        (SELECT count(*) FROM departure WHERE supplier_org_id=warehouse_org_id() AND status='published') AS departures,
        (SELECT count(*) FROM change_request WHERE status='staged') AS pending_changes,
        (SELECT count(*) FROM inventory_pool WHERE supplier_org_id=warehouse_org_id()) AS managed_pools,
        (SELECT count(*) FROM supplier_connection WHERE supplier_org_id=warehouse_org_id() AND active) AS connections""",
    )[0]


def connections(engine, actor):
    return rows(
        engine,
        actor,
        "SELECT id,name,connector_type,capabilities,active FROM supplier_connection WHERE supplier_org_id=warehouse_org_id() ORDER BY name,id",
    )


def partners(engine, authentication, actor):
    grants = rows(
        engine,
        actor,
        "SELECT id,buyer_org_id,connection_id,active,version,expires_at,"
        "(warehouse_live_grant(id) AND warehouse_org_active(buyer_org_id)) AS valid "
        "FROM distribution_grant WHERE supplier_org_id=warehouse_org_id() ORDER BY buyer_org_id,connection_id",
    )
    ids = list({row["buyer_org_id"] for row in grants})
    if ids:
        # IDs are derived from this supplier's grants, never supplied as an arbitrary
        # organization-directory query by a browser or model.
        with authentication.connect() as conn:
            names = dict(
                conn.execute(
                    text("SELECT id,name FROM organization WHERE id=ANY(:ids)"), {"ids": ids}
                ).all()
            )
        for grant in grants:
            grant["buyer_name"] = names.get(grant["buyer_org_id"], "未知采购组织")
    return grants


def _history(engine, actor, kind, *, before, limit, status=None, authentication=None):
    if not 1 <= limit <= 100:
        raise Conflict("分页大小须为 1 至 100")
    # Both projections exclude private file bytes and source credentials. The table
    # and projection come from this fixed map, never a request parameter.
    table, projection = {
        "imports": (
            "import_batch",
            "id,connection_id,file_name,status,created_at,jsonb_array_length(rows) AS row_count,errors,published_change_id",
        ),
        "changes": (
            "change_request",
            "id,kind,payload,payload_hash,expected_version,status,created_by,created_at,result",
        ),
    }[kind]
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "inventory_manager", "product_editor", "auditor")
        anchor = None
        if before:
            # A previously visible anchor may have been approved/discarded meanwhile.
            # Validate its organization, but not its mutable status, to keep paging valid.
            anchor = conn.execute(
                text(f"SELECT created_at FROM {table} WHERE id=:id"), {"id": before}
            ).scalar_one_or_none()
            if anchor is None:
                raise Forbidden("分页位置不存在或无权限")
        data = [
            dict(row)
            for row in conn.execute(
                text(f"""SELECT {projection} FROM {table}
                WHERE (CAST(:status AS text) IS NULL OR status=:status)
                  AND (CAST(:before AS uuid) IS NULL OR (created_at,id)<(:created,:before))
                ORDER BY created_at DESC,id DESC LIMIT :limit"""),
                {"status": status, "before": before, "created": anchor, "limit": limit + 1},
            ).mappings()
        ]
        items = data[:limit]
        if kind == "changes":
            label_changes(conn, items, authentication)
        return {"items": items, "next_cursor": str(items[-1]["id"]) if len(data) > limit else None}


def imports(engine, actor, *, before=None, limit=25):
    return _history(engine, actor, "imports", before=before, limit=limit)


def changes(engine, authentication, actor, *, before=None, limit=25, status=None):
    if status not in {None, "staged", "applied", "discarded"}:
        raise Conflict("无效的审批状态")
    return _history(
        engine,
        actor,
        "changes",
        before=before,
        limit=limit,
        status=status,
        authentication=authentication,
    )


def pools(engine, actor, *, after=None, limit=50):
    limit = max(1, min(limit, 100))
    data = rows(
        engine,
        actor,
        """SELECT i.id,i.departure_id,i.version,i.total,i.sold,i.blocked,i.held,
      i.total-i.sold-i.blocked-i.held AS available,d.code,d.depart_date,p.effective_name AS product_name
      FROM inventory_pool i JOIN departure d ON d.id=i.departure_id JOIN product_listing p ON p.id=d.product_id
      WHERE i.supplier_org_id=warehouse_org_id() AND (CAST(:after AS uuid) IS NULL OR i.id>:after)
      ORDER BY i.id LIMIT :limit""",
        {"after": after, "limit": limit + 1},
    )
    return {
        "items": data[:limit],
        "next_cursor": str(data[limit - 1]["id"]) if len(data) > limit else None,
    }
