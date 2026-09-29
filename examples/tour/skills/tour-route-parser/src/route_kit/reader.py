"""Read a PDF or DOCX itinerary into numbered source lines.

Every later stage cites these line numbers, so a line is the unit of evidence: a DOCX
paragraph, a DOCX table row (cells joined by `` | ``), or a PDF text line. PDF lines keep
their physical page. Nothing here interprets travel content.
"""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CELL_SEPARATOR = " | "
MIN_PDF_CHARS = 300
# Pictures worth reading: big enough to hold text (icons and rules are small), capped per file.
MIN_PICTURE_BYTES = 30_000
MAX_PICTURES = 30
RENDER_DPI = 110


class ReadError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Line:
    id: int
    text: str
    page: int | None = None
    kind: str = "para"  # para | row | pdf | image (transcribed) | picture (placeholder)
    picture: int | None = None  # index into Document.pictures for a placeholder


@dataclass
class Picture:
    """An image whose words the text layer does not hold: a PDF page or a DOCX picture."""

    key: str  # "page 3" or the DOCX part name
    data: bytes  # the image as stored (DOCX) or rendered (PDF page, JPEG)
    page: int | None = None


@dataclass
class Document:
    file_name: str
    media: str  # docx | pdf
    lines: list[Line] = field(default_factory=list)
    pages: int | None = None
    image_pages: list[int] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)  # lines dropped by cleaning, with reason
    pictures: list[Picture] = field(default_factory=list)  # only when read with pictures=True
    scanned: bool = False  # a PDF with no text layer, read only through its page pictures
    transcribed: list[dict] = field(default_factory=list)  # one record per picture, see vision
    pictures_unread: int = 0  # DOCX pictures that may hold words but were not kept (see read)

    def renumber(self) -> None:
        for number, line in enumerate(self.lines, start=1):
            line.id = number


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("　", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _run_text(node: ET.Element) -> str:
    """Text of a paragraph. A text box is stored twice (drawing and fallback): read once."""
    parts = []

    def visit(element: ET.Element):
        if element.tag == W + "t" and element.text:
            parts.append(element.text)
        elif element.tag == W + "tab":
            parts.append(" ")
        elif element.tag in (W + "br", W + "cr"):
            parts.append("\n")
        for child in element:
            if not child.tag.endswith("}Fallback"):
                visit(child)

    visit(node)
    return "".join(parts)


def _embeds(node: ET.Element) -> list[str]:
    """Relationship ids of the pictures inside an element (DrawingML and legacy VML)."""
    ids = []
    for element in node.iter():
        if element.tag.endswith("}blip") and element.get(R + "embed"):
            ids.append(element.get(R + "embed"))
        elif element.tag.endswith("}imagedata") and element.get(R + "id"):
            ids.append(element.get(R + "id"))
    return ids


def _read_docx(data: bytes) -> tuple[list[tuple[str, str]], dict[str, tuple[str, bytes]]]:
    """Body lines in order; a picture is a ("picture", rel id) line where it stands."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            media: dict[str, tuple[str, bytes]] = {}
            try:
                rels = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
            except KeyError:
                rels = None
            for rel in rels.iter(REL + "Relationship") if rels is not None else []:
                target = rel.get("Target", "")
                if rel.get("Type", "").endswith("/image") and not rel.get("TargetMode"):
                    name = "word/" + target.lstrip("/").removeprefix("word/")
                    try:
                        media[rel.get("Id")] = (name, archive.read(name))
                    except KeyError:
                        continue
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ReadError("DOCX_UNREADABLE") from error
    body = root.find(W + "body")
    out: list[tuple[str, str]] = []

    def walk(container):
        for child in container:
            if child.tag == W + "p":
                for piece in _run_text(child).split("\n"):
                    out.append(("para", piece))
                out.extend(("picture", rid) for rid in _embeds(child))
            elif child.tag == W + "tbl":
                # Cells keep their column position (an empty cell stays empty) so a
                # column such as MEAL can be read by its header; nested tables follow
                # their row as rows of their own.
                for row in child.findall(W + "tr"):
                    cells, nested = [], []
                    for cell in row.findall(W + "tc"):
                        texts = [_run_text(p).replace("\n", " ") for p in cell.findall(W + "p")]
                        cells.append(normalize(" ".join(texts)))
                        nested.extend(cell.findall(W + "tbl"))
                    if any(cells):
                        out.append(("row", CELL_SEPARATOR.join(cells).strip()))
                    for cell in row.findall(W + "tc"):
                        for paragraph in cell.findall(W + "p"):
                            out.extend(("picture", rid) for rid in _embeds(paragraph))
                    walk(nested)
            elif child.tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    walk(content)

    if body is not None:
        walk(body)
    return out, media


def _pdftotext(path: Path, layout: bool) -> str:
    args = ["pdftotext", "-enc", "UTF-8"] + (["-layout"] if layout else []) + [str(path), "-"]
    result = subprocess.run(args, capture_output=True, timeout=60)
    if result.returncode:
        raise ReadError("PDF_UNREADABLE")
    return result.stdout.decode("utf-8", "replace")


def _image_pages(path: Path, pages: int) -> list[int]:
    """Pages that carry images but little text: their words are not read."""
    try:
        listing = subprocess.run(
            ["pdfimages", "-list", str(path)], capture_output=True, timeout=60
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    with_images = set()
    for row in listing.splitlines()[2:]:
        cols = row.split()
        if cols and cols[0].isdigit():
            with_images.add(int(cols[0]))
    sparse = []
    for page in sorted(with_images):
        try:
            text = subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-enc", "UTF-8", str(path), "-"],
                capture_output=True,
                timeout=60,
            ).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired):
            continue
        if len(re.sub(r"\s", "", text)) < 80:
            sparse.append(page)
    return sparse


def _render_page(path: Path, page: int) -> bytes:
    result = subprocess.run(
        ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(RENDER_DPI)]
        + ["-scale-to", "1600", "-jpeg", "-jpegopt", "quality=85", str(path)],
        capture_output=True,
        timeout=60,
    )
    return result.stdout if result.returncode == 0 else b""


def _read_pdf(data: bytes, pictures: bool = False):
    try:
        return _read_pdf_file(data, pictures)
    except (OSError, subprocess.TimeoutExpired) as error:  # Poppler missing or stuck
        raise ReadError("PDF_TOOLS_FAILED") from error


def _read_pdf_file(data: bytes, pictures: bool):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(data)
        handle.flush()
        path = Path(handle.name)
        info = subprocess.run(["pdfinfo", str(path)], capture_output=True, timeout=60)
        match = re.search(rb"Pages:\s+(\d+)", info.stdout)
        pages = int(match.group(1)) if match else 0
        # Reading order beats column layout for itineraries; fall back to layout only
        # when the plain reading loses most of the text.
        plain = _pdftotext(path, layout=False)
        text = plain
        if len(re.sub(r"\s", "", plain)) < 0.6 * len(re.sub(r"\s", "", _pdftotext(path, True))):
            text = _pdftotext(path, layout=True)
        scanned = len(re.sub(r"\s", "", text)) < MIN_PDF_CHARS
        if scanned and not pictures:
            raise ReadError("PDF_TEXT_MISSING")
        # A scanned file is read through all its pages; otherwise only the image pages.
        image_pages = list(range(1, pages + 1)) if scanned else _image_pages(path, pages)
        rendered: dict[int, bytes] = {}
        if pictures:
            for page_no in image_pages[:MAX_PICTURES]:
                image = _render_page(path, page_no)
                if image:
                    rendered[page_no] = image
        out = []
        for page_no, page in enumerate(text.split("\f"), start=1):
            if page_no in rendered:
                out.append(("picture", "", page_no))
            for raw in page.splitlines():
                out.append(("pdf", raw, page_no))
        return out, pages, image_pages, rendered, scanned


# A repeated line that names meals, stays or money is content (a daily 午餐 line), not a header.
KEEP = re.compile(r"餐|住|宿|费|含|自理|元|欧|美金|美元")
PAGE_NUMBER = re.compile(r"第?\s*\d{1,3}\s*页?|\d{1,3}\s*/\s*\d{1,3}|-\s*\d{1,3}\s*-")


def _drop_repeated(lines: list[Line], removed: list[dict]) -> list[Line]:
    """Page headers/footers: short lines repeated on many pages, and page numbers.

    A page number is only taken from the first or last two lines of a page, so a bare
    number that pdftotext split off a price column stays.
    """
    counts: dict[str, set[int]] = {}
    for line in lines:
        if line.kind == "picture":
            continue
        if line.page and len(line.text) <= 40 and not KEEP.search(line.text):
            counts.setdefault(line.text, set()).add(line.page)
    pages = {line.page for line in lines if line.page}
    repeated = {
        t for t, p in counts.items() if len(pages) >= 4 and len(p) >= max(3, len(pages) // 2)
    }
    by_page: dict[int, list[int]] = {}
    for index, line in enumerate(lines):
        if line.page:
            by_page.setdefault(line.page, []).append(index)
    edges = {i for ids in by_page.values() for i in ids[:2] + ids[-2:]}
    kept = []
    for index, line in enumerate(lines):
        if line.kind == "picture":
            kept.append(line)
        elif line.text in repeated:
            removed.append({"text": line.text, "page": line.page, "reason": "页眉页脚（多页重复）"})
        elif index in edges and PAGE_NUMBER.fullmatch(line.text):
            removed.append({"text": line.text, "page": line.page, "reason": "页码"})
        else:
            kept.append(line)
    return kept


def read(path: Path, pictures: bool = False) -> Document:
    """Read a file into numbered lines.

    With ``pictures``, images that may hold words (PDF image pages, a scanned PDF's pages,
    DOCX pictures) are kept as placeholder lines where they stand, with their bytes in
    ``Document.pictures``; ``vision.transcribe`` replaces them with transcribed lines.
    """
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ReadError("FILE_UNREADABLE") from error
    found: list[Picture] = []
    if data.startswith(b"%PDF-"):
        raw, pages, image_pages, rendered, scanned = _read_pdf(data, pictures)
        doc = Document(path.name, "pdf", pages=pages, image_pages=image_pages, scanned=scanned)
        lines = []
        for kind, text, page in raw:
            if kind == "picture":
                found.append(Picture(f"page {page}", rendered[page], page))
                lines.append(Line(0, "", page, "picture", len(found) - 1))
            else:
                lines.append(Line(0, normalize(text), page, kind))
    elif data.startswith(b"PK"):
        doc = Document(path.name, "docx")
        body, media = _read_docx(data)
        seen: set[str] = set()
        lines = []
        for kind, text in body:
            if kind != "picture":
                lines.append(Line(0, normalize(text), None, kind))
                continue
            name, blob = media.get(text, ("", b""))
            digest = hashlib.sha256(blob).hexdigest()
            # Icons, rules and a picture repeated on every page carry no new words.
            if len(blob) < MIN_PICTURE_BYTES or digest in seen:
                continue
            seen.add(digest)
            if not pictures or len(found) >= MAX_PICTURES:
                doc.pictures_unread += 1
                continue
            found.append(Picture(name, blob))
            lines.append(Line(0, "", None, "picture", len(found) - 1))
    else:
        raise ReadError("UNSUPPORTED_FORMAT")
    lines = [line for line in lines if line.text or line.kind == "picture"]
    lines = _drop_repeated(lines, doc.removed)
    doc.lines = lines
    doc.pictures = found
    doc.renumber()
    return doc
