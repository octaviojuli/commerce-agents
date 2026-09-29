"""Read-only supplier contracts and complete, validated B2B pagination."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

Json = dict[str, Any]


class SourceError(ValueError):
    """A source response cannot safely be published. Only code is safe for logs."""

    def __init__(self, code: str, *, retryable: bool = True, retry_after_seconds: int = 0) -> None:
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class RouteRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    routeId: int = Field(gt=0)
    routeCode: str
    routeName: str = Field(min_length=1)
    days: int | None = Field(default=None, gt=0)
    departCityName: str | None = None


class DepartureRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    periodId: int = Field(gt=0)
    # The B2B contract uses zero for an unlinked plan. Preserve it in source storage;
    # publication quarantines it until the source supplies an actual route identity.
    routeId: int = Field(ge=0)
    periodCode: str
    departDate: date
    returnDate: date
    companyId: int | None = Field(default=None, gt=0)
    availableSeats: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def dates_in_order(self) -> DepartureRow:
        if self.returnDate < self.departDate:
            raise ValueError("returnDate precedes departDate")
        return self


@dataclass(frozen=True)
class CatalogBatch:
    routes: list[Json]
    departures: list[Json]
    window_start: date | None = None
    window_end: date | None = None
    observed_at: datetime | None = None

    def validate(self) -> None:
        routes = [RouteRow.model_validate(row) for row in self.routes]
        departures = [DepartureRow.model_validate(row) for row in self.departures]
        route_ids = {row.routeId for row in routes}
        if len(route_ids) != len(routes) or len({row.periodId for row in departures}) != len(
            departures
        ):
            raise SourceError("DUPLICATE_SOURCE_ID")
        if any(row.routeId != 0 and row.routeId not in route_ids for row in departures):
            raise SourceError("SOURCE_REFERENCE_MISSING")
        if any(
            (self.window_start and row.departDate < self.window_start)
            or (self.window_end and row.departDate > self.window_end)
            for row in departures
        ):
            raise SourceError("SOURCE_WINDOW_MISMATCH")


class SupplierConnector(Protocol):
    async def read_catalog(
        self, start: date | None = None, end: date | None = None
    ) -> CatalogBatch: ...


class B2BConnector:
    """The transport callback owns login/token refresh. This adapter can only issue GETs.

    Counts and first-page anchors must remain stable across pagination. A truncated or
    changing scan raises, leaving the previous published catalog unchanged.
    """

    def __init__(
        self, fetch: Callable[[str, Json], Awaitable[Json]], *, max_pages: int = 2000
    ) -> None:
        self.fetch = fetch
        self.max_pages = max_pages

    async def _pages(self, path: str, params: Json, key: str) -> list[Json]:
        size = 50
        first = await self.fetch(path, {**params, "pageNum": 1, "pageSize": size})
        try:
            total = int(first["total"])
            pages = int(first["totalPages"])
        except (KeyError, TypeError, ValueError) as error:
            raise SourceError("PAGINATION_METADATA_MISSING") from error
        if total < 0 or pages < 0 or pages > self.max_pages or (total > 0 and pages < 1):
            raise SourceError("PAGINATION_LIMIT_OR_INVALID")
        if first.get("pageNum", 1) != 1:
            raise SourceError("SOURCE_CHANGED_DURING_SCAN")
        if not isinstance(first.get("list"), list):
            raise SourceError("INVALID_PAGE_ROWS")
        rows = list(first["list"])
        for page in range(2, pages + 1):
            data = await self.fetch(path, {**params, "pageNum": page, "pageSize": size})
            if (
                data.get("total") != first["total"]
                or data.get("totalPages") != first["totalPages"]
                or data.get("pageNum", page) != page
            ):
                raise SourceError("SOURCE_CHANGED_DURING_SCAN")
            if not isinstance(data.get("list"), list):
                raise SourceError("INVALID_PAGE_ROWS")
            rows.extend(data["list"])
        if len(rows) != total or any(not isinstance(row, dict) or key not in row for row in rows):
            raise SourceError("INCOMPLETE_SOURCE_SCAN")
        if len({str(row[key]) for row in rows}) != len(rows):
            raise SourceError("DUPLICATE_SOURCE_ID")
        if pages > 1:
            anchor = await self.fetch(path, {**params, "pageNum": 1, "pageSize": size})
            if (
                anchor.get("total") != total
                or anchor.get("totalPages") != pages
                or anchor.get("pageNum", 1) != 1
                or fingerprint(anchor.get("list")) != fingerprint(first["list"])
            ):
                raise SourceError("SOURCE_CHANGED_DURING_SCAN")
        return rows

    async def read_catalog(
        self, start: date | None = None, end: date | None = None
    ) -> CatalogBatch:
        params = {}
        if start:
            params["departDateStart"] = start.isoformat()
        if end:
            params["departDateEnd"] = end.isoformat()
        # Read all route parents even when a date window limits the departure scan.
        routes = await self._pages("/route/list", {}, "routeId")
        observed_at = datetime.now(UTC)
        departures = await self._pages("/period/list", params, "periodId")
        batch = CatalogBatch(routes, departures, start, end, observed_at)
        batch.validate()
        return batch
