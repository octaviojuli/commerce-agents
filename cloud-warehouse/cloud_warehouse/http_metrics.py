"""Private per-application Prometheus metrics with finite, content-free labels."""

import hmac
import os
import re
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

TOKEN_PATTERN = re.compile(r"[a-f0-9]{64}")
OUTCOMES = {"complete", "incomplete", "cancelled", "disconnected", "error"}
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1, 2, 5, 10, 30, 60, 120, 300)
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"}


class HttpMetrics:
    def __init__(self, token: str) -> None:
        if not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("WAREHOUSE_METRICS_TOKEN must be 64 lowercase hexadecimal characters")
        if (
            os.environ.get("PROMETHEUS_MULTIPROC_DIR")
            or os.environ.get("prometheus_multiproc_dir")  # noqa: SIM112 - upstream legacy alias
            or os.environ.get("WEB_CONCURRENCY", "1") != "1"
        ):
            raise ValueError(
                "Warehouse metrics require one API worker per separately scraped instance"
            )
        self._authorization = b"Bearer " + token.encode("ascii")
        self.routes: frozenset[str] = frozenset()
        # Never use the global registry, which includes process/GC collectors and may
        # be shared by other apps. Separate factories must have separate counters.
        self.registry = CollectorRegistry()
        self.active = Gauge(
            "warehouse_http_in_progress",
            "Requests in progress, excluding metrics scrapes.",
            registry=self.registry,
        )
        self.started = Gauge(
            "warehouse_http_metrics_started_seconds",
            "This registry creation time in Unix seconds.",
            registry=self.registry,
        )
        self.started.set(time.time())
        self.requests = Counter(
            "warehouse_http_requests_total",
            "HTTP exchanges by transport outcome and status.",
            ["method", "route", "status", "outcome"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "warehouse_http_duration_seconds",
            "Full HTTP exchange duration, including stream sending.",
            ["method", "route", "outcome"],
            buckets=BUCKETS,
            registry=self.registry,
        )
        self.headers = Histogram(
            "warehouse_http_headers_seconds",
            "Time until response headers were sent.",
            ["method", "route"],
            buckets=BUCKETS,
            registry=self.registry,
        )
        self.bytes = Counter(
            "warehouse_http_response_bytes_total",
            "Response body bytes successfully sent.",
            ["method", "route"],
            registry=self.registry,
        )
        self.model_calls = Counter(
            "warehouse_advisor_model_calls_total",
            "Typed advisor model attempts.",
            ["call", "outcome"],
            registry=self.registry,
        )
        self.model_duration = Histogram(
            "warehouse_advisor_model_duration_seconds",
            "Typed advisor model attempt latency.",
            ["call"],
            buckets=(0.1, 0.3, 1, 3, 5, 8, 12, 20),
            registry=self.registry,
        )
        self.model_degraded = Counter(
            "warehouse_advisor_model_degraded_total",
            "Calls exhausted after bounded retry.",
            ["call"],
            registry=self.registry,
        )
        self.reply_violations = Counter(
            "warehouse_advisor_reply_violation_total",
            "Removed unsafe draft sentences.",
            ["reason"],
            registry=self.registry,
        )

    def observe_model(self, name, outcome, elapsed, degraded=False):
        if name not in {"extract", "decide", "draft"}:
            name = "other"
        if outcome not in {"complete", "timeout", "invalid_output", "provider_error", "error"}:
            outcome = "error"
        self.model_calls.labels(name, outcome).inc()
        self.model_duration.labels(name).observe(elapsed)
        if degraded:
            self.model_degraded.labels(name).inc()

    def observe_reply(self, reason):
        if reason not in {"private", "unproven", "promise", "conflict", "length", "invalid_action"}:
            reason = "unproven"
        self.reply_violations.labels(reason).inc()

    def authorized(self, authorization: str) -> bool:
        return hmac.compare_digest(authorization.encode("utf-8"), self._authorization)

    def start(self) -> None:
        self.active.inc()

    def finish(self, event: dict) -> None:
        try:
            route = event["route"] if event["route"] in self.routes else "<unmatched>"
            method = event["method"] if event["method"] in METHODS else "OTHER"
            status = event["status"]
            status = str(status) if isinstance(status, int) and 100 <= status <= 599 else "none"
            outcome = event["outcome"] if event["outcome"] in OUTCOMES else "incomplete"
            self.requests.labels(method, route, status, outcome).inc()
            self.duration.labels(method, route, outcome).observe(event["duration_ms"] / 1000)
            if event["headers_ms"] is not None:
                self.headers.labels(method, route).observe(event["headers_ms"] / 1000)
            self.bytes.labels(method, route).inc(event["response_bytes"])
        finally:
            self.active.dec()

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
