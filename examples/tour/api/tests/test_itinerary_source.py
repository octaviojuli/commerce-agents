# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Reading a 线路's 行程附件: the two layouts the agency's product staff actually write, the
documents that name no days at all, the fetch that refuses anything but a .docx it has not
already got, and the cache that keeps one reading per 线路. The .docx files here are built by
``docx`` out of the same three parts a Word document has, so the suite needs no attachment and
no server."""

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from tour.api.erp_client import Itinerary, ItineraryDay
from tour.api.itinerary_source import (
    DOCX_TYPE,
    cache_read,
    cache_write,
    day_number,
    fetch_attachment,
    parse_docx,
)

URL = "https://files.example/acme/xingcheng.docx"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/word/document.xml" ContentType="{DOCX_TYPE}"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Target="word/document.xml" Type="http://schemas.openxmlformats.org'
    '/officeDocument/2006/relationships/officeDocument"/>'
    "</Relationships>"
)
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def table(rows: list[list[str | list[str]]]) -> str:
    """One table; a cell is one string or a list of the paragraphs inside it."""
    body = ""
    for row in rows:
        cells = ""
        for cell in row:
            texts = cell if isinstance(cell, list) else [cell]
            cells += "<w:tc>" + "".join(paragraph(text) for text in texts) + "</w:tc>"
        body += f"<w:tr>{cells}</w:tr>"
    return f"<w:tbl>{body}</w:tbl>"


def docx(*blocks: str) -> bytes:
    """A .docx holding those body blocks: the two package parts Word needs to open it, and
    ``word/document.xml``."""
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{"".join(blocks)}</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


# The first layout: one table whose rows are the day header, 用餐, 住宿 and the programme, then
# the terms as paragraphs after it. The header carries the 参考航班 on the same line.
LAYOUT_ONE = docx(
    table(
        [
            ["第 1 天 上海-科伦坡/尼甘布  参考航班：ACM8801 21:05-01:25+1"],
            ["用餐", "早：自理", "中：自理", "晚：机上"],
            ["住宿", "尼甘布 · 海风椰林酒店", "交通：飞机+旅游用车"],
            ["指定时间于机场集合，领队发放登机牌与行程册，搭乘航班飞往科伦坡。"],
            ["抵达后专车前往尼甘布，办理入住，行李由司机送至房间。"],
            ["第 2 天 尼甘布-品纳瓦拉-康提"],
            ["用餐", "早：酒店", "中：中式合菜", "晚：酒店"],
            ["住宿", "康提 · 山岚湖景酒店", "交通：旅游用车"],
            ["上午前往品纳瓦拉的大象孤儿院，观看象群下河洗澡。"],
            ["第 3 天 康提-科伦坡"],
            ["用餐", "早：酒店", "中：自理", "晚：机上"],
            ["住宿", "全程无", "交通：旅游用车+飞机"],
            ["上午参观康提湖，下午返回科伦坡机场，搭乘航班返回上海。"],
        ]
    ),
    paragraph("包含项目"),
    paragraph("国际机票、当地四星酒店、行程所列用餐与门票。"),
    paragraph("不包含项目"),
    paragraph("签证费、个人消费。"),
    paragraph("旅行团须知"),
    paragraph("请于起飞前三小时抵达机场。"),
)

# The second layout: an English overview table above a Chinese detail table whose programme,
# 餐饮 and 住宿 sit in the column beside an empty marker cell; the terms follow as tables.
LAYOUT_TWO = docx(
    table(
        [
            ["DAY-1", "上海/重庆", "/", "/"],
            ["DAY-2", "重庆/慕尼黑", "/", "/"],
            ["DAY-12", "上海", "/", "/"],
        ]
    ),
    table(
        [
            ["第一天", "上海/重庆"],
            ["", "指定时间于机场集合，搭乘国内航班飞往重庆，抵达后入住机场周边酒店。"],
            ["", "餐饮：早餐：自理 午餐：自理 晚餐：自理"],
            ["", "住宿：重庆机场周边商务酒店"],
            ["第二天", "重庆/慕尼黑"],
            ["", ["上午办理登机与出境手续，搭乘国际航班前往慕尼黑。", "抵达后专车前往小镇入住。"]],
            ["", "餐饮：早餐：机餐 午餐：机餐 晚餐：机餐"],
            ["", "住宿：德国小镇或周边"],
            ["第十二天", "上海"],
            ["", "航班抵达上海，行程结束，感谢您选择 ACME 旅行社。"],
            ["", "餐饮：早餐：机餐 午餐：自理 晚餐：自理"],
            ["", "住宿：温馨的家"],
        ]
    ),
    table([["预 定 须 知"], ["报名后请于 24 小时内支付定金。"]]),
    table([["另行付费旅游项目的说明"], ["当地自费项目由客人自愿选择。"]]),
)

# The overview table alone: no 第N天 anywhere, so the English markers are the day headers.
ENGLISH_ONLY = docx(
    table([["DAY-1", "上海/重庆"], ["DAY-2", "重庆/慕尼黑"], ["D-3", "慕尼黑/因斯布鲁克"]])
)

# A document that names no day at all: a covering note the editors uploaded by mistake.
NO_MARKERS = docx(
    paragraph("ACME 旅行社 2026 年欧洲产品说明"),
    paragraph("本文件仅供同行参考，具体行程以出团通知为准。"),
    table([["线路代码", "YLBJ"], ["销售部门", "新疆部"]]),
)


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("1", 1),
        ("１２", 12),
        ("一", 1),
        ("九", 9),
        ("十", 10),
        ("十一", 11),
        ("十二", 12),
        ("二十", 20),
        ("二十一", 21),
        ("零", 0),
    ],
)
def test_a_day_header_reads_arabic_full_width_and_chinese_numerals(text, number):
    assert day_number(text) == number


def test_the_one_table_layout_reads_its_days_its_hotels_and_its_meals():
    days = parse_docx(LAYOUT_ONE)
    assert [day.day_no for day in days] == [1, 2, 3]
    assert [day.title for day in days] == [
        "上海-科伦坡/尼甘布",
        "尼甘布-品纳瓦拉-康提",
        "康提-科伦坡",
    ]
    # The 住宿 row's 交通 belongs to the row and not to the night's hotel.
    assert [day.hotel for day in days] == ["尼甘布 · 海风椰林酒店", "康提 · 山岚湖景酒店", "全程无"]
    assert days[0].meals == "早：自理 中：自理 晚：机上"


def test_the_flight_on_a_header_line_is_the_days_text_and_not_its_title():
    day = parse_docx(LAYOUT_ONE)[0]
    assert "参考航班" not in day.title
    assert day.text.startswith("参考航班：ACM8801 21:05-01:25+1")
    assert "领队发放登机牌" in day.text


def test_the_terms_after_the_last_day_are_not_part_of_it():
    last = parse_docx(LAYOUT_ONE)[-1]
    assert "搭乘航班返回上海" in last.text
    assert "包含项目" not in last.text
    assert "旅行团须知" not in last.text


def test_the_two_table_layout_ignores_the_english_overview_and_reads_the_detail_table():
    days = parse_docx(LAYOUT_TWO)
    # Three days, not the six the overview rows and the detail rows would make together.
    assert [day.day_no for day in days] == [1, 2, 12]
    assert [day.title for day in days] == ["上海/重庆", "重庆/慕尼黑", "上海"]
    assert [day.hotel for day in days] == ["重庆机场周边商务酒店", "德国小镇或周边", "温馨的家"]
    assert days[1].meals == "早餐：机餐 午餐：机餐 晚餐：机餐"
    # The paragraphs inside one cell stay in the day they were written in.
    assert "抵达后专车前往小镇入住。" in days[1].text
    assert "预 定 须 知" not in days[2].text
    assert "另行付费" not in days[2].text


def test_the_english_overview_is_read_only_where_no_chinese_header_exists():
    days = parse_docx(ENGLISH_ONLY)
    assert [day.day_no for day in days] == [1, 2, 3]
    assert [day.title for day in days] == ["上海/重庆", "重庆/慕尼黑", "慕尼黑/因斯布鲁克"]


def test_an_attachment_that_names_no_day_names_no_itinerary():
    assert parse_docx(NO_MARKERS) == []


@pytest.mark.parametrize("data", [b"", b"not a .docx at all", b"PK\x03\x04broken"])
def test_an_unreadable_attachment_is_a_value_error_the_caller_turns_into_no_itinerary(data):
    with pytest.raises(ValueError):
        parse_docx(data)


def test_a_zip_without_a_word_document_is_unreadable_too():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a document")
    with pytest.raises(ValueError):
        parse_docx(buffer.getvalue())


# -- the fetch ---------------------------------------------------------------------------


def wire(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def docx_response(body: bytes, *, etag: str | None = None, **headers) -> httpx.Response:
    sent = {"content-type": DOCX_TYPE, **headers}
    if etag:
        sent["etag"] = etag
    return httpx.Response(200, content=body, headers=sent)


async def test_the_fetch_carries_the_etag_it_has_and_answers_the_one_it_got():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return docx_response(LAYOUT_ONE, etag='"v3"')

    fetched = await fetch_attachment(URL, etag='"v2"', transport=wire(handler))
    assert fetched is not None
    assert fetched[0] == LAYOUT_ONE
    assert fetched[1] == '"v3"'
    assert seen[0].headers["If-None-Match"] == '"v2"'


async def test_an_unchanged_attachment_is_nothing_to_read_rather_than_an_empty_one():
    fetched = await fetch_attachment(
        URL, etag='"v2"', transport=wire(lambda request: httpx.Response(304))
    )
    assert fetched is None


async def test_an_attachment_that_is_not_a_docx_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>login</html>", headers={"content-type": "text/html"}
        )

    assert (
        await fetch_attachment("https://files.example/a/login", etag=None, transport=wire(handler))
        is None
    )
    # The same body under a .docx URL is read: the agency's file server states octet-stream.
    assert await fetch_attachment(URL, etag=None, transport=wire(handler)) is not None


async def test_an_attachment_over_the_ceiling_is_refused_by_its_length_and_by_its_body():
    stated = wire(lambda request: docx_response(b"x" * 10, **{"content-length": "9000000"}))
    assert await fetch_attachment(URL, etag=None, transport=stated, max_bytes=100) is None
    silent = wire(lambda request: docx_response(b"x" * 500))
    assert await fetch_attachment(URL, etag=None, transport=silent, max_bytes=100) is None


async def test_an_attachment_the_file_server_does_not_answer_for_is_nothing_to_read():
    missing = wire(lambda request: httpx.Response(404, text="not found"))
    assert await fetch_attachment(URL, etag=None, transport=missing) is None

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    assert await fetch_attachment(URL, etag=None, transport=wire(dead)) is None


# -- the cache ---------------------------------------------------------------------------


ITINERARY = Itinerary(
    route_id=1021,
    source="attachment",
    source_ref='"v3"',
    days=(
        ItineraryDay(1, "乌鲁木齐集合", "全天接站入住。", "乌鲁木齐 · 云杉里酒店", "早：不含"),
        ItineraryDay(2, "赛里木湖", "环湖一圈。", None, None),
    ),
)


def test_a_cached_reading_comes_back_as_it_went_in(tmp_path: Path):
    cache_write(tmp_path, ITINERARY, URL, '"v3"')
    read = cache_read(tmp_path, 1021, URL)
    assert read is not None
    assert read == (ITINERARY, '"v3"')


def test_a_cache_file_is_readable_by_its_owner_alone(tmp_path: Path):
    cache_write(tmp_path, ITINERARY, URL, '"v3"')
    path = tmp_path / "itineraries" / "1021.json"
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_reading_of_another_attachment_is_stale(tmp_path: Path):
    cache_write(tmp_path, ITINERARY, URL, '"v3"')
    assert cache_read(tmp_path, 1021, "https://files.example/acme/other.docx") is None


def test_a_route_with_no_cache_file_and_an_unreadable_one_read_as_nothing(tmp_path: Path):
    assert cache_read(tmp_path, 1021, URL) is None
    path = tmp_path / "itineraries" / "1021.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert cache_read(tmp_path, 1021, URL) is None
