"""Supplier-owned, read-only parser comparison with explicit reparse queueing."""

from sqlalchemy import text

from . import documents, route_kit_content
from .persistence import require_role, transaction


def _day_nodes(body):
    if not route_kit_content.is_kit(body):
        return body
    # Compare business content by day and named node; evidence and generated IDs
    # are immutable diagnostics, not hundreds of accidental positional edits.
    content = route_kit_content.customer_projection(body)
    days = {}
    for day in content.pop("days"):
        nodes = {}
        for node in day.pop("items"):
            name = node.pop("name")
            node.pop("node_id", None)
            nodes.setdefault(name, []).append(node)
        day.pop("day_id", None)
        day["nodes"] = nodes
        days[str(day["day"])] = day
    content["days"] = days
    content.pop("cover_asset_id", None)
    return content


def differences(before, after):
    if route_kit_content.is_kit(after):
        before, after = _day_nodes(before), _day_nodes(after)
    left = dict(documents._leaves(before))
    right = dict(documents._leaves(after))
    return [
        {"path": p, "before": left.get(p), "after": right.get(p)}
        for p in sorted(left.keys() | right.keys())
        if not p.startswith(("/source/parsed_at", "/quality/reviewed_"))
        and left.get(p) != right.get(p)
    ]


def run(engine, actor, store, parser, *, apply=False, limit=100, after=None):
    limit = max(1, min(limit, 100))
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        rows = [
            dict(row)
            for row in conn.execute(
                text("""SELECT a.*,p.id AS parse_id,p.body AS parsed_body
          FROM document_asset a JOIN document_parse p ON p.asset_id=a.id AND p.generation=a.parse_generation
          WHERE a.supplier_org_id=warehouse_org_id() AND a.status='parsed'
            AND NOT EXISTS(SELECT 1 FROM document_asset newer WHERE newer.product_id=a.product_id
              AND (newer.created_at,newer.id)>(a.created_at,a.id))
            AND (CAST(:after AS uuid) IS NULL OR a.id>:after)
          ORDER BY a.id LIMIT :limit"""),
                {"after": after, "limit": max(1, min(limit, 100)) + 1},
            ).mappings()
        ]
    results = []
    for work in rows[:limit]:
        body = store.read(actor.organization_id, work["id"], work["file_hash"])
        parsed = parser(work, body)
        if isinstance(parsed, documents.ParsedDocument):
            parsed = parsed.document
        with transaction(engine, actor) as conn:
            require_role(conn, "supplier_admin", "product_editor")
            live = documents._asset(conn, work["id"])
            if (
                live["parse_generation"] != work["parse_generation"]
                or live["file_hash"] != work["file_hash"]
            ):
                raise documents.Conflict("解析对比期间来源已变化，请重新读取")
        result = {
            "asset_id": str(work["id"]),
            "parse_id": str(work["parse_id"]),
            "parser_before": work["parsed_body"]["source"]["parser"],
            "parser_after": parsed.source.parser,
            "differences": differences(
                work["parsed_body"], parsed.model_dump(mode="json", by_alias=True)
            ),
            "queued": False,
        }
        if apply:
            documents.reparse(
                engine,
                actor,
                work["id"],
                documents.ReparseRequest(
                    expected_parse_id=work["parse_id"],
                    note="操作员明确执行解析器升级对比与重新解析；保留人工稿和已发布版本",
                ),
            )
            result["queued"] = True
        results.append(result)
    return {
        "items": results,
        "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        "published": False,
    }
