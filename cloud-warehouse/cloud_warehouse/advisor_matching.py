"""Fixed, parameter-bound ranking with source-bounded pages and indexed fact reads."""

import base64
import json
import re
from datetime import date
from uuid import UUID

from sqlalchemy import text

from shopping_agent import NotOffered

from . import destination_catalog, destinations, search, travel_search
from .integrations import fingerprint

# Published documents only. Scoped versions require a chosen departure and remain unknown.
DOCUMENT = f"LEFT JOIN LATERAL (SELECT body FROM ({destination_catalog.PUBLICATION}) published WHERE NOT COALESCE(({search.CURRENT}),false)) doc ON true"
SHOPPING = """CASE WHEN doc.body IS NULL THEN 'unknown'
 WHEN COALESCE(doc.body->'shopping','[]')<>'[]'::jsonb
 OR jsonb_path_exists(doc.body, '$.days[*].sights[*] ? (@.kind == "购物")')
 OR jsonb_path_exists(doc.body, '$.days[*].blocks[*] ? (@.type == "shopping")')
 OR jsonb_path_exists(doc.body, '$.days[*].items[*] ? (@.type == "shopping")')
 THEN 'conflict'
 WHEN doc.body->>'schema'='route-kit/1' AND COALESCE(doc.body->>'shopping_status','unknown')<>'none' THEN 'unknown'
 ELSE 'ok' END"""


def query_sql(query, attrs, after):
    try:
        start = date.fromisoformat(attrs["depart_from"]) if attrs.get("depart_from") else None
        end = date.fromisoformat(attrs["depart_to"]) if attrs.get("depart_to") else None
        low = int(attrs.get("days_min", attrs.get("days", "1")))
        high = int(attrs.get("days_max", attrs.get("days", "365")))
    except ValueError as error:
        raise NotOffered("日期或天数范围无效") from error
    if not 1 <= low <= high <= 365 or (start and end and start > end):
        raise NotOffered("日期或天数范围无效")
    dates = ["d.product_id=p.id", "d.status='published'"]
    if start:
        dates.append("d.depart_date>=:start")
    if end:
        dates.append("d.depart_date<=:end")
    predicates = ["p.status='published'"]
    if any(key in attrs for key in ("days", "days_min", "days_max")):
        predicates += ["p.effective_days BETWEEN :low AND :high"]
    params = {
        **travel_search.parameters(query),
        "city": attrs.get("depart_city", ""),
        "start": start,
        "end": end,
        "low": low,
        "high": high,
        "after": after,
    }
    try:
        examples = json.loads(attrs.get("destination_examples", "[]"))
        if (
            not isinstance(examples, list)
            or len(examples) > 20
            or any(not isinstance(x, str) or not 1 <= len(x) <= 80 for x in examples)
        ):
            raise ValueError
    except (ValueError, TypeError) as error:
        raise NotOffered("举例目的地格式无效") from error
    params["example_patterns"] = [
        ".*".join(travel_search.parameters(x)["travel_patterns"]) for x in examples
    ]
    example_score = "(SELECT count(*)::integer FROM unnest(CAST(:example_patterns AS text[])) e(pattern) WHERE lower(concat_ws(' ',p.effective_name,p.effective_description)) ~ e.pattern)"
    params.update(travel_search.exclusions(attrs.get("excluded_destinations", "[]")))
    join = (
        search.JOIN
        if params["query"] or params["excluded_patterns"] or attrs.get("no_shopping") == "true"
        else ""
    )
    if params["excluded_patterns"]:
        predicates.append(travel_search.EXCLUDE)
    if start or end:
        predicates.append("next_departure.next_date IS NOT NULL")
    if params["query"]:
        predicates.append(travel_search.MATCH)
    city = (
        "CASE WHEN p.effective_gateway IS NULL OR p.effective_gateway='' THEN 'unknown' WHEN p.effective_gateway=:city THEN 'ok' ELSE 'conflict' END"
        if params["city"]
        else "'ok'::text"
    )
    shopping = (
        f"CASE WHEN {search.CURRENT} THEN COALESCE(s.content_facts->>'shopping','unknown') ELSE {SHOPPING} END"
        if attrs.get("no_shopping") == "true"
        else "'ok'::text"
    )
    document = DOCUMENT if attrs.get("no_shopping") == "true" else ""
    window = (
        "CASE WHEN next_date IS NULL THEN 'conflict' ELSE 'ok' END"
        if start or end
        else "'ok'::text"
    )
    cursor_clause = ""
    if after:
        try:
            anchor = json.loads(base64.urlsafe_b64decode(str(after).encode()))
            if anchor["scope"] != fingerprint({"query": query, "attrs": attrs}):
                raise ValueError
            params.update(
                cursor_imperfect=int(anchor["imperfect"]),
                cursor_conflicts=int(anchor["conflicts"]),
                cursor_examples=int(anchor["examples"]),
                cursor_date=date.fromisoformat(anchor["date"]),
                cursor_id=UUID(anchor["id"]),
            )
        except (ValueError, KeyError, TypeError) as error:
            raise NotOffered("检索条件已变化或游标无效，请从第一页重新查询") from error
        cursor_clause = "AND (s.imperfect,s.conflicts,s.example_rank,s.date_rank,s.id) > (:cursor_imperfect,:cursor_conflicts,:cursor_examples,:cursor_date,:cursor_id)"
    # LIMIT is applied only after each source uses the same global ranking/cursor.
    # A source's (N+1)th ranked item cannot be in the global first N.
    sql = f"""WITH facts AS NOT MATERIALIZED (
      SELECT p.id,p.connection_id,p.supplier_org_id,p.effective_name AS name,p.effective_days AS days,p.effective_gateway AS gateway,
        p.effective_description AS description,p.name_origin,p.description_origin,p.version,
        next_departure.next_date,-{example_score} AS example_rank,{city} AS city_match,{shopping} AS shopping_match
      FROM product_listing p {join} {document}
      LEFT JOIN LATERAL (SELECT d.depart_date AS next_date FROM departure d
        WHERE {" AND ".join(dates)} ORDER BY d.depart_date,d.id LIMIT 1) next_departure ON true
      WHERE {" AND ".join(predicates)}
    ), scored AS NOT MATERIALIZED (
      SELECT *,{window} AS window_match,
        (city_match='conflict')::int+(shopping_match='conflict')::int+({window}='conflict')::int AS conflicts,
        CASE WHEN city_match='ok' AND shopping_match='ok' AND {window}='ok' THEN 0 ELSE 1 END AS imperfect,
        COALESCE(next_date,'9999-12-31'::date) AS date_rank FROM facts
    )
    SELECT candidate.*,c.name AS source_name FROM supplier_connection c CROSS JOIN LATERAL (
      SELECT * FROM scored s WHERE s.connection_id=c.id
      {cursor_clause}
      ORDER BY s.imperfect,s.conflicts,s.example_rank,s.date_rank,s.id LIMIT :limit
    ) candidate ORDER BY candidate.imperfect,candidate.conflicts,candidate.example_rank,candidate.date_rank,candidate.id LIMIT :limit"""
    return text(sql), params


