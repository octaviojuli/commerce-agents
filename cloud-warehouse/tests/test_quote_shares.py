import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, quote_shares, quotes
from cloud_warehouse.api import create_app
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction

from .test_api import PASSWORD
from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE, add_buyer, commit, contract
from .test_quotes import managed_offer as managed_offer


async def issue(runtime, tenant, offer):
    commit(runtime, tenant.supplier, contract(runtime, tenant, offer))
    return await quotes.create(
        runtime,
        tenant.buyer,
        quotes.QuoteRequest(offer_id=offer, departure_date=FUTURE, party=quotes.Party()),
        str(uuid4()),
    )


async def test_share_replay_scope_projection_and_revoke(
    database, authentication, tenant, managed_offer
):
    admin, runtime = database
    quote = await issue(runtime, tenant, managed_offer)
    identifier = UUID(quote["quote_id"])
    command = quote_shares.Create(request_id=uuid4(), hours=72)
    share = quote_shares.create(runtime, tenant.buyer, identifier, command)
    assert len(share["token"]) == 43
    repeat = quote_shares.create(runtime, tenant.buyer, identifier, command)
    assert repeat["id"] == share["id"] and repeat["token"] is None
    with pytest.raises(Conflict):
        quote_shares.create(
            runtime, tenant.buyer, identifier, command.model_copy(update={"hours": 1})
        )
    public = quote_shares.read(runtime, authentication, share["token"])
    assert public["market_total"] == quote["market_total"] and not public["snapshot_stale"]
    assert set(public).isdisjoint(
        {
            "settlement_total",
            "settlement_lines",
            "buyer_org_id",
            "source_ref",
            "offer_id",
            "versions",
        }
    )
    assert "child_ages" not in public["party"]
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT token_hash FROM quote_share WHERE id=:id"), {"id": share["id"]}
            )
            == hashlib.sha256(share["token"].encode()).hexdigest()
        )
    with transaction(runtime, tenant.buyer) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("UPDATE quote_share SET expires_at=now()+interval '100 hours' WHERE id=:id"),
            {"id": share["id"]},
        )
    with authentication.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT * FROM quote_share"))
    with runtime.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("SELECT * FROM warehouse_quote_share_lookup(:digest)"),
            {"digest": hashlib.sha256(share["token"].encode()).hexdigest()},
        )
    with pytest.raises(Forbidden):
        quote_shares.revoke(runtime, tenant.supplier, share["id"])
    assert quote_shares.revoke(runtime, tenant.buyer, share["id"])["revoked"]
    assert quote_shares.revoke(runtime, tenant.buyer, share["id"])["revoked"]
    assert quote_shares.read(runtime, authentication, share["token"]) is None


@pytest.mark.parametrize(
    "lost", ["user", "membership", "organization", "grant", "share_expired", "role"]
)
async def test_public_share_dies_with_live_permission(
    database, authentication, tenant, managed_offer, excel_source, lost
):
    admin, runtime = database
    quote = await issue(runtime, tenant, managed_offer)
    share = quote_shares.create(
        runtime, tenant.buyer, UUID(quote["quote_id"]), quote_shares.Create(request_id=uuid4())
    )
    statements = {
        "user": ("UPDATE warehouse_user SET active=false WHERE id=:id", tenant.buyer.user_id),
        "membership": (
            "UPDATE membership SET active=false WHERE user_id=:id",
            tenant.buyer.user_id,
        ),
        "organization": (
            "UPDATE organization SET active=false WHERE id=:id",
            tenant.buyer.organization_id,
        ),
        "grant": (
            "UPDATE distribution_grant SET active=false WHERE connection_id=:id",
            excel_source,
        ),
        "share_expired": (
            "UPDATE quote_share SET created_at=now()-interval '3 hours',expires_at=now()-interval '1 hour',revoked_at=now() WHERE id=:id",
            share["id"],
        ),
        "role": (
            "UPDATE membership SET roles=ARRAY['auditor'] WHERE user_id=:id",
            tenant.buyer.user_id,
        ),
    }
    # Move time in place before the immutable revocation trigger for this expiration-only fixture.
    with admin.begin() as conn:
        if lost == "share_expired":
            conn.execute(text("ALTER TABLE quote_share DISABLE TRIGGER share_revoke_only"))
            conn.execute(
                text(
                    "UPDATE quote_share SET created_at=now()-interval '3 hours',expires_at=now()-interval '1 hour' WHERE id=:id"
                ),
                {"id": share["id"]},
            )
            conn.execute(text("ALTER TABLE quote_share ENABLE TRIGGER share_revoke_only"))
        else:
            sql, value = statements[lost]
            conn.execute(text(sql), {"id": value})
    assert quote_shares.read(runtime, authentication, share["token"]) is None


