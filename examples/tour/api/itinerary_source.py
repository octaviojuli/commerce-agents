# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The 行程附件 as an ``Itinerary``: fetching the .docx a 线路 links, reading its days out of
it, and keeping the reading on disk so the next conversation costs no download. This is the
host's own work and not the ERP seam's — the agency's API carries no itinerary endpoint yet
(``docs/erp-contract.md`` asks for one), and until it does the attachment is the only place a
线路's day-by-day 行程 is written down.

An attachment is a Word document the agency's product staff wrote, so it is read as evidence
and never as a schema. Two layouts are in the production catalog and both are handled: one
whole table whose rows are `第 1 天 …`, `用餐 || 早：… || 中：… || 晚：…`, `住宿 || … ||
交通：…` and then the programme; and an English overview table (`DAY-1 || … `) followed by a
detail table whose rows are `第一天 || …` with the programme in an unlabelled cell under it.
A document the reader finds no day marker in yields no days at all, which the backend states
to the advisor as 行程来源：无 rather than guessing.

Nothing here reaches the model unfenced: the days become specs on a details record, which the
executor fences like every other third-party text."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import httpx

from .erp_client import Itinerary, ItineraryDay
from .pdf_source import PDF_MAGIC, PDF_TYPE, pdf_lines, strip_private_use

log = logging.getLogger(__name__)

# What a .docx and a .pdf are, on the wire and in a URL; anything else is not read at all.
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ATTACHMENT_TYPES = {DOCX_TYPE, PDF_TYPE}
ATTACHMENT_SUFFIXES = (".docx", ".pdf")
FETCH_TIMEOUT = 8.0
# The ceiling on one attachment. An attachment is mostly the photographs in it: over 60 of the
# production catalog's the median is 1.0 MB and the largest 12.4 MB, and a third of them are
# over 5 MB, so a lower ceiling refuses a third of the catalog. The download is not the cost —
# the largest took 0.7 s — and it is made once per 线路 per state directory.
MAX_ATTACHMENT_BYTES = 20_000_000
# The version of the cache file's own layout; a file written under another is not read.
CACHE_VERSION = 1

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# The cells of one table row, and the paragraphs inside one cell, as they are joined into a
# line. Both are visible in the parsed text, because a row's shape is what tells 住宿 from
# 交通 in the first layout.
CELL_SEPARATOR = " || "
PARAGRAPH_SEPARATOR = " / "

# A day header: `第 1 天`, `第一天`, `第十二天`, in half-width or full-width digits.
_DAY_CN = re.compile(r"^\s*第\s*([0-9０-９一二三四五六七八九十]+)\s*天")
# The English overview some attachments carry above the Chinese detail table. It is read only
# when the document has no 第N天 header anywhere: where both are present the detail table is
# the itinerary and the overview is a table of contents.
_DAY_EN = re.compile(r"^\s*(?:DAY|D)\s*-?\s*(\d+)\b", re.IGNORECASE)
# Where the itinerary stops and the terms begin. Matched only inside a day, so a document that
# opens with its terms is not read as having none.
_SECTION = re.compile(
    r"^(包含项目|不包含项目|费用包含|费用不含|预\s*定\s*须\s*知|服\s*务\s*所\s*包\s*含"
    r"|旅行团须知|另行付费|购物|自费|温馨提示|注意事项)"
)
# A 温馨提示 or 注意事项 is a note inside a day as often as the head of the terms: a .pdf
# reading writes one on a line of its own in the middle of the itinerary. It ends the days
# only where no day header follows it; the 费用/包含 family ends them wherever it stands.
_SOFT_SECTIONS = ("温馨提示", "注意事项")
# The 住宿 and 用餐 labels of the .docx rows, and the 酒店：/餐食：/住：/餐： of the .pdf ones.
# A label is written with a space between it and its colon as often as without (``餐饮 ：早餐：
# 酒店内``), and is the same label either way.
_HOTEL = re.compile(r"^(?:住宿[：:\s]*|(?:酒店|住)\s*[：:]\s*)")
_MEALS = re.compile(r"^(?:用餐[：:\s]*|(?:餐饮|餐食|餐)\s*[：:]\s*)")
# The 内陆交通 the first layout writes into the 住宿 row; it is not the night's hotel.
_TRANSPORT = re.compile(r"^交通\s*[：:]")
_SPACES = re.compile(r"[ \t　]+")

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def day_number(text: str) -> int:
    """The day a header names, as an int: `1`, `１`, `一`, `十`, `十二`, `二十一`. `0` is
    nothing readable, which is what makes a line not a header."""
    text = unicodedata.normalize("NFKC", text).strip()
    if text.isdigit():
        return int(text)
    if "十" not in text:
        return _CN_DIGITS.get(text, 0)
    tens, _, ones = text.partition("十")
    return (_CN_DIGITS.get(tens, 0) if tens else 1) * 10 + (_CN_DIGITS.get(ones, 0) if ones else 0)


