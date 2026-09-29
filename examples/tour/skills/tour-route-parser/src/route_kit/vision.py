"""Transcribe the words in pictures: PDF pages without a text layer and DOCX images.

``reader.read(path, pictures=True)`` leaves a placeholder line where each picture stands.
``transcribe`` asks the model to copy each picture's text verbatim and puts the lines in
the placeholder's place with ``kind="image"``, so they become evidence units like any
other line: fields that cite them pass the same checks, and review views mark them as
read from a picture. A cover picture is kept (downscaled) for the page's hero.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from . import prompts
from .llm import Model, ModelError
from .reader import Document, Line, normalize

MAX_SIDE = 1600
COVER_SIDE = 1100


class PictureText(BaseModel):
    kind: Literal["cover", "itinerary", "table", "text", "photo", "decorative"] = "photo"
    lines: list[str] = Field(default_factory=list)


def _jpeg(data: bytes, side: int, quality: int = 85) -> bytes | None:
    """The picture as a JPEG no larger than ``side``; ``b""`` for an icon; None if undecodable."""
    is_jpeg = data[:3] == b"\xff\xd8\xff"
    try:
        from PIL import Image
    except ImportError:  # Pillow is needed only for DOCX pictures and the cover image
        return data if is_jpeg else None
    try:
        with Image.open(io.BytesIO(data)) as image:
            if max(image.size) < 200:
                return b""  # an icon or a rule: nothing to read (a 1600×150 banner is kept)
            if is_jpeg and max(image.size) <= side and side == MAX_SIDE:
                return data
            image = image.convert("RGB")
            image.thumbnail((side, side))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - any decode failure means "not readable"
        return None


def transcribe(
    doc: Document, model: Model, pool: ThreadPoolExecutor, *, prepared: bool = False
) -> list[dict]:
    """Replace picture placeholders with transcribed lines; return one record per picture."""
    records: list[dict] = [{"key": p.key, "page": p.page} for p in doc.pictures]

    def run(index: int):
        picture = doc.pictures[index]
        image = picture.data if prepared else _jpeg(picture.data, MAX_SIDE)
        if image is None:
            return index, None, "PICTURE_UNREADABLE"
        if not image:
            return index, PictureText(kind="decorative"), ""
        try:
            answer = model.ask(
                prompts.PICTURE,
                {"picture": picture.key},
                PictureText,
                max_tokens=3000,
                images=(("image/jpeg", image),),
            )
        except ModelError as error:
            return index, None, str(error)[:160]
        return index, answer, ""

    results = list(pool.map(run, range(len(doc.pictures))))
    text_by_picture: dict[int, list[str]] = {}
    for index, answer, error in results:
        record = records[index]
        if answer is None:
            record.update(kind="", lines=0, error=error)
            continue
        lines = [normalize(x) for x in answer.lines if normalize(x)]
        record.update(kind=answer.kind, lines=len(lines), error="")
        if answer.kind in ("photo", "decorative"):
            lines = []
        text_by_picture[index] = lines
        if answer.kind == "cover" and not any(r.get("cover") for r in records):
            record["cover"] = True

    out: list[Line] = []
    for line in doc.lines:
        if line.kind != "picture":
            out.append(line)
            continue
        for text in text_by_picture.get(line.picture, []):
            out.append(Line(0, text, line.page, "image", line.picture))
    doc.lines = out
    doc.renumber()
    doc.transcribed = records
    return records


def cover_image(doc: Document) -> bytes | None:
    """The first picture the model read as a cover, downscaled for the page hero."""
    for record, picture in zip(doc.transcribed, doc.pictures, strict=False):
        if record.get("cover"):
            return _jpeg(picture.data, COVER_SIDE, quality=72) or None
    return None