async def test_price_age_is_disclosed_without_expiring_the_quote_document(
    database, authentication, tenant, managed_offer, monkeypatch
):
    _, runtime = database
    quote = await issue(runtime, tenant, managed_offer)
    share = quote_shares.create(
        runtime, tenant.buyer, UUID(quote["quote_id"]), quote_shares.Create(request_id=uuid4())
    )
    at = datetime.now(UTC) + timedelta(minutes=10)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    monkeypatch.setattr(quotes, "datetime", Clock)
    public = quote_shares.read(runtime, authentication, share["token"])
    assert public["snapshot_stale"] and public["market_total"] == quote["market_total"]
    assert "available_seats" not in public
    assert quote_shares.create(
        runtime, tenant.buyer, UUID(quote["quote_id"]), quote_shares.Create(request_id=uuid4())
    )["token"]
    at = datetime.now(UTC) + timedelta(hours=25)
    with pytest.raises(Conflict, match="重新询价"):
        quote_shares.create(
            runtime, tenant.buyer, UUID(quote["quote_id"]), quote_shares.Create(request_id=uuid4())
        )


async def test_share_http_anonymous_read_and_same_org_owner_isolation(
    database, authentication, tenant, managed_offer
):
    admin, runtime = database
    quote = await issue(runtime, tenant, managed_offer)
    share = quote_shares.create(
        runtime, tenant.buyer, UUID(quote["quote_id"]), quote_shares.Create(request_id=uuid4())
    )
    other_id = auth.create_user(
        admin, f"{uuid4()}@acme.example", PASSWORD, {tenant.buyer.organization_id: ["advisor"]}
    )
    other = Principal(other_id, tenant.buyer.organization_id)
    assert quote_shares.list_for_quote(runtime, other, UUID(quote["quote_id"]))["items"] == []
    with pytest.raises(Forbidden):
        quote_shares.create(
            runtime, other, UUID(quote["quote_id"]), quote_shares.Create(request_id=uuid4())
        )
    with pytest.raises(Forbidden):
        quote_shares.revoke(runtime, other, share["id"])
    with TestClient(create_app(runtime, authentication)) as client:
        result = client.post("/v1/public/quote", json={"token": share["token"]})
        assert result.status_code == 200
        assert (
            result.headers["cache-control"] == "no-store"
            and result.headers["referrer-policy"] == "no-referrer"
        )
        assert result.headers["x-robots-tag"] == "noindex, nofollow"
        assert "settlement" not in result.text and "source_ref" not in result.text
        for token in ("bad", "x" * 43):
            assert client.post("/v1/public/quote", json={"token": token}).status_code == 404
        assert (
            client.post(
                f"/v1/quotes/{quote['quote_id']}/shares", json={"request_id": str(uuid4())}
            ).status_code
            == 401
        )


def test_customer_projection_drops_nested_internal_fields():
    public = quotes.customer_view(
        {
            "party": {"adults": 1, "child_ages": [6], "buyer_id": "secret"},
            "market_lines": [{"code": "adult", "total": "100", "settlement": "secret"}],
            "settlement_total": "secret",
            "source_ref": "secret",
        }
    )
    assert "settlement" not in str(public) and "secret" not in str(public)