def reasons(row, attrs, query="", facts=None):
    values = []
    required = destinations.country_codes(query, cities=True)
    if required:
        known = {c["code"]: c for c in (facts or {}).get("countries", [])}
        certain = all(known.get(c, {}).get("status") in ("verified", "confirmed") for c in required)
        names = "、".join(destinations.COUNTRIES[c][0] for c in sorted(required))
        values.append(
            {
                "criterion": "destinations",
                "verdict": "ok" if certain else "unknown",
                "text": f"{names}："
                + ("已复核行程或商户确认覆盖" if certain else "目录信息匹配，具体行程覆盖待核对"),
            }
        )
    if attrs.get("depart_from") or attrs.get("depart_to"):
        values.append(
            {
                "criterion": "window",
                "verdict": row["window_match"],
                "text": "需求月份内有已发布团期，能否报名待核实"
                if row["next_date"]
                else "窗口内暂无已发布团期",
            }
        )
    if any(key in attrs for key in ("days", "days_min", "days_max")):
        values.append(
            {"criterion": "days", "verdict": "ok", "text": f"{row['days']} 天，在需求范围内"}
        )
    if attrs.get("depart_city"):
        values.append(
            {
                "criterion": "depart_city",
                "verdict": row["city_match"],
                "text": f"{row['gateway'] or '出发地未知'}，需求为 {attrs['depart_city']}",
            }
        )
    if attrs.get("no_shopping") == "true":
        values.append(
            {
                "criterion": "no_shopping",
                "verdict": row["shopping_match"],
                "text": {
                    "ok": "已复核行程：无购物店",
                    "conflict": "已复核行程含购物店，与需求冲突",
                    "unknown": "暂无适用的已复核稿，购物情况待确认",
                }[row["shopping_match"]],
            }
        )
    description = (str(row["name"]) + " " + str(row["description"] or "")).lower()
    for example in json.loads(attrs.get("destination_examples", "[]")):
        mentioned = all(
            re.search(pattern, description)
            for pattern in travel_search.parameters(example)["travel_patterns"]
        )
        values.append(
            {
                "criterion": "example:" + example,
                "verdict": "ok" if mentioned else "unknown",
                "text": f"举例 {example}："
                + ("目录已提及，详细覆盖待行程核对" if mentioned else "目录未提及，是否覆盖待确认"),
            }
        )
    return values


def cursor(row, query, attrs):
    payload = {
        "scope": fingerprint({"query": query, "attrs": attrs}),
        "imperfect": row["imperfect"],
        "conflicts": row["conflicts"],
        "examples": row["example_rank"],
        "date": str(row["date_rank"]),
        "id": str(row["id"]),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
