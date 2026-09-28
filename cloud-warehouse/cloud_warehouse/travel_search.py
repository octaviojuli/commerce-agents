"""Canonical destination predicates; live catalog permissions remain authoritative."""

import json
import re

from shopping_agent import NotOffered

from . import destination_catalog, search
from . import destinations as geo

# Compatibility names for integrations that inspect the bounded vocabulary.
PAIRS = {name: tuple(geo.COUNTRIES[c][0] for c in codes) for name, codes in geo.GROUPS.items()}
EUROPE = tuple(geo.COUNTRIES[c][0] for c in geo.EUROPE)
COUNTRIES = {row[0] for row in geo.COUNTRIES.values()}
TOKENS = geo.TOKEN
DOCUMENT = f"(CASE WHEN {search.CURRENT} THEN s.document ELSE {search.DOCUMENT} END)"
GEOGRAPHY = f"(CASE WHEN {search.CURRENT} THEN s.destination_document ELSE {destination_catalog.DOCUMENT} END)"
MATCH = f"""(:query='' OR NOT EXISTS (
 SELECT 1 FROM unnest(CAST(:travel_patterns AS text[]),CAST(:geo_flags AS boolean[]),CAST(:region_flags AS boolean[]),CAST(:term_labels AS text[])) term(pattern,is_geo,is_region,label)
 WHERE (CASE WHEN term.is_geo THEN {GEOGRAPHY} ELSE {DOCUMENT} END) !~ term.pattern
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(p.approved_tags) tag
   WHERE tag->>'category'='destination' AND tag->>'state'='excluded'
     AND (CASE WHEN term.is_region THEN lower(tag->>'label')=term.label ELSE lower(tag->>'label') ~ term.pattern END))
))"""
EXCLUDE = f"""NOT EXISTS(SELECT 1 FROM unnest(CAST(:excluded_patterns AS text[])) term(pattern)
 WHERE {GEOGRAPHY} ~ term.pattern)"""


def parameters(query):
    query = query[:500].strip().lower()
    terms = geo.canonical_terms(query)
    return {
        "query": query,
        "travel_patterns": [geo.pattern(t) for t in terms] or [re.escape(query)],
        "geo_flags": [t.casefold() in geo.ALIASES for t in terms] or [False],
        "region_flags": [t in geo.REGIONS for t in terms] or [False],
        "term_labels": terms or [query],
    }


def exclusions(value):
    try:
        values = json.loads(value)
        if (
            not isinstance(values, list)
            or len(values) > 20
            or any(not isinstance(x, str) or not 1 <= len(x) <= 80 for x in values)
        ):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise NotOffered("排除目的地格式无效") from error
    return {
        "excluded_patterns": [
            geo.pattern(x) for value in values for x in geo.canonical_terms(value)
        ]
    }
