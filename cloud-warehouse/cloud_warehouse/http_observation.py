"""Bounded HTTP completion records without request content or raw exception text."""

import asyncio
import json
import logging
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .http_metrics import HttpMetrics

LOGGER = logging.getLogger("warehouse.http")
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"}


def configure_logging() -> None:
    """Called by serving entry points, not imports or workers. No root logger changes."""
    if not LOGGER.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


class HttpObservation:
    """Observe the complete ASGI exchange, including streaming and cancellation.

    IDs are server-generated, never identity or idempotency keys. Only a successfully
    authenticated organization may be supplied by the principal dependency through state.
    Per-request state stays local; concurrent requests never share mutable observations.
    """

    def __init__(self, app: ASGIApp, metrics: HttpMetrics | None = None) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started_at = perf_counter()
        request_id = str(uuid4())
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state.pop("observed_organization_id", None)
        status = None
        headers_ms = None
        sent_bytes = 0
        complete = False
        disconnected = False
        outcome = "incomplete"
        metrics = self.metrics if scope.get("path") not in {"/metrics", "/metrics/"} else None
        if metrics:
            metrics.start()

        async def observed_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message: Message) -> None:
            nonlocal status, headers_ms, sent_bytes, complete, disconnected
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
            try:
                await send(message)
            except OSError:
                disconnected = True
                raise
            if message["type"] == "http.response.start":
                status = message["status"]
                headers_ms = round((perf_counter() - started_at) * 1000, 3)
            elif message["type"] == "http.response.body":
                sent_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    complete = True

        try:
            await self.app(scope, observed_receive, observed_send)
            outcome = "complete" if complete else "disconnected" if disconnected else "incomplete"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            outcome = "disconnected" if disconnected else "error"
            if status is None and not disconnected:
                # Unhandled exceptions may carry SQL parameters, upstream credentials or
                # chat text. Preserve ambiguity about writes; do not echo or log the error.
                response = JSONResponse(
                    {
                        "code": "INTERNAL_OPERATION_UNCONFIRMED",
                        "message": "操作结果暂未确认；若刚提交变更，请先查询原记录后再决定是否重试。",
                    },
                    status_code=500,
                )
                try:
                    await response(scope, observed_receive, observed_send)
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except Exception:
                    outcome = "disconnected" if disconnected else "error"
                    raise RuntimeError("WAREHOUSE_HTTP_RESPONSE_INTERRUPTED") from None
            else:
                # A started stream cannot be replaced by a second response. Propagate a
                # fixed exception without the original context to the ASGI server.
                raise RuntimeError("WAREHOUSE_HTTP_STREAM_INTERRUPTED") from None
        finally:
            organization = state.get("observed_organization_id")
            event = {
                "event": "warehouse.http.completed",
                "schema_version": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "process_id": os.getpid(),
                "request_id": request_id,
                "method": scope["method"] if scope["method"] in METHODS else "OTHER",
                "route": getattr(scope.get("route"), "path", "<unmatched>"),
                "organization_id": str(organization) if isinstance(organization, UUID) else None,
                "status": status,
                "outcome": outcome,
                "response_complete": complete,
                "headers_ms": headers_ms,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                "response_bytes": sent_bytes,
            }
            # Completion logging is not the durable business audit. A failed output
            # sink must not turn a committed operation into a failed HTTP response.
            with suppress(Exception):
                LOGGER.info(json.dumps(event, separators=(",", ":")))
            if metrics:
                with suppress(Exception):
                    metrics.finish(event)
