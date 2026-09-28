"""Split source lines into evidence units and find the itinerary's day blocks.

Units are the citation grain: a long DOCX row that holds a whole day is cut at sentence
ends and before each 【bracket】, so a field can cite the sentence that states it.

Day blocks are found by rule: headers such as 第三天 / DAY-3 / D3 / Day 3 / 第23-26天.
A document often carries two sequences (an overview table, then the detailed programme);
the sequence with the most text becomes the days and the other becomes per-day overview
evidence. When no continuous sequence exists the caller may ask the model instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .reader import Document

CN = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "两": 2,
}


def cn_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if not text or any(ch not in CN and ch != "十" for ch in text):
        return None
    if text == "十":
        return 10
    if "十" in text:
        tens, _, ones = text.partition("十")
        return (CN.get(tens, 1) if tens else 1) * 10 + (CN.get(ones, 0) if ones else 0)
    return CN.get(text)


NUM = r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})"
HEADER = re.compile(
    r"^\s*(?:"
    rf"第\s*{NUM}\s*(?:天\s*)?(?:[-~至到–—]\s*(?:第\s*)?{NUM}\s*)?天"
    rf"|(?:DAY|Day|day|D)\s*[-_ ]?\s*{NUM}(?!\d)(?:\s*[-~至–—]\s*{NUM})?"
    r")"
    r"(?P<rest>.*)$"
)
TERMS = re.compile(
    r"^(?:[一二三四五六七八九十]+[、.])?(?:"
    r"(?:费用|报价|服务所?|团费|价格)(?:所)?(?:包含|不含|不包含|说明|标准)|(?:不)?包含项目|服务标准"
    r"|(?:预定|报名|出行|旅游|旅行团|行前)须知|注意事项|温馨提示|特别(?:说明|提示)|重要提示"
    r"|购物(?:店|场所|说明|安排)|自费(?:项目|说明)|签证(?:说明|资料|须知)|退改|取消条款)"
)
DATE_PREFIX = re.compile(r"^\s*\d{1,2}[./月]\d{1,2}日?\s+")


@dataclass
class Unit:
    id: int
    line: int
    text: str
    page: int | None = None
    origin: str = "text"  # "image": transcribed from a picture (see vision)


@dataclass
class DayBlock:
    day: int
    day_end: int | None
    header_unit: int
    units: list[int] = field(default_factory=list)
    overview_units: list[int] = field(default_factory=list)


@dataclass
class Layout:
    units: list[Unit]
    days: list[DayBlock]
    outside: list[int]  # units not in any day block (cover, terms, overview headers)
    method: str
    problems: list[str] = field(default_factory=list)


PARA_START = re.compile(
    r"^(?:餐|住|行|用餐|住宿|交通|早上|上午|中午|下午|晚上|傍晚|温馨提示|注意|备注|特别提示|参考航班|参考车次|【|★|❀|\d+[、.．]|[一二三四五六七八九十]+[、.．])"
)
PARA_END = re.compile(r"[。！？!?；;:：】）)]$")


def _paragraphs(doc: Document) -> list[tuple[int, str, int | None, str]]:
    """PDF text lines are visual lines; join the ones that continue a sentence.

    Lines transcribed from pictures are never joined, with each other or with text lines.
    """
    origin = {"image": "image"}
    if doc.media != "pdf":
        return [
            (line.id, line.text, line.page, origin.get(line.kind, "text")) for line in doc.lines
        ]
    out: list[list] = []
    previous = None
    for line in doc.lines:
        standalone = bool(_header(line.text)) or bool(PARA_START.match(line.text))
        joinable = (
            out
            and previous is not None
            and line.kind == "pdf"
            and previous.kind == "pdf"
            and not standalone
            and not _header(previous.text)
            and not PARA_END.search(previous.text)
            and len(previous.text) >= 24
        )
        if joinable:
            gap = (
                " "
                if re.search(r"[A-Za-z0-9]$", out[-1][1]) and re.match(r"[A-Za-z0-9]", line.text)
                else ""
            )
            out[-1][1] += gap + line.text
        else:
            out.append([line.id, line.text, line.page, origin.get(line.kind, "text")])
        previous = line
    return [tuple(item) for item in out]


def to_units(doc: Document, max_len: int = 110) -> list[Unit]:
    units: list[Unit] = []
    for line_id, text, page, source in _paragraphs(doc):
        pieces = [text]
        if len(text) > max_len:
            pieces = [
                p.strip()
                for p in re.split(
                    r"(?<=[。！？!?；;])\s*|\s+(?=【)|(?<=\S)(?=【[^】]{1,40}】)", text
                )
                if p and p.strip()
            ]
            merged: list[str] = []
            for piece in pieces:  # keep very short fragments with their neighbour
                if merged and (len(piece) < 8 or len(merged[-1]) < 8):
                    merged[-1] = merged[-1] + " " + piece
                else:
                    merged.append(piece)
            pieces = merged
        for piece in pieces:
            units.append(Unit(len(units) + 1, line_id, piece, page, source))
    return units


def _header(text: str):
    text = DATE_PREFIX.sub("", text)
    match = HEADER.match(text)
    if not match:
        return None
    numbers = [cn_number(g) for g in match.groups()[:-1] if g]
    numbers = [n for n in numbers if n]
    if not numbers or numbers[0] > 60:
        return None
    start = numbers[0]
    end = numbers[1] if len(numbers) > 1 and numbers[1] > start else None
    return start, end


def _sequences(
    headers: list[tuple[int, int, int | None]],
) -> list[list[tuple[int, int, int | None]]]:
    """Split header hits into runs that restart at day 1."""
    runs: list[list] = []
    for hit in headers:
        _, start, _ = hit
        if start == 1:
            runs.append([hit])
            continue
        if not runs:
            continue
        last = runs[-1][-1]
        expected = (last[2] or last[1]) + 1
        if start in (expected, expected + 1):  # one missing day stays in the run, reported later
            runs[-1].append(hit)
    return [run for run in runs if len(run) >= 2]


def segment(doc: Document) -> Layout:
    units = to_units(doc)
    hits = []
    for unit in units:
        parsed = _header(unit.text)
        if parsed:
            hits.append((unit.id, parsed[0], parsed[1]))
    runs = _sequences(hits)
    if not runs:
        return Layout(units, [], [u.id for u in units], "none", ["NO_DAY_HEADERS"])

    def score(run):
        # Only spans between two headers of the same run: the last day's length is unknown.
        return sum(
            len(u.text)
            for i in range(len(run) - 1)
            for u in units[run[i][0] - 1 : run[i + 1][0] - 1]
        ) / max(1, len(run) - 1)

    scored = sorted(runs, key=score, reverse=True)
    detail = scored[0]
    overview = scored[1] if len(scored) > 1 else None
    problems = []
    last_end = detail[-1][2] or detail[-1][1]
    covered = []
    for _, start, end in detail:
        covered.extend(range(start, (end or start) + 1))
    if covered != list(range(1, last_end + 1)):
        problems.append("DAY_SEQUENCE_GAP")

    blocks: list[DayBlock] = []
    boundaries = [hit[0] for hit in detail]
    other_headers = {hit[0] for run in runs if run is not detail for hit in run}
    for index, (unit_id, start, end) in enumerate(detail):
        stop = boundaries[index + 1] if index + 1 < len(boundaries) else None
        block_units = []
        for unit in units[unit_id - 1 :]:
            if stop is not None and unit.id >= stop:
                break
            # The last day ends at the terms, or at another run's day header (an
            # overview table placed after the programme).
            if (
                stop is None
                and unit.id > unit_id
                and (TERMS.search(re.sub(r"\s", "", unit.text)[:14]) or unit.id in other_headers)
            ):
                break
            block_units.append(unit.id)
        blocks.append(DayBlock(start, end, unit_id, block_units))

    if overview:
        by_day = {start: uid for uid, start, _ in overview}
        order = [uid for uid, _, _ in overview]
        for block in blocks:
            uid = by_day.get(block.day)
            if uid is None:
                continue
            nxt = next((o for o in order if o > uid), None)
            span = [uid]
            # An overview row is usually one unit; take continuation units until the next row.
            for unit in units[uid:]:
                if nxt is not None and unit.id >= nxt:
                    break
                if len(span) >= 3 or unit.id >= block.header_unit:
                    break
                if _header(unit.text):
                    break
                span.append(unit.id)
            block.overview_units = span

    inside = {u for b in blocks for u in b.units} | {u for b in blocks for u in b.overview_units}
    outside = [u.id for u in units if u.id not in inside]
    return Layout(units, blocks, outside, "rules" + ("+overview" if overview else ""), problems)
