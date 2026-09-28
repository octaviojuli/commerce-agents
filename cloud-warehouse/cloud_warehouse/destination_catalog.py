"""Source-labelled, rebuildable route destinations; approved tags remain authoritative."""

from . import destinations as geo

# Global search cannot adopt a date/city-specific publication as a general fact.
# The same conditions are shared by the projection and the live fallback.
PUBLICATION = """SELECT pub.body FROM document_publication pub
 WHERE pub.id=p.published_document_id AND pub.product_id=p.id
 AND (pub.content_version>0 OR pub.product_version=p.version)
 AND pub.reviewed_by IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM document_publication other
   WHERE other.product_id=p.id AND other.source_content_hash IS NOT DISTINCT FROM pub.source_content_hash
   AND (NULLIF(other.body#>>'{applicability,start}','') IS NOT NULL
     OR NULLIF(other.body#>>'{applicability,end}','') IS NOT NULL
     OR COALESCE(other.body#>'{applicability,departure_cities}','[]')<>'[]'::jsonb
     OR COALESCE(other.body#>>'{applicability,version_label}','')<>''))"""
# Only named, structured destination fields enter search. Prose, private originals,
# flights, disclaimers and gateway cities are not proof of visited countries.
SOURCE_TEXT = "concat_ws(' ',p.source->>'countries',p.source->>'countryNames',p.source->>'destinationCountries',p.source->>'cities',p.source->>'destinationCities')"
REVIEWED_TEXT = f"""COALESCE((SELECT concat_ws(' ',
 jsonb_path_query_array(doc.body,'$.days[*].countries[*]')::text,
 jsonb_path_query_array(doc.body,'$.days[*].cities[*]')::text)
 FROM ({PUBLICATION}) doc),'')"""


def facts(row):
    countries, cities = {}, {}
    tags = row.get("approved_tags") or []
    excluded = set().union(
        *(
            geo.country_codes(t.get("label", ""))
            for t in tags
            if t.get("category") == "destination" and t.get("state") == "excluded"
        )
    )
    evidence = []
    # Increasing confidence, with explicit manual corrections last.
    evidence.append((row.get("name", ""), "source_title", "candidate"))
    evidence.append((row.get("effective_name", row.get("name", "")), "title", "candidate"))
    raw = row.get("source") or {}
    for key in ("countries", "countryNames", "destinationCountries", "cities", "destinationCities"):
        values = raw.get(key, [])
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            for value in values[:100]:
                if isinstance(value, str):
                    evidence.append((value[:100], "supplier_field", "declared"))
    doc = row.get("destination_document") or {}
    for day in doc.get("days", []):
        for key in ("countries", "cities"):
            for name in day.get(key, []):
                if isinstance(name, str):
                    evidence.append((name, "published_itinerary", "verified"))
    for tag in tags:
        if (
            tag.get("category") == "destination"
            and tag.get("state") == "confirmed"
            and tag.get("search_enabled")
        ):
            evidence.append((tag["label"], "merchant_tag", "confirmed"))
    for value, origin, status in evidence:
        for _, kind, key in geo.mentions(value):
            codes = geo.GROUPS[key] if kind == "group" else (key,) if kind == "country" else ()
            if kind == "city":
                code, parent, _ = geo.CITIES[key]
                cities[code] = {
                    "code": code,
                    "name": key,
                    "country_code": parent,
                    "origin": origin,
                    "status": status,
                }
                codes = (parent,)
            for code in codes:
                if code not in excluded:
                    countries[code] = {
                        "code": code,
                        "name": geo.COUNTRIES[code][0],
                        "origin": origin,
                        "status": status,
                    }
    return {
        "countries": [countries[k] for k in sorted(countries)],
        "cities": [cities[k] for k in sorted(cities) if cities[k]["country_code"] not in excluded],
        "regions": [
            name for name, codes in geo.REGIONS.items() if set(countries).intersection(codes)
        ],
        "rule_version": geo.VERSION,
        "product_version": row["version"],
        "display_version": row["display_version"],
        "publication_id": str(row["published_document_id"])
        if row.get("published_document_id")
        else None,
        "content_version": row["content_version"],
    }


# Keep departure gateways and marketing prose out of destination coverage matching.
DOCUMENT = (
    f"lower(concat_ws(' ',p.effective_name,p.name,p.tags_search,{SOURCE_TEXT},{REVIEWED_TEXT}))"
)
