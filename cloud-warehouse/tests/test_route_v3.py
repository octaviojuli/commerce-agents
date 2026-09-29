"""Fictional evidence checks independent of provider/model availability."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cloud_warehouse import route_consistency, route_content, route_extraction
from cloud_warehouse.route_doc import Applicability, Money, ReviewResolution, RouteDoc
from tour.api.erp_client import RouteRecord
from tour.api.route_parser import parse_route

FIXTURES = Path(__file__).parents[2] / "examples/tour/api/tests/fixtures/route_eval"


def parsed(name="bus_no_flights"):
    folder = FIXTURES / name
    spec = json.loads((folder / "expected.json").read_text())
    return parse_route(RouteRecord(**spec["record"]), (folder / spec["file"]).read_bytes())


def codes(doc):
    return {issue["code"] for issue in route_content.issues(doc)}


def test_v3_native_fields_keep_exact_evidence_and_decimal_units():
    doc = parsed("structured_terms")
    assert doc.schema_version == "3.0"
    assert doc.service_fee == Money(
        amount="120",
        currency="USD",
        unit="person",
        raw="司导服务费：120美元/人。",
        source_lines=doc.service_fee.source_lines,
    )
    assert doc.service_fee.source_lines
    assert doc.source.lines[doc.service_fee.source_lines[0] - 1] == doc.service_fee.raw
    assert doc.cancellation_tiers[0].penalty_percent == 20
    assert doc.traveler_requirements.child.minimum_age == 6
    assert doc.traveler_requirements.insurance.included is False
    assert doc.meeting.time == "09:00"
    assert doc.policies.cancellation.startswith("退团政策")


def test_old_documents_validate_without_claiming_new_fields():
    body = parsed().model_dump(mode="json")
    body["schema_version"] = "1.0"
    for key in ("applicability", "formation", "service_fee", "cancellation_tiers", "meeting"):
        body.pop(key)
    old = RouteDoc.model_validate(body)
    assert old.schema_version == "1.0" and old.service_fee.amount is None


def test_customer_allowlist_excludes_nested_evidence():
    doc = parsed("structured_terms")
    doc.service_fee.raw = "PRIVATE_EVIDENCE"
    doc.days[0].hotel.raw = "PRIVATE_EVIDENCE"
    doc.source.lines = ["PRIVATE_EVIDENCE"]
    doc.quality.needs_review = ["PRIVATE_EVIDENCE"]
    projected = route_content.customer_projection(doc)
    assert "PRIVATE_EVIDENCE" not in json.dumps(projected)
    assert "source_lines" not in json.dumps(projected)
    assert projected["service_fee"] == {"amount": "120", "currency": "USD", "unit": "person"}


def test_long_advisor_document_provides_section_read_instructions_inside_tool_fence():
    from cloud_warehouse.advisor import WarehouseAdvisorBackend
    from shopping_agent.fencing import STOREFRONT_FENCE
    from shopping_agent.serialization import product_details_payload
    from shopping_agent.types import ProductDetails

    doc = parsed("structured_terms")
    doc.days = [doc.days[0].model_copy(deep=True) for _ in range(30)]
    doc.source.lines = ["PRIVATE_EVIDENCE"]
    specs = WarehouseAdvisorBackend._document_specs(
        {"id": "ACME", "product_version": 1, "body": doc.model_dump(mode="json")}
    )
    payload = product_details_payload(
        ProductDetails(product_id="ACME", title="ACME", price=None, specs=specs)
    )
    visible = STOREFRONT_FENCE.fence_payload(payload)
    assert "read_route_itinerary" in visible and "未显示部分不可据此比较或承诺" in visible
    assert "PRIVATE_EVIDENCE" not in visible


def test_variant_and_day_failures_require_real_content_corrections():
    doc = parsed("season_variants")
    assert len(doc.source.variants) == 2
    assert "VARIANTS_DETECTED" in codes(doc)
    doc.applicability = Applicability(version_label=doc.source.variants[0].label)
    assert "VARIANTS_DETECTED" not in codes(doc)
    assert "DAYS_INCOMPLETE" in codes(parsed("missing_day"))
    assert "DAYS_INCOMPLETE" in codes(parsed("duplicate_day"))


def test_image_acknowledgement_is_content_bound_and_never_automatic():
    doc = parsed("image_cover")
    image = next(i for i in route_content.issues(doc) if i["code"] == "IMAGE_TEXT_UNREAD")
    assert image["pages"] == [1] and image["acknowledgeable"]
    doc.quality.resolutions = [
        ReviewResolution(
            code=image["code"],
            path=image["path"],
            basis_hash=image["basis_hash"],
            note="ACME 已对照图片确认",
        )
    ]
    assert "IMAGE_TEXT_UNREAD" not in codes(doc)
    doc.days[0].title = "ACME changed"
    assert "IMAGE_TEXT_UNREAD" in codes(doc)


def test_cross_field_conflicts_are_locatable():
    doc = parsed()
    doc.inclusions = ["ACME 门票"]
    doc.exclusions = ["ACME 门票"]
    doc.cover.hotel_standard = "全程五星"
    doc.days[0].hotel.grade = "四星"
    doc.days[0].overnight = "flight"
    doc.summary.nights = 3
    issues = route_consistency.checks(doc)
    assert {
        "FEE_CONFLICT",
        "HOTEL_GRADE_CONFLICT",
        "OVERNIGHT_FLIGHT_MISSING",
        "NIGHTS_MISMATCH",
    } <= {i["code"] for i in issues}
    assert all(i["path"] for i in issues)
    assert "MEAL_OVERVIEW_CONFLICT" in codes(parsed("overview_conflict"))


def test_flight_local_times_and_offset_preserve_reference_text():
    flight = parsed("overnight_flight").transport[0]
    assert (flight.departure_local_time, flight.arrival_local_time, flight.arrival_day_offset) == (
        "23:00",
        "01:30",
        1,
    )
    assert flight.times == "2300-0130+1"


def test_cited_model_only_fills_missing_and_never_overrides_native():
    doc = parsed()
    doc.source.lines += ["司导服务费：120美元/人。"]
    proposed = doc.model_copy(deep=True)
    proposed.service_fee.amount = Decimal("120")
    proposed.service_fee.currency = "USD"
    proposed.service_fee.unit = "person"
    refs = {
        f"/service_fee/{key}": [len(doc.source.lines)] for key in ("amount", "currency", "unit")
    }
    candidate = route_extraction.Candidate(document=proposed, citations=refs)
    merged, fields, report = route_extraction.merge(doc, candidate)
    assert merged.service_fee.amount == 120 and len(fields) == 3
    assert all(item["source_lines"] == [len(doc.source.lines)] for item in fields.values())
    doc.service_fee.amount = Decimal("100")
    merged, fields, report = route_extraction.merge(doc, candidate)
    assert merged.service_fee.amount == 100 and "/service_fee/amount" in report["conflicts"]


def test_cited_model_rejects_unknown_lines_unmentioned_values_and_wrong_days():
    doc = parsed()
    proposed = doc.model_copy(deep=True)
    proposed.service_fee.amount = Decimal("999")
    proposed.days[0].vehicle_type = "巴士"
    candidate = route_extraction.Candidate(
        document=proposed,
        citations={
            "/service_fee/amount": [1],
            "/days/0/vehicle_type": [9],
            "/service_fee/unit": [999],
        },
    )
    merged, fields, report = route_extraction.merge(doc, candidate)
    assert merged.service_fee.amount is None and merged.days[0].vehicle_type is None
    assert not fields
    assert "/service_fee/amount" in report["discarded"]


def test_unavailable_model_preserves_native_result():
    def fail(*args):
        raise ValueError("private provider error")

    doc = parsed()
    result, fields, report = route_extraction.extract(doc, fail)
    assert result == doc and not fields and report["code"] == "EXTRACTION_MODEL_UNAVAILABLE"


def test_extraction_requests_only_bounded_missing_fields_and_keeps_day_positions():
    doc = parsed("structured_terms")

    def answer(prompt, data, schema):
        assert len(data["missing_fields"]) <= 160
        assert "/service_fee/amount" not in data["missing_fields"]
        assert not any(path.endswith("/text") for path in data["missing_fields"])
        assert [d["day"] for d in data["output_template"]["days"]] == [d.day for d in doc.days]
        assert all(not d["title"] for d in data["output_template"]["days"])
        return schema(document=RouteDoc.model_validate(data["output_template"]), citations={})

    result, fields, report = route_extraction.extract(doc, answer)
    assert result == doc and not fields and report["status"] == "merged"


def test_truncated_model_output_has_specific_code_and_retains_original():
    def fail(*args):
        raise ValueError("EDITOR_RESPONSE_SHAPE")

    doc = parsed()
    result, fields, report = route_extraction.extract(doc, fail)
    assert result == doc and not fields and report["code"] == "EXTRACTION_OUTPUT_INCOMPLETE"


def test_invalid_money_and_date_ranges_rejected():
    with pytest.raises(ValueError):
        Money(amount="-1")
    with pytest.raises(ValueError):
        Applicability(start=date(2026, 6, 1), end=date(2026, 5, 1))


@pytest.mark.parametrize(
    "path",
    [
        "days[0].vehicle_type",
        "/days/0/vehicle_type",
        "document.days/0/vehicle_type",
        "/document/days/0/vehicle_type",
    ],
)
def test_citation_notation_and_unfamiliar_vehicle_are_source_checked(path):
    doc = parsed("unfamiliar_vehicle")
    proposed = doc.model_copy(deep=True)
    proposed.days[0].vehicle_type = "小巴"
    line = next(i for i, value in enumerate(doc.source.lines, 1) if "用车方式" in value)
    result, fields, _ = route_extraction.merge(
        doc,
        route_extraction.Candidate(document=proposed, citations={path: [line]}),
    )
    assert result.days[0].vehicle_type == "小巴"
    assert fields["/days/0/vehicle_type"]["source_lines"] == [line]


def test_opposite_ticket_wording_and_wrong_currency_are_not_supported():
    assert not route_extraction._supported(
        "/days/0/sights/0/ticket_included", True, "ACME 不包含门票"
    )
    assert not route_extraction._supported("/service_fee/currency", "CNY", "120美元/人")
    assert route_extraction._supported("/service_fee/currency", "USD", "120美元/人")


def test_models_cannot_merge_facts_across_variants():
    doc = parsed("season_variants")

    def never(*args):
        raise AssertionError("multi-version source must not be sent")

    same, fields, report = route_extraction.extract(doc, never)
    assert same == doc and not fields and report["code"] == "EXTRACTION_VARIANTS_REVIEW"
