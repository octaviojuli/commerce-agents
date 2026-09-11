# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The 线路 documents the runtime reads: loaded from the state directory, reviewed over draft,
and read by the backend for the 行程, the card's 规格, the attributes the model sees, and the
纯玩 and hotel filters — ahead of the tags, which only guess at those."""

from datetime import UTC, datetime

import pytest

from shopping_agent import SearchFilters, ShoppingSessionContext
from tour.api.mock_erp import MockErpClient
from tour.api.route_doc import (
    Cover,
    Day,
    Flight,
    Hotel,
    Meal,
    Meals,
    Policies,
    Quality,
    RouteDoc,
    ShoppingStop,
    Source,
    Summary,
)
from tour.api.route_docs import RouteDocStore, hotel_grade, itinerary_of, meals_included
from tour.api.tests.test_tour_backend import TODAY, FakeClock, build

ROUTE = 1021  # 伊犁北疆环线 8 日纯玩小团; its tags say 纯玩无购物 and 四钻
YILI = {"destination": "伊犁", "depart_from": "2026-10-11", "depart_to": "2026-10-20"}


def _doc(route_id: int = ROUTE, *, reviewed: bool = False, shopping: int = 1) -> RouteDoc:
    meal = lambda included, text="": Meal(text=text, included=included)  # noqa: E731
    return RouteDoc(
        route_id=route_id,
        route_code="YLBJ",
        name="伊犁北疆环线 8 日纯玩小团",
        department="新疆部",
        summary=Summary(days=8, nights=7, depart_city="乌鲁木齐", region="伊犁"),
        cover=Cover(hotel_standard="全程网评5钻酒店", airline="优选东航"),
        transport=[
            Flight(
                day=1,
                flight_no="MU5601",
                from_place="上海",
                to_place="乌鲁木齐",
                times="0800-1330",
                raw="",
            )
        ],
        days=[
            Day(
                day=1,
                title="上海-乌鲁木齐",
                overnight="hotel",
                hotel=Hotel(name="友好大酒店", or_similar=True),
                meals=Meals(
                    breakfast=meal(False, "X"), lunch=meal(False, "X"), dinner=meal(True, "酒店内")
                ),
                text="抵达后入住。",
            ),
            Day(
                day=2,
                title="乌鲁木齐-伊宁",
                overnight="hotel",
                hotel=Hotel(name="伊宁宾馆"),
                meals=Meals(
                    breakfast=meal(True, "酒店内"),
                    lunch=meal(True, "特色餐"),
                    dinner=meal(True, "含"),
                ),
                text="游览【赛里木湖】。",
            ),
        ],
        inclusions=["往返机票", "首道门票"],
        exclusions=["单房差 800 元/人"],
        policies=Policies(single_room="单房差 800 元/人"),
        shopping=[ShoppingStop(name="特产超市", day=2)] * shopping,
        source=Source(
            attachment_name="伊犁.docx",
            attachment_url="https://files.example/a.docx",
            bytes=10,
            parsed_at=datetime(2026, 9, 11, tzinfo=UTC),
            parser="docx-rules-1",
        ),
        quality=Quality(completeness=1.0, reviewed_by="产品部 王" if reviewed else ""),
    )


def _write(root, folder: str, doc: RouteDoc) -> None:
    (root / folder).mkdir(parents=True, exist_ok=True)
    (root / folder / f"{doc.route_id}.json").write_text(doc.model_dump_json(), encoding="utf-8")


@pytest.fixture
def session():
    return ShoppingSessionContext(session_id="rd-1", user_id="demo-user")


def test_the_store_reads_published_over_selected_and_skips_what_is_not_a_document(tmp_path):
    _write(tmp_path, "selected", _doc())
    _write(tmp_path, "published", _doc(reviewed=True, shopping=0))
    _write(tmp_path, "selected", _doc(route_id=1022))
    (tmp_path / "selected" / "selected.json").write_text("[]", encoding="utf-8")
    (tmp_path / "selected" / "1023.json").write_text("{not a document}", encoding="utf-8")
    store = RouteDocStore.load(tmp_path)
    assert len(store) == 2
    assert store.reviewed(ROUTE) and not store.reviewed(1022) and store.get(1023) is None
    assert store.get(ROUTE).shopping == []
    assert len(RouteDocStore.load(tmp_path / "nowhere")) == 0 and len(RouteDocStore.load(None)) == 0


def test_the_documents_days_become_the_itinerary_the_card_and_the_plan_read():
    itinerary = itinerary_of(_doc())
    assert itinerary.source == "attachment" and itinerary.source_ref == "route-doc"
    first, second = itinerary.days
    assert first.hotel == "友好大酒店或同级" and first.meals == "早：X 中：X 晚：酒店内"
    assert second.title == "乌鲁木齐-伊宁" and second.text == "游览【赛里木湖】。"
    assert meals_included(_doc()) == (4, 6) and hotel_grade(_doc()) == "五钻"


async def test_the_backend_reads_the_document_ahead_of_the_attachment(tmp_path, session):
    _write(tmp_path, "selected", _doc())
    backend = build(MockErpClient(today=TODAY, now=FakeClock()))
    backend._route_docs = RouteDocStore.load(tmp_path)
    details = await backend.get_product_details(session, f"RT-{ROUTE}")
    assert details is not None
    specs = details.specs
    assert specs["行程来源"] == "线路文档（解析稿，待复核）"
    assert specs["第1天"].startswith("上海-乌鲁木齐｜抵达后入住。｜住宿：友好大酒店或同级")
    assert specs["参考航班"] == "MU5601 上海-乌鲁木齐 0800-1330"
    assert specs["购物店"] == "特产超市" and specs["费用包含"] == "往返机票；首道门票"
    assert specs["单房差"] == "单房差 800 元/人"
    attrs = details.attributes
    assert (
        attrs["doc"] == "draft"
        and attrs["shopping_stops"] == "1"
        and attrs["meals_included"] == "4/6"
    )
    assert attrs["doc_hotel_grade"] == "五钻" and attrs["flights"].startswith("MU5601")


async def test_a_reviewed_document_says_so(tmp_path, session):
    _write(tmp_path, "published", _doc(reviewed=True))
    backend = build(MockErpClient(today=TODAY, now=FakeClock()))
    backend._route_docs = RouteDocStore.load(tmp_path)
    details = await backend.get_product_details(session, f"RT-{ROUTE}")
    assert (
        details.specs["行程来源"] == "线路文档（已复核）"
        and details.attributes["doc"] == "reviewed"
    )


async def test_the_document_decides_纯玩_and_the_hotel_standard(tmp_path, session):
    """RT-1021's tags claim 纯玩无购物 and 四钻; its document lists one 购物店 and states 五钻,
    and the document is the catalog — the tags filter nothing."""
    _write(tmp_path, "selected", _doc())
    backend = build(MockErpClient(today=TODAY, now=FakeClock()))
    backend._route_docs = RouteDocStore.load(tmp_path)
    pure = await backend.search_products(
        session, "伊犁", SearchFilters(attributes={**YILI, "no_shopping": "yes"})
    )
    assert [p.product_id for p in pure] == []
    selling = await backend.search_products(session, "伊犁", SearchFilters(attributes=dict(YILI)))
    assert [p.product_id for p in selling] == [f"RT-{ROUTE}"]
    assert selling[0].attributes["shopping_stops"] == "1"
    assert selling[0].attributes["doc"] == "draft"
    assert selling[0].labels == ["购物店1家", "五钻", "优选东航", "解析稿"]
    five = await backend.search_products(
        session, "伊犁", SearchFilters(attributes={**YILI, "hotel_level": "五钻"})
    )
    assert [p.product_id for p in five] == [f"RT-{ROUTE}"]
    four = await backend.search_products(
        session, "伊犁", SearchFilters(attributes={**YILI, "hotel_level": "四钻"})
    )
    assert [p.product_id for p in four] == []
