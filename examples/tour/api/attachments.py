# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The 行程附件 a 线路 links, handed to the workbench as a download the advisor asked for.

The catalog's ``routeAttachmentUrl`` is a public object-store link under a hashed file name
(``6aa20bcd6093c.docx``), and the name the agency's staff gave the file is a separate field.
So the workbench does not link the store: it asks this host, which reads the file on the
advisor's behalf and answers it under its own name — ``GET /api/attachments/{product_id}`` in
``main.py``, on a signed-in session.

The download is a step the advisor asks for, not a fixture of every card: ``present_attachments``
is the presentation tool the model calls when the advisor asks for a 线路's 行程单, and the
card it renders lists the documents of the 线路 named, each joined from what this session has
seen. Nothing about the file passes through the model — the ids are the model's, the names and
the bytes are the server's."""

from __future__ import annotations

import logging
import mimetypes
import re
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from commerce_common.presentation import (
    EnrichmentContext,
    PresentationExtension,
    PresentationRefused,
)

log = logging.getLogger(__name__)

# How many 线路's documents one card lists: the advisor asks for one line's, or the few they
# are choosing between.
MAX_ATTACHMENT_ITEMS = 5
_UNSEEN_DROPPED = "以下编号不在本次会话的结果里，已从附件卡上去掉："
_NONE_DROPPED = "以下线路在目录里没有行程附件，已去掉："
NOTHING_TO_SEND = (
    "附件卡上没有一条线路：编号不在本次会话的结果里，或者这些线路在目录里没有行程附件。"
    "先搜索并展示线路，再用返回的 RT- 编号发附件卡。"
)

# The ceiling on one attachment as the workbench downloads it: an itinerary with its photographs
# runs to a few megabytes, and a file over this is not an itinerary.
MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024
FETCH_TIMEOUT = 30.0
OCTET_STREAM = "application/octet-stream"

_ASCII_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


class AttachmentTooLarge(Exception):
    """The store's file is over ``MAX_ATTACHMENT_BYTES``."""


class AttachmentUnavailable(Exception):
    """The store did not answer with the file: a transport failure or a non-success status."""


def content_disposition(name: str) -> str:
    """``Content-Disposition`` for the file under the name the agency gave it: the UTF-8 form
    every current browser reads, and an ASCII fallback beside it for the rest."""
    fallback = _ASCII_UNSAFE.sub("_", name).strip() or "attachment"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def media_type(name: str, stated: str | None) -> str:
    """The store's own content type when it states one, else the one the file name implies."""
    clean = (stated or "").split(";")[0].strip().lower()
    if clean and clean != OCTET_STREAM:
        return clean
    guessed, _ = mimetypes.guess_type(name)
    return guessed or clean or OCTET_STREAM


async def fetch(
    url: str,
    *,
    timeout: float = FETCH_TIMEOUT,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str | None]:
    """The file's bytes and the content type the store stated. Raises
    :class:`AttachmentUnavailable` for anything but a success and
    :class:`AttachmentTooLarge` for a file over ``max_bytes``, by its ``Content-Length`` or
    by what arrived."""
    try:
        async with (
            httpx.AsyncClient(
                timeout=httpx.Timeout(timeout), transport=transport, follow_redirects=True
            ) as client,
            client.stream("GET", url) as response,
        ):
            if not response.is_success:
                raise AttachmentUnavailable(f"status {response.status_code}")
            if int(response.headers.get("content-length") or 0) > max_bytes:
                raise AttachmentTooLarge()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise AttachmentTooLarge()
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type")
    except httpx.HTTPError as error:
        log.warning("tour: attachment not fetched: %s", type(error).__name__)
        raise AttachmentUnavailable(type(error).__name__) from error


class AttachmentsPayload(BaseModel):
    """What the model sends: the 线路 (or 团期) ids whose documents the advisor asked for."""

    product_ids: list[str] = Field(min_length=1, max_length=MAX_ATTACHMENT_ITEMS)
    note: str | None = Field(default=None, max_length=200)


class AttachmentItem(BaseModel):
    """One document: the 线路 it belongs to and the file as the agency named it. The bytes come
    from ``GET /api/attachments/{product_id}`` when the advisor taps."""

    product_id: str
    title: str
    name: str
    extension: str


class AttachmentsCard(BaseModel):
    note: str | None = None
    items: list[AttachmentItem]


_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_ATTACHMENT_ITEMS,
        },
        "note": {"type": "string", "maxLength": 200},
    },
    "required": ["product_ids"],
    "additionalProperties": False,
}


def _extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


async def _enrich(payload: AttachmentsPayload, context: EnrichmentContext) -> dict[str, Any]:
    items: list[AttachmentItem] = []
    unseen: list[str] = []
    without: list[str] = []
    for product_id in dict.fromkeys(payload.product_ids):
        product = context.state.seen_products.get(product_id)
        if product is None:
            unseen.append(product_id)
            continue
        found = await context.backend.attachment(context.session, product_id)
        if found is None:
            without.append(product_id)
            continue
        name, _url = found
        items.append(
            AttachmentItem(
                product_id=product_id, title=product.title, name=name, extension=_extension(name)
            )
        )
    if unseen:
        context.notes.append(f"{_UNSEEN_DROPPED}{'、'.join(unseen)}。")
    if without:
        context.notes.append(f"{_NONE_DROPPED}{'、'.join(without)}。")
    if not items:
        raise PresentationRefused(NOTHING_TO_SEND)
    return AttachmentsCard(note=payload.note, items=items).model_dump(exclude_none=True)


def build_attachments_extension() -> PresentationExtension:
    return PresentationExtension(
        name="present_attachments",
        component="attachments",
        description=(
            "Show the 行程附件 (the itinerary document, .docx or .pdf) of one or a few 线路 as "
            "downloads. Use only when the advisor asks for a line's itinerary document, 行程单 "
            "or attachment; pass the route ids (RT-…) or departure ids (DP-…) presented earlier "
            "in this session — the card fills in each file's name, and the advisor downloads it "
            "from the card. Never offer it unasked, and never paste a file URL."
        ),
        input_schema=_INPUT_SCHEMA,
        payload_model=AttachmentsPayload,
        enrich=_enrich,
    )