async def test_concurrent_share_creation_returns_secret_once_and_caps_active_links(
    database, tenant, managed_offer
):
    _, runtime = database
    quote = await issue(runtime, tenant, managed_offer)
    identifier = UUID(quote["quote_id"])
    command = quote_shares.Create(request_id=uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(
            pool.map(
                lambda _: quote_shares.create(runtime, tenant.buyer, identifier, command), range(4)
            )
        )
    assert (
        len({row["id"] for row in rows}) == 1 and sum(row["token"] is not None for row in rows) == 1
    )
    for _ in range(9):
        quote_shares.create(
            runtime, tenant.buyer, identifier, quote_shares.Create(request_id=uuid4())
        )
    with pytest.raises(Conflict, match="10"):
        quote_shares.create(
            runtime, tenant.buyer, identifier, quote_shares.Create(request_id=uuid4())
        )
    quote_shares.revoke(runtime, tenant.buyer, rows[0]["id"])
    assert quote_shares.create(
        runtime, tenant.buyer, identifier, quote_shares.Create(request_id=uuid4())
    )["token"]


async def test_share_history_pagination_filters_and_changed_anchor(database, tenant, managed_offer):
    admin, runtime = database
    quote = await issue(runtime, tenant, managed_offer)
    identifier = UUID(quote["quote_id"])
    shares = []
    for index in range(28):
        share = quote_shares.create(
            runtime, tenant.buyer, identifier, quote_shares.Create(request_id=uuid4())
        )
        shares.append(share)
        if index < 24:
            quote_shares.revoke(runtime, tenant.buyer, share["id"])
    first = quote_shares.list_page(runtime, tenant.buyer)
    assert len(first["items"]) == 25 and first["next_cursor"]
    tail = quote_shares.list_page(runtime, tenant.buyer, before=UUID(first["next_cursor"]))
    ids = [row["id"] for row in first["items"] + tail["items"]]
    assert len(ids) == len(set(ids)) == 28 and tail["next_cursor"] is None
    assert all(row["product_name"] and row["quote_readable"] for row in first["items"])
    assert all(
        set(row).isdisjoint({"token", "token_hash", "body", "actor_id", "request_id"})
        for row in first["items"]
    )
    active = quote_shares.list_page(runtime, tenant.buyer, status="active", limit=2)
    anchor = UUID(active["next_cursor"])
    quote_shares.revoke(runtime, tenant.buyer, anchor)
    older = quote_shares.list_page(runtime, tenant.buyer, status="active", limit=2, before=anchor)
    assert len(older["items"]) == 2 and older["next_cursor"] is None
    with admin.begin() as conn:
        conn.execute(text("ALTER TABLE quote_share DISABLE TRIGGER share_revoke_only"))
        conn.execute(
            text(
                "UPDATE quote_share SET created_at=now()-interval '3 hours',expires_at=now()-interval '1 hour' WHERE id=:id"
            ),
            {"id": shares[-1]["id"]},
        )
        conn.execute(text("ALTER TABLE quote_share ENABLE TRIGGER share_revoke_only"))
    expired = quote_shares.list_page(runtime, tenant.buyer, status="expired")
    assert [row["id"] for row in expired["items"]] == [shares[-1]["id"]]
    revoked = quote_shares.list_page(runtime, tenant.buyer, status="revoked", limit=100)
    assert len(revoked["items"]) == 25


async def test_share_history_scoped_cursor_and_access_loss(
    database, tenant, managed_offer, excel_source
):
    admin, runtime = database
    quote = await issue(runtime, tenant, managed_offer)
    identifier = UUID(quote["quote_id"])
    first = quote_shares.create(
        runtime, tenant.buyer, identifier, quote_shares.Create(request_id=uuid4())
    )
    another = await quotes.create(
        runtime,
        tenant.buyer,
        quotes.QuoteRequest(
            offer_id=managed_offer, departure_date=FUTURE, party=quotes.Party(adults=2)
        ),
        str(uuid4()),
    )
    second = quote_shares.create(
        runtime, tenant.buyer, UUID(another["quote_id"]), quote_shares.Create(request_id=uuid4())
    )
    assert len(quote_shares.list_page(runtime, tenant.buyer)["items"]) == 2
    assert [
        row["id"]
        for row in quote_shares.list_page(runtime, tenant.buyer, quote_id=identifier)["items"]
    ] == [first["id"]]
    with pytest.raises(Forbidden):
        quote_shares.list_page(runtime, tenant.buyer, quote_id=identifier, before=second["id"])
    other_id = auth.create_user(
        admin, f"{uuid4()}@acme.example", PASSWORD, {tenant.buyer.organization_id: ["advisor"]}
    )
    other = Principal(other_id, tenant.buyer.organization_id)
    assert quote_shares.list_page(runtime, other)["items"] == []
    with pytest.raises(Forbidden):
        quote_shares.list_page(runtime, other, before=first["id"])
    with pytest.raises(Forbidden):
        quote_shares.list_page(runtime, tenant.supplier)
    outsider = add_buyer(admin, tenant, excel_source)
    assert quote_shares.list_page(runtime, outsider)["items"] == []
    with pytest.raises(Forbidden):
        quote_shares.list_page(runtime, outsider, before=first["id"])
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE connection_id=:id"),
            {"id": excel_source},
        )
    remaining = quote_shares.list_page(runtime, tenant.buyer)["items"]
    assert len(remaining) == 2 and all(
        not row["quote_readable"] and row["product_name"] is None and row["departure_date"] is None
        for row in remaining
    )
    assert quote_shares.revoke(runtime, tenant.buyer, first["id"])["revoked"]


def test_share_history_http_validation_and_anonymous_denial(database, authentication, tenant):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    with TestClient(create_app(runtime, authentication)) as client:
        assert client.get("/v1/quote-shares").status_code == 401
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        result = client.get("/v1/quote-shares", headers=headers)
        assert result.json() == {"items": [], "next_cursor": None}
        assert result.headers["cache-control"] == "no-store"
        for query in ("limit=0", "limit=101", "status=unknown", "before=bad", "quote_id=bad"):
            assert client.get("/v1/quote-shares?" + query, headers=headers).status_code == 422
        assert client.get(f"/v1/quote-shares?before={uuid4()}", headers=headers).status_code == 403