def _text_of(node: ET.Element) -> str:
    return "".join(run.text or "" for run in node.iter(f"{_W}t"))


def _row_line(row: ET.Element) -> str:
    """One table row as one line: the cells joined by ``||``, the paragraphs inside a cell by
    ``/``. A row keeps its inner cell boundaries, because that is what tells a 住宿 row from the
    交通 written beside it; the empty cells at either end go, because the second layout writes
    a day's programme into the column beside an empty marker cell."""
    cells = []
    for cell in row.findall(f"{_W}tc"):
        paragraphs = (_text_of(p).strip() for p in cell.findall(f"{_W}p"))
        cells.append(PARAGRAPH_SEPARATOR.join(part for part in paragraphs if part))
    while cells and not cells[0].strip():
        cells.pop(0)
    while cells and not cells[-1].strip():
        cells.pop()
    return CELL_SEPARATOR.join(cells)


def document_lines(data: bytes) -> list[str]:
    """The attachment's body as lines in document order: one per paragraph, one per table row
    of a .docx, and the same shape out of a .pdf through ``pdf_source``. Raises ``ValueError``
    for anything that is not readable, which the caller states as no itinerary rather than as
    an error."""
    if data.startswith(PDF_MAGIC):
        return pdf_lines(data)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read("word/document.xml")
        body = ET.fromstring(document).find(f"{_W}body")
    except (zipfile.BadZipFile, KeyError, EOFError, RuntimeError, ET.ParseError) as error:
        raise ValueError(f"unreadable .docx attachment: {error}") from error
    if body is None:
        raise ValueError("unreadable .docx attachment: no body")
    lines = []
    for node in body:
        if node.tag == f"{_W}p":
            lines.append(_text_of(node))
        elif node.tag == f"{_W}tbl":
            lines.extend(_row_line(row) for row in node.findall(f"{_W}tr"))
    return [_SPACES.sub(" ", strip_private_use(line)).strip() for line in lines]


def _header(line: str, chinese: bool) -> tuple[int, str] | None:
    """The day number and the rest of the header line, or ``None`` where the line is not one.
    A table row is matched on its first cell, which is where both layouts write the marker."""
    found = (_DAY_CN if chinese else _DAY_EN).match(line)
    if found is None:
        return None
    number = day_number(found[1]) if chinese else int(found[1])
    return (number, line[found.end() :]) if number else None


def _title_and_flight(rest: str) -> tuple[str, str]:
    """A header line's remainder as the day's title, and the 参考航班 that often follows it as
    a line of its own: the flight is the agency's own note about the day and not its name."""
    parts = [part.strip() for part in rest.split(CELL_SEPARATOR)]
    text = " ".join(part for part in parts if part.strip(" /-—、"))
    title, marker, flight = text.partition("参考航班")
    return title.strip(" -—:：/、"), (marker + flight).strip() if marker else ""


def _field(line: str, pattern: re.Pattern[str]) -> str:
    """A 住宿 or 用餐 line without its label — which the first layout writes as a cell of its
    own and the second as a prefix — and without the 交通 written beside it in the same row."""
    parts = [part.strip() for part in line.split(CELL_SEPARATOR)]
    parts[0] = pattern.sub("", parts[0]).strip()
    return " ".join(part for part in parts if part and not _TRANSPORT.match(part))


class _Day:
    def __init__(self, day_no: int, title: str, flight: str) -> None:
        self.day_no, self.title = day_no, title
        self.lines = [flight] if flight else []
        self.hotel: str | None = None
        self.meals: str | None = None

    def add(self, line: str) -> None:
        """A 住宿 or 用餐 row is the field whole; a row whose cells carry their own labels
        (``交通：旅游用车 || 酒店：Goldi Sands``, ``餐：/ || 住：飞机上 || 行：无``) gives each
        cell to its field and the rest to the text."""
        cells = [c.strip() for c in line.split(CELL_SEPARATOR)]
        labelled = any(
            _HOTEL.match(c) or _MEALS.match(c) or c.startswith(("行：", "行:")) for c in cells[1:]
        )
        if not labelled and self.hotel is None and _HOTEL.match(line):
            self.hotel = _field(line, _HOTEL) or None
            return
        if not labelled and self.meals is None and _MEALS.match(line):
            self.meals = _field(line, _MEALS) or None
            return
        if labelled:
            rest = []
            for cell in cells:
                if self.hotel is None and _HOTEL.match(cell):
                    self.hotel = _HOTEL.sub("", cell).strip() or None
                elif self.meals is None and _MEALS.match(cell):
                    self.meals = _MEALS.sub("", cell).strip() or None
                elif cell and not _TRANSPORT.match(cell) and not cell.startswith(("行：", "行:")):
                    rest.append(cell)
            if len(rest) < len(cells):
                line = CELL_SEPARATOR.join(rest)
        if line:
            self.lines.append(line)

    def record(self) -> ItineraryDay:
        return ItineraryDay(
            day_no=self.day_no,
            title=self.title,
            text="；".join(self.lines),
            hotel=self.hotel,
            meals=self.meals,
        )


