"""Versioned catalog search; live RLS and current facts always decide visibility."""

from sqlalchemy import text

from . import destination_catalog, destinations, route_search_facts
from .integrations import canonical

# All consumers use aliases p (product_listing) and s (product_search).
DOCUMENT = (
    "lower(p.effective_name||' '||p.name||' '||p.code||' '||COALESCE(p.gateway,'')||' '||p.tags_search||' '||"
    + destination_catalog.SOURCE_TEXT
    + "||' '||"
    + destination_catalog.REVIEWED_TEXT
    + ")"
)
JOIN = "LEFT JOIN product_search s ON s.product_id=p.id"
CURRENT = f"s.product_version=p.version AND s.display_version=p.display_version AND s.content_version=p.content_version AND s.publication_id IS NOT DISTINCT FROM p.published_document_id AND s.destination_rule_version={destinations.VERSION} AND s.content_rule_version={route_search_facts.VERSION}"
MATCH = f"""(:query='' OR (CASE WHEN {CURRENT} THEN s.document ELSE {DOCUMENT} END)
    LIKE :pattern ESCAPE '!')"""


def parameters(query):
    query = query[:500].strip()
    pattern = "%" + query.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    return {"query": query, "pattern": pattern}


def rebuild(conn):
    """Refresh only changed rows, in the caller's scoped transaction."""
    rows = (
        conn.execute(
            text(f"""SELECT p.id,p.supplier_org_id,p.version,p.display_version,p.content_version,
      p.published_document_id,p.name,p.effective_name,p.source,p.approved_tags,{DOCUMENT} AS document,
      {destination_catalog.DOCUMENT} AS destination_document_text,
      ({destination_catalog.PUBLICATION}) AS destination_document
      FROM product_listing p {JOIN} WHERE p.supplier_org_id=warehouse_org_id()
      AND (s.product_id IS NULL OR NOT ({CURRENT}) OR s.document IS DISTINCT FROM {DOCUMENT})""")
        )
        .mappings()
        .all()
    )
    for row in rows:
        conn.execute(
            text("""INSERT INTO product_search AS s
          (product_id,supplier_org_id,product_version,display_version,document,destination_document,destination_facts,destination_rule_version,content_version,publication_id,content_facts,content_rule_version)
          VALUES(:id,:supplier_org_id,:version,:display_version,:document,:destination_document_text,CAST(:facts AS jsonb),:rule,:content_version,:published_document_id,CAST(:content_facts AS jsonb),:content_rule)
          ON CONFLICT(product_id) DO UPDATE SET product_version=excluded.product_version,
            display_version=excluded.display_version,document=excluded.document,destination_document=excluded.destination_document,destination_facts=excluded.destination_facts,
            destination_rule_version=excluded.destination_rule_version,content_version=excluded.content_version,
            publication_id=excluded.publication_id,content_facts=excluded.content_facts,content_rule_version=excluded.content_rule_version,updated_at=now()"""),
            {
                **row,
                "facts": canonical(destination_catalog.facts(row)),
                "rule": destinations.VERSION,
                "content_facts": canonical(route_search_facts.derive(row["destination_document"])),
                "content_rule": route_search_facts.VERSION,
            },
        )
    return len(rows)


def destination_facts(conn, identifiers):
    """Read current projection or the same live facts; product RLS still applies."""
    rows = conn.execute(
        text(f"""SELECT p.id,p.version,p.display_version,p.content_version,
      p.published_document_id,p.name,p.effective_name,p.source,p.approved_tags,
      CASE WHEN {CURRENT} THEN s.destination_facts END AS stored,
      CASE WHEN {CURRENT} THEN NULL ELSE ({destination_catalog.PUBLICATION}) END AS destination_document
      FROM product_listing p {JOIN} WHERE p.id=ANY(CAST(:ids AS uuid[]))"""),
        {"ids": list(identifiers)},
    ).mappings()
    return {row["id"]: row["stored"] or destination_catalog.facts(row) for row in rows}
