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
PDFTOTEXT_TIMEOUT = 60.0
PDF_MODES = ("layout", "default")
# Below this many Chinese characters the file is a scan (or a cover only), not a document.
MIN_TEXT_CHARS = 300
CELL_SEPARATOR = " || "

_CELL_GAP = re.compile(r"[ \t　]{3,}")
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
# What a day title is: the A-B-C route of the day, short and with no sentence in it. Anything
# else beside a day number is the programme paragraph the number was printed against.
_NOT_A_TITLE = re.compile(
    r"[。！，、；]|\|\||^(?:早上|上午|中午|下午|晚上|晚间|参考航班|餐|住|行|酒店|交通)\s*[：:]"
)
_TITLE_CHARS = 40


class ImageOnlyPdf(ValueError):
    """A .pdf with no text layer to read."""


def pdf_text(data: bytes, mode: str = "layout") -> str:
    """The .pdf's text, in page layout or in text-block order, or ``ValueError`` when pdftotext
    is missing or fails."""
    if PDFTOTEXT is None:
        raise ValueError("pdftotext is not installed")
    try:
        done = subprocess.run(
            [PDFTOTEXT, *(["-layout"] if mode == "layout" else []), "-enc", "UTF-8", "-", "-"],
            input=data,
            capture_output=True,
            timeout=PDFTOTEXT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"pdftotext failed: {type(error).__name__}") from error
    if done.returncode != 0:
        raise ValueError(f"pdftotext exit {done.returncode}: {done.stderr.decode()[:120]}")
    return done.stdout.decode("utf-8", errors="replace")


def pdf_lines(data: bytes, mode: str = "layout") -> list[str]:
    """The .pdf as document lines, in the shape of a .docx's."""
    text = pdf_text(data, mode)
    chars = len(_CJK.findall(text))
    if chars < MIN_TEXT_CHARS:
        raise ImageOnlyPdf(f"image-only .pdf: {chars} characters of text")
    return normalise_lines(text.replace("\f", "\n").splitlines())


def _title_like(line: str) -> bool:
    """Whether a line could be a day's title rather than a line of its programme."""
    text = line.strip()
    return bool(text) and len(text) <= _TITLE_CHARS and not _NOT_A_TITLE.search(text)


def _hoistable(out: list[str], anchor: int) -> bool:
    """Whether the day that is starting can be moved back to where its block began. There has
    to be a block; it must hold no day of its own already; and the 餐/住/行 row that opened it
    must end a day rather than open one — an attachment that writes 用餐 and 住宿 under each
    day header keeps its days where they are printed."""
    if anchor < 2 or any(_DAY_CN.match(line) for line in out[anchor:]):
        return False
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


def normalise_lines(raw: list[str]) -> list[str]:
    """Layout text as document lines: column gaps as cells, dashes as one dash, the private-use
    characters read, the .pdf day headers rewritten, blank lines dropped."""
    lines = [
        _CELL_GAP.sub(
            CELL_SEPARATOR, _DASHES.sub("-", _date_stamp(strip_private_use(line)).strip())
        ).strip()
        for line in raw
        if line.strip()
    ]
    # ``餐：/ 住：飞机上 行：无`` on one line with single spaces: one cell per label.
    lines = [
        CELL_SEPARATOR.join(_LABEL_SPLIT.split(line))
        if CELL_SEPARATOR not in line and len(_LABELS.findall(line)) >= 2
        else line
        for line in lines
    ]
    out: list[str] = []
    day_count = 0
    bare_next = 1
    skip = 0
    anchor = 0
    for index, line in enumerate(lines):
        if skip:
            skip -= 1
            continue
        if line == "或同级" and out:
            out[-1] = f"{out[-1]} 或同级"
            continue
        if _HEADER_ONLY.match(line) and index + 1 < len(lines):
            # ``第 01 天`` alone, the title on the next line.
            following = lines[index + 1]
            if len(following) <= 40 and "。" not in following and CELL_SEPARATOR not in following:
                out.append(f"{line} {following}")
                skip = 1
                continue
        unnumbered = _DAY_NO_NUMBER.match(line)
        if unnumbered is not None:
            title = unnumbered[1].strip()
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
                    title = f"{title}{ahead}"
                else:
                    between.append(ahead)
            if numbered is not None:
                marker = numbered[1] if numbered[1].startswith("第") else f"第{numbered[1]}天"
                cells = [c.strip() for c in (numbered[2] or "").split(CELL_SEPARATOR) if c.strip()]
                fields = [c for c in cells if _FIELD_CELL.match(c)]
                rest = [c for c in cells if not _FIELD_CELL.match(c)]
                out.append(CELL_SEPARATOR.join([f"{marker} {title}".strip(), *rest]))
                out.extend(fields)
                out.extend(between)
                skip = consumed
                bare_next = int(re.sub(r"\D", "", marker) or bare_next) + 1
                continue
            day_count += 1
            out.append(f"DAY-{day_count} {title}")
            continue
        bare = _BARE_NUMBER.match(line)
        if bare is not None and int(bare[1]) == bare_next:
            title = (bare[2] or "").strip()
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if (title and _CJK.search(title)) or _DAY_FOLLOWERS.match(following):
                bare_next += 1
                out.append(f"第{bare[1]}天 {title}".strip())
                continue
        marked = _DAY_CN.match(line)
        if marked is not None:
            rest = marked[2].strip()
            if rest and len(_LABELS.findall(rest)) >= 2:
                # ``第 12 天 || 餐：/ || 住：温暖的家 || 行：飞机``: the day number printed in the
                # same row as the day's fields. The marker is the header and the fields are the
                # row under it, or the day is read as having neither.
                out.extend([marked[1], rest])
                anchor = len(out)
                continue
            if not _title_like(rest) and _hoistable(out, anchor):
                # The day number printed against the middle of the right column's paragraph,
                # or on a line of its own inside it. The paragraph above it is this day's too,
                # so the header goes where the day began — after the 餐/住/行 row that closed
                # the day before it — and takes that line as its title where one is printed
                # there.
                if anchor < len(out) and _title_like(out[anchor]):
                    out[anchor] = f"{marked[1]} {out[anchor]}"
                else:
                    out.insert(anchor, marked[1])
                if rest:
                    out.append(rest)
                continue
        out.append(line)
        if len(_LABELS.findall(line)) >= 2 or _HEADER_ONLY.match(line):
            anchor = len(out)
    return out
