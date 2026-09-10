# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The 行程附件 a 线路 links, handed to the workbench as a download.

The catalog's ``routeAttachmentUrl`` is a public object-store link under a hashed file name
(``6aa20bcd6093c.docx``), and the name the agency's staff gave the file is a separate field.
So the workbench does not link the store: it asks this host, which reads the file on the
advisor's behalf and answers it under its own name — ``GET /api/attachments/{product_id}`` in
``main.py``, on a signed-in session. Nothing about the file passes through the model: the
card knows the attachment exists from the ``attachment`` attribute on the route record, and
the bytes go from the store to the browser."""

from __future__ import annotations

import logging
import mimetypes
import re
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

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
