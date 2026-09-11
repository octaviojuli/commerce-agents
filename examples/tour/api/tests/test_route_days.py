# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_route_days``, the 逐日行程 card: every day of one 线路 as the agency's document
writes it, the flights out and back, and the terms under them. The documents are the invented
ones in ``test_catalog.py``; nothing here asks the ERP, because a line's days are not its
团期."""

import pytest

from commerce_common.skills import SkillRegistry
from shopping_agent import (
    Product,
    ShoppingAgentConfig,
    ShoppingSessionContext,
    ShoppingSessionState,
)
from tour.api.mock_erp import MockErpClient
from tour.api.route_days import NO_DOCUMENT, UNSEEN, build_route_days_extension
from tour.api.route_docs import RouteDocStore
from tour.api.tests.test_catalog import DOCS, EUROPE, WHALE
from tour.api.tests.test_tour_backend import CUSTOMER_ID, MOBILE, TODAY, FakeClock
from tour.api.tour_backend import TourBackend, TourToolExecutor

ADVISOR = "demo-user"


@pytest.fixture
def backend(tmp_path) -> TourBackend:
    published = tmp_path / "published"
    published.mkdir(parents=True)
    for doc in DOCS:
        (published / f"{doc.route_id}.json").write_text(doc.model_dump_json(), encoding="utf-8")
    return TourBackend(
        MockErpClient(today=TODAY, now=FakeClock()),
        today=TODAY,
        customer_id=CUSTOMER_ID,
        contact_mobile=MOBILE,
        route_docs=RouteDocStore.load(tmp_path),
    )


@pytest.fixture
def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="rd-1", user_id=ADVISOR)


@pytest.fixture
def state() -> ShoppingSessionState:
    """A session that has been shown the two lines, which is what the card is gated on."""
    seen = ShoppingSessionState()
    seen.remember_products(
        [
            Product(product_id="RT-301", title=EUROPE.name, price=18800.0),
            Product(product_id="RT-202", title=WHALE.name, price=9800.0),
        ]
    )
    return seen


@pytest.fixture
def executor(backend, session, state) -> TourToolExecutor:
    return TourToolExecutor(
        backend=backend,
        config=ShoppingAgentConfig(brand_name="ACME"),
        skills=SkillRegistry([]),
        session=session,
        state=state,
        extensions=[build_route_days_extension()],
    )


def _ui(result):
    return next(event for event in result.events if event.type == "ui").data


def test_the_tool_is_advertised_with_its_input_schema():
    extension = build_route_days_extension()
    assert extension.component == "route_days"
    assert extension.input_schema["required"] == ["route_id"]
    schema = extension.payload_model.model_json_schema()
    assert extension.input_schema["properties"].keys() == schema["properties"].keys()
    assert "逐日行程" in extension.description


async def test_the_card_is_the_whole_itinerary_as_the_document_writes_it(executor):
    result = await executor.execute("present_route_days", {"route_id": "RT-301"})
    assert not result.is_error, result.result_text
    ui = _ui(result)
    assert ui["component"] == "route_days"
    card = ui["payload"]
    assert card["route_id"] == "RT-301" and card["title"] == EUROPE.name
    assert card["route_code"] == "RC301" and card["department"] == "欧洲部"
    assert card["day_count"] == 12 and card["nights"] == 11
    assert card["countries"] == ["德国", "法国", "意大利", "瑞士"]
    assert card["depart_city"] == "上海"
    assert card["reviewed"] is True and card["reviewed_by"] == "产品部 · 林薇"
    assert card["highlights"] == EUROPE.cover.highlights
    assert card["hotel_standard"] == "全程网评4钻酒店"
    # Every day rides, whole: the card is the itinerary and the reply is not.
    assert [day["day"] for day in card["days"]] == list(range(1, 13))
    first = card["days"][0]
    assert first["places"] == ["法兰克福"] and first["overnight"] == "hotel"
    assert first["hotel"]["name"] == "法兰克福酒店" and first["hotel"]["or_similar"] is True
    assert first["meals"]["breakfast"] == {"text": "酒店", "included": True}
    assert first["meals"]["dinner"] == {"text": "自理", "included": False}
    assert first["text"].startswith("全天在法兰克福")
    second = card["days"][1]
    assert second["sights"] == [
        {"name": "卢浮宫", "kind": "景点", "duration": "", "ticket_included": True}
    ]
    assert card["inclusions"] == ["国际机票", "行程所列门票"]
    assert card["exclusions"] == ["单房差"]
    assert [stop["name"] for stop in card["shopping"]] == ["免税店", "皮具中心"]
    assert card["policies"]["single_room"] == "单房差 2400 元/人"
    assert card["attachment_name"] == f"{EUROPE.name}.docx"


async def test_the_flights_are_the_first_day_that_flies_and_the_last(executor):
    card = _ui(await executor.execute("present_route_days", {"route_id": "RT-301"}))["payload"]
    assert card["outbound"] == [
        {
            "day": 1,
            "flight_no": "KA912",
            "carrier": "国泰",
            "from_place": "上海",
            "to_place": "法兰克福",
            "times": "0830-1400",
        }
    ]
    # These documents fly on the first day alone, so there is no return segment to show.
    assert card["inbound"] == []


async def test_the_optional_items_price_is_the_documents_own_words(executor):
    card = _ui(await executor.execute("present_route_days", {"route_id": "RT-202"}))["payload"]
    assert card["optional"] == [{"name": "热气球", "price": "120 美金/人", "day": 4}]


async def test_a_line_this_session_has_not_shown_is_refused(executor):
    result = await executor.execute("present_route_days", {"route_id": "RT-302"})
    assert result.is_error and UNSEEN.format(route_id="RT-302") in result.result_text
    assert not [event for event in result.events if event.type == "ui"]


async def test_a_line_with_no_document_is_refused(executor, state):
    state.remember_products([Product(product_id="RT-1021", title="伊犁北疆环线", price=5580.0)])
    result = await executor.execute("present_route_days", {"route_id": "RT-1021"})
    assert result.is_error and NO_DOCUMENT.format(route_id="RT-1021") in result.result_text
