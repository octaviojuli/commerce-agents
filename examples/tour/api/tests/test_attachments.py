# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The 行程附件 as the workbench downloads it: resolved from a 线路 or one of its 团期 on the
advisor's session, read from the agency's store, and answered under the name the agency gave
the file."""

import httpx
import pytest

from commerce_common.skills import SkillRegistry
from demo_common import SESSION_HEADER
from tour.api import attachments
from tour.api.main import ATTACHMENT_TOO_LARGE, ATTACHMENT_UNAVAILABLE, NO_ATTACHMENT
from tour.api.tour_backend import TourToolExecutor

NAME = "【一价全含·纯玩+观鲸】东航斯里兰卡7天5晚5钻.docx"
URL = "https://store.example/attachments/2026/09/10/6aa20bcd6093c.docx"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
BODY = b"PK\x03\x04 not really a docx"


@pytest.fixture
def linked(main):
    """RT-1021 links an attachment for the test and unlinks it after: the fixtures carry none."""
    row = main.backend.erp._routes[1021]
    row["routeAttachmentName"], row["routeAttachmentUrl"] = NAME, URL
    main.backend._routes.pop(1021, None)
    yield
    row["routeAttachmentName"] = row["routeAttachmentUrl"] = None
    main.backend._routes.pop(1021, None)


@pytest.fixture
def store(main, monkeypatch):
    """The store behind the URL, as a mock wire; the test says what it answers."""
    answers: dict[str, httpx.Response] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        return answers.get(str(request.url), httpx.Response(404))

    monkeypatch.setattr(main, "attachment_transport", httpx.MockTransport(handle))
    return answers


def _headers(client) -> dict[str, str]:
    return {SESSION_HEADER: client.post("/api/session", json={}).json()["session_id"]}


def test_the_disposition_carries_the_agencys_name_in_utf8_with_an_ascii_fallback():
    header = attachments.content_disposition(NAME)
    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''%E3%80%90" in header
    # The fallback is ASCII only, and keeps the extension.
    fallback = header.split('filename="')[1].split('"')[0]
    assert fallback.isascii() and fallback.endswith(".docx")


def test_the_media_type_is_the_stores_or_the_names():
    assert attachments.media_type("x.docx", DOCX) == DOCX
    assert attachments.media_type("x.pdf", None) == "application/pdf"
    assert attachments.media_type("x.pdf", "application/octet-stream") == "application/pdf"
    assert attachments.media_type("x.bin", None) == "application/octet-stream"


async def test_the_attachment_resolves_from_the_route_and_from_its_departure(main, linked, session):
    assert await main.backend.attachment(session, "RT-1021") == (NAME, URL)
    assert await main.backend.attachment(session, "DP-3008") == (NAME, URL)
    assert await main.backend.attachment(session, "RT-1022") is None
    assert await main.backend.attachment(session, "not-an-id") is None


def test_the_download_is_the_file_under_the_agencys_name(client, linked, store):
    store[URL] = httpx.Response(200, content=BODY, headers={"content-type": DOCX})
    response = client.get("/api/attachments/RT-1021", headers=_headers(client))
    assert response.status_code == 200
    assert response.content == BODY
    assert response.headers["content-type"].startswith(DOCX)
    assert "filename*=UTF-8''" in response.headers["content-disposition"]


def test_a_route_with_no_attachment_is_a_404(client):
    response = client.get("/api/attachments/RT-1022", headers=_headers(client))
    assert response.status_code == 404 and response.json()["detail"] == NO_ATTACHMENT


def test_a_store_that_does_not_answer_is_a_502_and_a_huge_file_a_413(client, linked, store):
    headers = _headers(client)
    store[URL] = httpx.Response(500)
    response = client.get("/api/attachments/RT-1021", headers=headers)
    assert response.status_code == 502 and response.json()["detail"] == ATTACHMENT_UNAVAILABLE
    store[URL] = httpx.Response(
        200,
        content=BODY,
        headers={"content-type": DOCX, "content-length": str(attachments.MAX_ATTACHMENT_BYTES + 1)},
    )
    response = client.get("/api/attachments/RT-1021", headers=headers)
    assert response.status_code == 413 and response.json()["detail"] == ATTACHMENT_TOO_LARGE


def test_the_download_needs_a_session(client):
    assert client.get("/api/attachments/RT-1021").status_code in (401, 403, 422)


async def test_the_route_record_names_its_attachment(main, linked, session):
    from shopping_agent import SearchFilters

    products = await main.backend.search_products(
        session, "伊犁", SearchFilters(attributes={"destination": "伊犁"})
    )
    by_id = {p.product_id: p for p in products}
    assert by_id["RT-1021"].attributes["attachment"] == NAME
    assert "attachment" not in by_id["RT-1022"].attributes


@pytest.fixture
def executor(main, backend, session, state) -> TourToolExecutor:
    return TourToolExecutor(
        backend=backend,
        config=main.agent.config,
        skills=SkillRegistry([]),
        session=session,
        state=state,
        extensions=list(main.agent.extra_presentation_tools),
    )


@pytest.fixture
def linked_on_backend(backend):
    """The test backend's own mock ERP links an attachment on RT-1021 and RT-1024."""
    for route_id, name in ((1021, NAME), (1024, "伊犁五钻轻奢8日-行程单.pdf")):
        row = backend.erp._routes[route_id]
        row["routeAttachmentName"], row["routeAttachmentUrl"] = name, URL
    yield
    for route_id in (1021, 1024):
        row = backend.erp._routes[route_id]
        row["routeAttachmentName"] = row["routeAttachmentUrl"] = None


def _ui(result):
    return next(event for event in result.events if event.type == "ui").data


def test_the_tool_is_advertised_with_its_input_schema(main):
    extension = attachments.build_attachments_extension()
    assert extension.component == "attachments"
    tool = next(t for t in main.agent._tools if t["name"] == "present_attachments")
    assert tool["input_schema"] == extension.input_schema
    assert (
        "only when the advisor asks" in tool["description"].lower()
        or "Use only when" in tool["description"]
    )


async def test_the_card_lists_the_documents_of_the_lines_the_advisor_asked_for(
    executor, linked_on_backend
):
    """The ids are the model's; each file's name is the server's, joined from the record the
    session saw. A line with no document and an id never seen are named as dropped."""
    await executor.execute("search_products", {"query": "伊犁", "limit": 8})
    result = await executor.execute(
        "present_attachments",
        {"product_ids": ["RT-1021", "RT-1024", "RT-1022", "RT-999999"], "note": "两条线的行程单"},
    )
    assert not result.is_error, result.result_text
    ui = _ui(result)
    assert ui["component"] == "attachments"
    card = ui["payload"]
    assert card["note"] == "两条线的行程单"
    assert [(i["product_id"], i["extension"]) for i in card["items"]] == [
        ("RT-1021", "docx"),
        ("RT-1024", "pdf"),
    ]
    assert card["items"][0]["name"] == NAME and card["items"][0]["title"]
    assert "RT-1022" in result.result_text and "RT-999999" in result.result_text


async def test_a_card_with_nothing_on_it_is_refused(executor):
    await executor.execute("search_products", {"query": "伊犁", "limit": 8})
    result = await executor.execute("present_attachments", {"product_ids": ["RT-1022"]})
    assert result.is_error and attachments.NOTHING_TO_SEND in result.result_text
    unseen = await executor.execute("present_attachments", {"product_ids": ["RT-999999"]})
    assert unseen.is_error
