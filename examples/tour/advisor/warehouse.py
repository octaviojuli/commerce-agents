"""The cloud warehouse, reached only through its advisor HTTP API with the advisor's token.

The advisor service never reads the warehouse database. Catalog, published itineraries,
departures, offers and quotes come from here; permissions stay the warehouse's.
"""

from dataclasses import dataclass
from datetime import date
from uuid import NAMESPACE_URL, uuid5

import httpx


class WarehouseError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Login:
    token: str
    expires_at: str
    user_id: str
    org_id: str
    org_name: str


def _message(response):
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or "云仓暂时无法访问"
    detail = body.get("message") or body.get("detail") or body
    if isinstance(detail, list):
        # A validation error: name the fields, which is what the advisor can act on.
        fields = [
            ".".join(str(p) for p in d.get("loc", [])[1:]) for d in detail if isinstance(d, dict)
        ]
        return (
            "云仓拒绝了这次请求：" + "、".join(filter(None, fields))
            if any(fields)
            else "云仓拒绝了这次请求"
        )
    return detail if isinstance(detail, str) else "云仓拒绝了这次请求"


class Warehouse:
    """One advisor's view of the warehouse. ``base_url`` ends before ``/v1``."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        org_id: str | None = None,
        *,
        transport=None,
        verify=True,
    ):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=45, transport=transport, verify=verify
        )
        self.token = token
        self.org_id = org_id

    async def aclose(self):
        await self.client.aclose()

    def _headers(self, extra=None):
        headers = {}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.org_id:
            headers["X-Organization-Id"] = self.org_id
        return {**headers, **(extra or {})}

    async def _get(self, path, **params):
        response = await self.client.get(
            path, headers=self._headers(), params={k: v for k, v in params.items() if v is not None}
        )
        if response.status_code >= 400:
            raise WarehouseError(response.status_code, _message(response))
        return response.json()

    async def picture(self, asset_id, limit=5_000_000):
        """A route's cover picture (bytes and type), or None; never more than ``limit`` bytes."""
        response = await self.client.get(f"/v1/documents/{asset_id}/file", headers=self._headers())
        kind = response.headers.get("content-type", "")
        if response.status_code >= 400 or not kind.startswith("image/"):
            return None
        return (response.content, kind) if len(response.content) <= limit else None

    async def _post(self, path, body, headers=None):
        response = await self.client.post(path, headers=self._headers(headers), json=body)
        if response.status_code >= 400:
            raise WarehouseError(response.status_code, _message(response))
        return response.json()

    async def login(self, email: str, password: str) -> Login:
        response = await self.client.post(
            "/v1/auth/login", json={"email": email, "password": password}
        )
        if response.status_code >= 400:
            raise WarehouseError(response.status_code, _message(response))
        body = response.json()
        self.token = body["access_token"]
        orgs = (await self._get("/v1/me/organizations"))["items"]
        buyer = next((o for o in orgs if "advisor" in o.get("roles", [])), None)
        if buyer is None:
            raise WarehouseError(403, "这个账号没有顾问权限")
        self.org_id = buyer["id"]
        return Login(
            token=self.token,
            expires_at=body.get("expires_at", ""),
            # The warehouse token is opaque; the advisor is identified by login email.
            user_id=str(
                uuid5(NAMESPACE_URL, str(self.client.base_url) + "|" + email.strip().lower())
            ),
            org_id=buyer["id"],
            org_name=buyer.get("name", ""),
        )

    async def still_advisor(self) -> bool:
        """Whether this token still reaches this organisation with the advisor role."""
        orgs = (await self._get("/v1/me/organizations"))["items"]
        return any(
            str(o.get("id")) == str(self.org_id) and "advisor" in o.get("roles", []) for o in orgs
        )

    async def products(
        self, query="", *, start: date | None = None, end: date | None = None, limit=50, after=None
    ):
        return await self._get(
            "/v1/advisor/products",
            query=query,
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
            limit=limit,
            after=after,
        )

    async def product(self, product_id):
        return await self._get(f"/v1/advisor/products/{product_id}")

    async def document(self, product_id, departure_id=None):
        try:
            return await self._get(
                f"/v1/advisor/products/{product_id}/document", departure_id=departure_id
            )
        except WarehouseError as error:
            if error.status == 404:
                return None
            raise

    async def departures(self, product_id, *, start=None, end=None, limit=25, after=None):
        return await self._get(
            "/v1/advisor/departures",
            product_id=product_id,
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
            limit=limit,
            after=after,
        )

    async def offers(self, departure_id):
        return await self._get("/v1/advisor/offers", departure_id=departure_id)

    async def quote(self, departure_id, party: dict, offer_id=None, *, key: str):
        body = {"departure_id": departure_id, "party": party}
        if offer_id:
            body["offer_id"] = offer_id
        return await self._post("/v1/advisor/quotes", body, headers={"Idempotency-Key": key})

    async def quote_read(self, quote_id):
        return await self._get(f"/v1/quotes/{quote_id}")
