# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""A .pdf 行程附件 as the lines ``itinerary_source.document_lines`` gives for a .docx.

``pdftotext -layout`` (poppler) keeps the page's columns as runs of spaces, so a table row
comes out as ``用餐        早：自理         中：自理        晚：自理``; three or more spaces
become the ``||`` cell separator and the row reads like a .docx row. Two day-header shapes
the .pdf attachments use and the .docx ones do not are rewritten into the shapes
``split_days`` knows: ``DAY   上海⸺科伦坡`` with the number drawn as a picture becomes
``DAY-1 上海-科伦坡`` by count, and a day written as a bare number on its own line, or as
``2       西南部海滨-科伦坡``, becomes ``第2天 …`` when the numbers run in order and the
用餐/住宿 row follows. A scanned .pdf has no text layer; it is refused as ``ImageOnlyPdf``
and the batch lists it as skipped.

pdftotext reads a page two ways and neither suits every attachment: ``-layout`` keeps the
columns of a day table (the DAY-and-number layout needs it), the default order follows the
text blocks (a day written as a wrapped middle column reads whole that way). ``PDF_MODES``
names both; the parser reads a .pdf in each and keeps the better document."""

from __future__ import annotations

import re
import shutil
import subprocess

PDF_TYPE = "application/pdf"
PDF_MAGIC = b"%PDF"
PDFTOTEXT = shutil.which("pdftotext")
PDFIMAGES = shutil.which("pdfimages")
PDFTOTEXT_TIMEOUT = 60.0
PDF_MODES = ("layout", "default")
# Below this many text letters the file is a scan (or a short cover), not a full document.
MIN_TEXT_CHARS = 300
CELL_SEPARATOR = " || "

_CELL_GAP = re.compile(r"[ \t　]{3,}")
# What a row of a table opens with: the label of a field, a day marker or a 参考航班 note. A
# line that opens with one of them is a row and its gaps are its columns.
_ROW_LABEL = re.compile(
    r"^(?:用餐|住宿|餐饮|餐食|酒店|交通|住|餐|行|参考航班|DAY|D\d|第\s*[0-9０-９一二三四五六七八九十]{1,3}\s*天)",
    re.IGNORECASE,
)
# A justified paragraph is printed with its words spread out to the margin, which leaves runs
# of spaces inside a sentence (华尔   街铜牛). Past this many Chinese characters before the
# first run, the line is prose being justified and not a row being ruled into columns.
PROSE_CHARS = 30
# Wingdings and the other private-use characters (U+E000–U+F8FF) a Word document draws: an
# airplane (U+F051) between two place names, a bullet (U+F0B2) before an item. Between two
# words the character separates them and reads as a dash; anywhere else it is decoration and
# goes, because a private-use character has no glyph outside Word and reaches an advisor as a
# blank box. Both layouts go through here (``itinerary_source.document_lines`` calls it for a
# .docx), so a line is clean wherever it was read.
_PRIVATE_BETWEEN = re.compile(r"(?<=[^\W_])[-]+(?=[^\W_])")
_PRIVATE = re.compile(r"[-]")
_DATE_STAMP = re.compile(
    r"(?<=[一-鿿])(?:1[0-2]|[1-9])\.(?:3[01]|[12]\d|[1-9])"
    r"(?=[一-鿿])(?!(?:小时|分钟|公里|千米|米|元|万|个|天|晚|度|星|钻|人|倍))"
)
_CJK = re.compile(r"[一-鿿]")
_DAY_NO_NUMBER = re.compile(r"^DAY(?:\s*\|\|)?\s+(?!\d)(.*)$", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"^(\d{1,2})(?:\s*\|\|\s*(\S.*))?$")
_DAY_FOLLOWERS = re.compile(r"^(?:用餐|住宿|餐饮|餐食|交通|早餐|早[：:])")
_DAY_CN = re.compile(r"^(第\s*\d{1,2}\s*天)\s*(?:\|\|)?\s*(.*)$")
_FIELD_CELL = re.compile(r"^(?:用餐|住宿|餐饮|餐食|酒店|交通)\s*[：:]")
_DASHES = re.compile(r"[⸺—–]+")
# ``餐饮 ：`` — a space between the label and its colon — is the label all the same.
_LABEL = r"(?:住宿|用餐|餐饮|餐食|酒店|交通|住|餐|行)\s*[：:]"
_LABEL_SPLIT = re.compile(r"\s+(?=" + _LABEL + ")")
_LABELS = re.compile(_LABEL)
_HEADER_ONLY = re.compile(r"^第\s*\d{1,2}\s*天$")
_DETAIL_START = re.compile(r"^(?:行程详情|详细行程|行程安排)\s*[：:]?$")
# Where the itinerary stops and the terms begin. Past one of these headings the attachments'
# numbered lists are the terms' own and never day headers.
_TERMS_HEAD = re.compile(
    r"^(?:服务所[包不]|费用[包不]|报价[包不]|包含项目|不包含项目|价格[包不]|自费|另行付费|购物"
    r"|预定须知|报名注意事项|旅行团须知|旅游注意事项|旅游补充协议|补充协议|附录)"
)
# What a day title is: the A-B-C route of the day, short and with no sentence in it. Anything
# else beside a day number is the programme paragraph the number was printed against.
_NOT_A_TITLE = re.compile(
    r"[。！，、；]|\|\||^(?:早上|上午|中午|下午|晚上|晚间|参考航班|餐|住|行|酒店|交通)\s*[：:]"
)
_TITLE_CHARS = 40


class ImageOnlyPdf(ValueError):
    """A .pdf with no or insufficient text; a short layer does not prove a scan."""

    def __init__(self, text_chars: int):
        self.text_chars = text_chars
        super().__init__(f"insufficient .pdf text: {text_chars} characters")


class PdfReadError(ValueError):
    """A classified extractor failure without document text or subprocess stderr."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def pdf_text(data: bytes, mode: str = "layout") -> str:
    """The .pdf's text, in page layout or in text-block order, or ``ValueError`` when pdftotext
    is missing or fails."""
    if PDFTOTEXT is None:
        raise PdfReadError("DOCUMENT_PARSER_UNAVAILABLE")
    try:
        done = subprocess.run(
            [PDFTOTEXT, *(["-layout"] if mode == "layout" else []), "-enc", "UTF-8", "-", "-"],
            input=data,
            capture_output=True,
            timeout=PDFTOTEXT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise PdfReadError("DOCUMENT_PARSE_TIMEOUT") from None
    except OSError:
        raise PdfReadError("DOCUMENT_PARSER_UNAVAILABLE") from None
    if done.returncode != 0:
        raise PdfReadError("DOCUMENT_PDF_READ_FAILED")
    return done.stdout.decode("utf-8", errors="replace")


class LocatedLine(str):
    """Normalized text with physical PDF pages, retained when lines merge or move."""

    pages: tuple[int, ...]

    def __new__(cls, value: str, pages: tuple[int, ...]):
        instance = super().__new__(cls, value)
        instance.pages = tuple(sorted(set(pages)))
        return instance


def _located(value: str, *sources: str) -> str:
    pages = {page for source in sources for page in getattr(source, "pages", ())}
    return LocatedLine(value, tuple(sorted(pages))) if pages else value


def pdf_lines(data: bytes, mode: str = "layout") -> list[str]:
    """The .pdf as document lines, in the shape of a .docx's."""
    text = pdf_text(data, mode)
    chars = sum(character.isalpha() for character in text)
    if chars < MIN_TEXT_CHARS:
        raise ImageOnlyPdf(chars)
    # Form feeds are physical page boundaries, including empty pages. A final form
    # feed contributes no lines and therefore never invents a trailing page reference.
    return normalise_lines(
        [
            LocatedLine(line, (page,))
            for page, content in enumerate(text.split("\f"), 1)
            for line in content.splitlines()
        ]
    )


def unread_image_pages(data: bytes, lines: list[str]) -> list[int]:
    """Detect sparse image pages without OCR or guessing the image's contents."""
    if PDFIMAGES is None:
        raise PdfReadError("DOCUMENT_PARSER_UNAVAILABLE")
    try:
        result = subprocess.run(
            [PDFIMAGES, "-list", "-"], input=data, capture_output=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        raise PdfReadError("DOCUMENT_PARSE_TIMEOUT") from None
    except OSError:
        raise PdfReadError("DOCUMENT_PARSER_UNAVAILABLE") from None
    if result.returncode:
        raise PdfReadError("DOCUMENT_PDF_READ_FAILED")
    pages = {
        int(m[1])
        for line in result.stdout.decode("utf-8", errors="replace").splitlines()
        if (m := re.match(r"^\s*(\d+)\s+\d+\s+(?:image|mask|smask)\s", line))
    }
    return sorted(
        page
        for page in pages
        if sum(
            sum(c.isalpha() for c in line) for line in lines if page in getattr(line, "pages", ())
        )
        < 100
    )


def _title_like(line: str) -> bool:
    """Whether a line could be a day's title rather than a line of its programme."""
    text = line.strip()
    return bool(text) and len(text) <= _TITLE_CHARS and not _NOT_A_TITLE.search(text)


def _hoistable(out: list[str], anchor: int) -> bool:
    """Whether the day that is starting can be moved back to where its block began. There has
    to be a block; it must hold no day of its own already; and the 餐/住/行 row that opened it
    must end a day rather than open one — an attachment that writes 用餐 and 住宿 under each
    day header keeps its days where they are printed."""
    if anchor < 1 or any(_DAY_CN.match(line) for line in out[anchor:]):
        return False
    if _DETAIL_START.match(out[anchor - 1]):
        return True
    return not any(_DAY_CN.match(line) for line in out[anchor - 2 : anchor])


def _date_stamp(line: str) -> str:
    """A page-edge 出发日期 stamp (9.23, 10.1) that ``-layout`` prints inside the sentence it
    stands beside (特别安排❀佛罗9.23伦萨深度游). It is a stamp only between two Chinese
    characters on a line of prose, and never a figure the sentence itself carries — 约1.5小时
    and 4.5公里 keep their unit after them."""
    return _DATE_STAMP.sub("", line) if len(_CJK.findall(line)) >= 20 else line


def strip_private_use(line: str) -> str:
    """One line with its private-use characters read: one standing between two words as a
    dash, the rest dropped."""
    return _PRIVATE.sub("", _PRIVATE_BETWEEN.sub("-", line))


def _cells(line: str) -> str:
    """One line with its column gaps as cells. A line that opens with no label and runs past
    ``PROSE_CHARS`` Chinese characters before its first gap is a justified paragraph, not a
    row: its gaps are the printing spreading a sentence to the margin, and they read as one
    space — a cell boundary there cuts a word in two (华尔 || 街铜牛)."""
    gap = _CELL_GAP.search(line)
    if gap is None:
        return line
    if not _ROW_LABEL.match(line.lstrip()) and len(_CJK.findall(line[: gap.start()])) > PROSE_CHARS:
        return _CELL_GAP.sub(" ", line)
    return _CELL_GAP.sub(CELL_SEPARATOR, line)


def normalise_lines(raw: list[str]) -> list[str]:
    """Layout text as document lines: column gaps as cells, dashes as one dash, the private-use
    characters read, the .pdf day headers rewritten, blank lines dropped."""
    lines = [
        _located(
            _cells(_DASHES.sub("-", _date_stamp(strip_private_use(line)).strip())).strip(), line
        )
        for line in raw
        if line.strip()
    ]
    # ``餐：/ 住：飞机上 行：无`` on one line with single spaces: one cell per label.
    lines = [
        _located(CELL_SEPARATOR.join(_LABEL_SPLIT.split(line)), line)
        if CELL_SEPARATOR not in line and len(_LABELS.findall(line)) >= 2
        else line
        for line in lines
    ]
    out: list[str] = []
    day_count = 0
    bare_next = 1
    terms = False
    skip = 0
    anchor = 0
    for index, line in enumerate(lines):
        if skip:
            skip -= 1
            continue
        if line == "或同级" and out:
            out[-1] = _located(f"{out[-1]} 或同级", out[-1], line)
            continue
        if _DETAIL_START.match(line):
            out.append(line)
            anchor = len(out)
            continue
        if _HEADER_ONLY.match(line) and index + 1 < len(lines):
            # ``第 01 天`` alone, the title on the next line.
            following = lines[index + 1]
            if len(following) <= 40 and "。" not in following and CELL_SEPARATOR not in following:
                out.append(_located(f"{line} {following}", line, following))
                skip = 1
                continue
        unnumbered = _DAY_NO_NUMBER.match(line)
        if unnumbered is not None:
            title = _located(unnumbered[1].strip(), line)
            # ``DAY   上海⸺科伦坡`` with the number drawn, then within three lines ``第1天 餐食：X
            # || 参考航班：…`` or ``1 || 餐食：X || …``: one header with the title and the
            # flight, the 餐食 cell as a row of its own, a wrapped title joined, a note kept.
            numbered = None
            between: list[str] = []
            consumed = 0
            for ahead in lines[index + 1 : index + 4]:
                consumed += 1
                numbered = _DAY_CN.match(ahead) or _BARE_NUMBER.match(ahead)
                if numbered is not None:
                    break
                if title.endswith(("（", "(")) or ahead.startswith("-"):
                    title = _located(f"{title}{ahead}", title, ahead)
                else:
                    between.append(ahead)
            if numbered is not None:
                marker = numbered[1] if numbered[1].startswith("第") else f"第{numbered[1]}天"
                cells = [c.strip() for c in (numbered[2] or "").split(CELL_SEPARATOR) if c.strip()]
                fields = [c for c in cells if _FIELD_CELL.match(c)]
                rest = [c for c in cells if not _FIELD_CELL.match(c)]
                out.append(
                    _located(
                        CELL_SEPARATOR.join([f"{marker} {title}".strip(), *rest]), title, ahead
                    )
                )
                out.extend(_located(field, ahead) for field in fields)
                out.extend(between)
                skip = consumed
                bare_next = int(re.sub(r"\D", "", marker) or bare_next) + 1
                continue
            day_count += 1
            out.append(_located(f"DAY-{day_count} {title}", title))
            continue
        if _TERMS_HEAD.match(re.sub(r"\s+", "", line)) and len(re.sub(r"\s+", "", line)) <= 20:
            terms = True
        bare = _BARE_NUMBER.match(line)
        if bare is not None and not terms and int(bare[1]) == bare_next:
            # A bare number is a day only in the itinerary: under 服务所不含项目 the same shape
            # is the numbering of the terms (1 / 2 / 3), and a day header there would swallow
            # the rest of the document.
            title = (bare[2] or "").strip()
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if (title and _CJK.search(title) and _title_like(title)) or _DAY_FOLLOWERS.match(
                following
            ):
                bare_next += 1
                out.append(_located(f"第{bare[1]}天 {title}".strip(), line))
                continue
        marked = _DAY_CN.match(line)
        if marked is not None:
            rest = marked[2].strip()
            if rest and len(_LABELS.findall(rest)) >= 2:
                # ``第 12 天 || 餐：/ || 住：温暖的家 || 行：飞机``: the day number printed in the
                # same row as the day's fields. The marker is the header and the fields are the
                # row under it, or the day is read as having neither.
                out.extend([_located(marked[1], line), _located(rest, line)])
                anchor = len(out)
                continue
            if not _title_like(rest) and _hoistable(out, anchor):
                # The day number printed against the middle of the right column's paragraph,
                # or on a line of its own inside it. The paragraph above it is this day's too,
                # so the header goes where the day began — after the 餐/住/行 row that closed
                # the day before it — and takes that line as its title where one is printed
                # there.
                if anchor < len(out) and _title_like(out[anchor]):
                    out[anchor] = _located(f"{marked[1]} {out[anchor]}", line, out[anchor])
                else:
                    out.insert(anchor, _located(marked[1], line))
                if rest:
                    out.append(_located(rest, line))
                continue
        out.append(line)
        if len(_LABELS.findall(line)) >= 2 or _HEADER_ONLY.match(line):
            anchor = len(out)
    return out
