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
_CJK = re.compile(r"[一-鿿]")
_DAY_NO_NUMBER = re.compile(r"^DAY(?:\s*\|\|)?\s+(?!\d)(.*)$", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"^(\d{1,2})(?:\s*\|\|\s*(\S.*))?$")
_DAY_FOLLOWERS = re.compile(r"^(?:用餐|住宿|餐饮|餐食|交通|早餐|早[：:])")
_DAY_CN = re.compile(r"^(第\s*\d{1,2}\s*天)\s*(?:\|\|)?\s*(.*)$")
_FIELD_CELL = re.compile(r"^(?:用餐|住宿|餐饮|餐食|酒店|交通)[：:]")
_DASHES = re.compile(r"[⸺—–]+")
_LABEL = r"(?:住宿|用餐|餐饮|餐食|酒店|交通|住|餐|行)[：:]"
_LABEL_SPLIT = re.compile(r"\s+(?=" + _LABEL + ")")
_LABELS = re.compile(_LABEL)
_HEADER_ONLY = re.compile(r"^第\s*\d{1,2}\s*天$")


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


def normalise_lines(raw: list[str]) -> list[str]:
    """Layout text as document lines: column gaps as cells, dashes as one dash, the .pdf day
    headers rewritten, blank lines dropped."""
    lines = [
        _CELL_GAP.sub(CELL_SEPARATOR, _DASHES.sub("-", line.strip())).strip()
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
        out.append(line)
    return out
