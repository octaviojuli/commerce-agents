"""Departure-aware selection of immutable, human-approved route publications."""

from route_kit.models import Applicability as KitApplicability
from sqlalchemy import text

from .route_doc import Applicability
from .route_kit_content import day_count


def matches(scope, departure_date, city):
    return (
        (not scope.start or departure_date >= scope.start)
        and (not scope.end or departure_date <= scope.end)
        and (not scope.departure_cities or city in scope.departure_cities)
    )


def scoped(body):
    value = (
        KitApplicability if body.get("schema") == "route-kit/1" else Applicability
    ).model_validate(body.get("applicability", {}))
    return bool(value.start or value.end or value.departure_cities or value.version_label)


def select_publication(conn, current, departure_id):
    publications = [
        dict(r)
        for r in conn.execute(
            text("""SELECT d.* FROM document_publication d
      WHERE d.product_id=:product AND d.source_content_hash IS NOT DISTINCT FROM :source
      ORDER BY d.content_version DESC,d.created_at DESC LIMIT 101"""),
            {"product": current["product_id"], "source": current.get("source_content_hash")},
        ).mappings()
    ]
    # Legacy publications without a source hash use only the selected current record.
    if not current.get("source_content_hash"):
        publications = [current]
    if not publications or len(publications) > 100:
        return None
    bounded = publications[:100]
    has_scope = any(scoped(p["body"]) for p in bounded)
    if not departure_id:
        return None if has_scope else current
    departure = (
        conn.execute(
            text("""SELECT d.depart_date,d.return_date,p.gateway FROM departure d
      JOIN supplier_product p ON p.id=d.product_id WHERE d.id=:id AND p.id=:product"""),
            {"id": departure_id, "product": current["product_id"]},
        )
        .mappings()
        .one_or_none()
    )
    if not departure:
        return None
    if len(publications) > 100:
        return None  # Never select from a truncated version history.
    for item in bounded:
        if has_scope and not scoped(item["body"]):
            continue
        scope = (
            KitApplicability if item["body"].get("schema") == "route-kit/1" else Applicability
        ).model_validate(item["body"].get("applicability", {}))
        if not matches(scope, departure["depart_date"], departure["gateway"]):
            continue
        duration = (
            (departure["return_date"] - departure["depart_date"]).days + 1
            if departure["return_date"]
            else None
        )
        if duration is not None and duration != day_count(item["body"]):
            continue
        return item
    return None


def departure_durations(conn, product_id, doc):
    scope = doc.applicability
    rows = conn.execute(
        text("""SELECT d.id,(d.return_date-d.depart_date+1) AS days,d.depart_date,p.gateway
      FROM departure d JOIN supplier_product p ON p.id=d.product_id WHERE p.id=:id AND d.status='published'
      AND (CAST(:start AS date) IS NULL OR d.depart_date>=:start)
      AND (CAST(:end AS date) IS NULL OR d.depart_date<=:end)"""),
        {"id": product_id, "start": scope.start, "end": scope.end},
    ).mappings()
    return [
        (row["id"], row["days"])
        for row in rows
        if not scope.departure_cities or row["gateway"] in scope.departure_cities
    ]
