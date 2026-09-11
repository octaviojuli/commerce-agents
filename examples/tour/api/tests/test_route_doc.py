# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``RouteDoc`` is the contract the agency's product staff and the review tooling read: the
JSON Schema in ``data/route-schema.json`` is the model's own, a document round-trips through
JSON, and a field the schema does not name is refused."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tour.api.route_doc import (
    SCHEMA_VERSION,
    Day,
    Quality,
    RouteDoc,
    Source,
    Summary,
    json_schema,
)
from tour.api.tour_backend import DATA_DIR


def _doc() -> RouteDoc:
    return RouteDoc(
        route_id=1021,
        route_code="YLBJ",
        name="伊犁北疆环线 8 日纯玩小团",
        department="新疆部",
        summary=Summary(
            days=8, nights=7, depart_city="乌鲁木齐", countries=["伊犁"], region="伊犁"
        ),
        days=[Day(day=1, title="乌鲁木齐集合", overnight="hotel")],
        source=Source(
            attachment_name="伊犁北疆环线.docx",
            attachment_url="https://files.example/a.docx",
            bytes=1024,
            parsed_at=datetime(2026, 9, 11, tzinfo=UTC),
            parser="docx-rules-1",
        ),
        quality=Quality(completeness=0.5, needs_review=["没有读到参考航班"]),
    )


def test_the_schema_file_is_the_models_own():
    """``scripts/parse_attachments.py --schema`` writes it; this holds the file to the model."""
    on_disk = json.loads((DATA_DIR / "route-schema.json").read_text(encoding="utf-8"))
    assert on_disk == json_schema()
    assert on_disk["title"] == "RouteDoc"
    assert SCHEMA_VERSION in on_disk["$comment"]


def test_a_document_round_trips_through_json():
    doc = _doc()
    again = RouteDoc.model_validate_json(doc.model_dump_json())
    assert again == doc
    assert again.schema_version == SCHEMA_VERSION


def test_a_field_the_schema_does_not_name_is_refused():
    with pytest.raises(ValidationError):
        RouteDoc.model_validate({**_doc().model_dump(mode="json"), "price": 1})
    with pytest.raises(ValidationError):
        Day(day=1, title="x", overnight="boat")