def split_days(lines: list[str]) -> list[ItineraryDay]:
    """The lines as days. Everything before the first header is the attachment's cover and is
    dropped; everything from the first 费用/须知 section inside a day is the terms and ends the
    reading. The 住宿 and 用餐 lines of a day become its two fields and are not repeated in its
    text."""
    chinese = any(_header(line, True) for line in lines)
    last = max((i for i, line in enumerate(lines) if _header(line, chinese)), default=-1)
    days: list[_Day] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        found = _header(line, chinese)
        if found is not None:
            days.append(_Day(found[0], *_title_and_flight(found[1])))
        elif not days:
            continue
        elif _SECTION.match(line) and not (
            index < last and _SPACES.sub("", line).startswith(_SOFT_SECTIONS)
        ):
            break
        else:
            days[-1].add(line)
    return [day.record() for day in days]


def parse_docx(data: bytes) -> list[ItineraryDay]:
    """The days written in a .docx 行程附件, or an empty list where it names none."""
    return split_days(document_lines(data))


async def fetch_attachment(
    url: str,
    *,
    etag: str | None,
    timeout: float = FETCH_TIMEOUT,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str | None] | None:
    """The attachment's bytes and the ETag they came with, or ``None`` — which the caller
    reads as "keep whatever you have". ``None`` is the answer to an unchanged file (304 under
    the ETag given), to anything that is not a .docx or .pdf by content type or by URL, to a file over
    ``max_bytes`` by its ``Content-Length`` or by what actually arrived, and to every failure:
    the file is one the agency's staff uploaded to a server neither this host nor the ERP
    owns, and a details read must not fail because it is unreachable."""
    headers = {"If-None-Match": etag} if etag else {}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), transport=transport, follow_redirects=True
        ) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as error:
        log.warning("tour: itinerary attachment not fetched: %s", type(error).__name__)
        return None
    if response.status_code == 304 or not response.is_success:
        return None
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ATTACHMENT_TYPES and not url.split("?")[0].lower().endswith(
        ATTACHMENT_SUFFIXES
    ):
        log.warning(
            "tour: itinerary attachment is not a .docx or .pdf: %s", content_type or "unstated"
        )
        return None
    if int(response.headers.get("content-length") or 0) > max_bytes:
        return None
    data = response.content
    return None if len(data) > max_bytes else (data, response.headers.get("etag"))


def _cache_path(cache_dir: Path, route_id: int) -> Path:
    return cache_dir / "itineraries" / f"{route_id}.json"


def cache_read(cache_dir: Path, route_id: int, url: str) -> tuple[Itinerary, str | None] | None:
    """The 线路's cached reading and the ETag it was read at, or ``None`` where there is none.
    An entry written for another ``url`` is stale — the catalog's editors replace an attachment
    by uploading a new one — and reads as nothing cached."""
    try:
        raw = json.loads(_cache_path(cache_dir, route_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if raw.get("version") != CACHE_VERSION or raw.get("url") != url:
        return None
    days = tuple(
        ItineraryDay(
            day_no=int(row["dayNo"]),
            title=str(row["title"]),
            text=str(row["text"]),
            hotel=row.get("hotel"),
            meals=row.get("meals"),
        )
        for row in raw.get("days") or ()
    )
    etag = raw.get("etag")
    if not days:
        return None
    return Itinerary(route_id, "attachment", etag, days), etag


def cache_write(cache_dir: Path, itinerary: Itinerary, url: str, etag: str | None) -> None:
    """Keep one 线路's reading as one JSON file, so the next conversation that opens the 线路
    downloads nothing. The file holds an attachment the agency wrote for its 同行 customers:
    owner-only permissions, like the memory store's."""
    path = _cache_path(cache_dir, itinerary.route_id)
    payload: dict[str, Any] = {
        "version": CACHE_VERSION,
        "routeId": itinerary.route_id,
        "url": url,
        "etag": etag,
        "days": [
            {
                "dayNo": day.day_no,
                "title": day.title,
                "text": day.text,
                "hotel": day.hotel,
                "meals": day.meals,
            }
            for day in itinerary.days
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, content)
    finally:
        os.close(descriptor)
