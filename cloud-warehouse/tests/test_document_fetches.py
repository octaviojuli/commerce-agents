import io
import socket
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, changes, document_fetches, documents, remote_documents
from cloud_warehouse.api import create_app
from cloud_warehouse.assets import LocalObjectStore
from cloud_warehouse.catalog import list_products, synchronize
from cloud_warehouse.changes import Conflict
from cloud_warehouse.integrations import SourceError
from cloud_warehouse.persistence import Forbidden, transaction
from cloud_warehouse.remote_documents import Download

from .test_api import PASSWORD
from .test_documents import docx, parsed, proposal
from .test_sync import Connector, batch


def source_data():
    value = batch()
    value.routes[0].update(
        routeAttachmentUrl="https://files.acme.example/route.docx",
        routeAttachmentName="ACME 行程.docx",
    )
    return value


@pytest.mark.parametrize(
    "url",
    [
        "http://files.acme.example/a",
        "https://files.acme.example:8443/a",
        "https://files.acme.example.evil.example/a",
        "https://user:secret@files.acme.example/a",
        "https://files.acme.example/a#fragment",
        "file:///etc/passwd",
        "https://files.acme.example/\nprivate",
    ],
)
def test_invalid_destinations_never_resolve(monkeypatch, url):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid URL reached DNS")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(SourceError):
        remote_documents.destination(url, {"files.acme.example"})


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
    ],
)
def test_private_and_mixed_dns_denied(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", (address, 443))],
    )
    with pytest.raises(SourceError, match="ATTACHMENT_ADDRESS_DENIED"):
        remote_documents.destination("https://files.acme.example/a", {"files.acme.example"})


def test_pinned_socket_preserves_tls_hostname_and_no_credentials(monkeypatch):
    seen = {}
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    raw = object()

    def connect(address, timeout):
        seen["address"] = address
        return raw

    monkeypatch.setattr(socket, "create_connection", connect)

    class TLS:
        def wrap_socket(self, value, server_hostname):
            assert value is raw
            seen["sni"] = server_hostname
            return "verified socket"

    monkeypatch.setattr(remote_documents.ssl, "create_default_context", TLS)

    class Response(io.BytesIO):
        status = 200

        def getheader(self, key, default=None):
            return {"Content-Length": "4", "ETag": "ACME"}.get(key, default)

    class Connection:
        def __init__(self, host, timeout):
            seen["host"] = host

        def request(self, method, target, headers):
            assert self.sock == "verified socket"
            seen.update(method=method, target=target, headers=headers)

        def getresponse(self):
            return Response(b"ACME")

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(remote_documents.http.client, "HTTPSConnection", Connection)
    assert remote_documents._download(
        "https://files.acme.example/a?q=b", {"files.acme.example"}
    ) == Download(b"ACME", "ACME", "")
    assert seen == {
        "address": ("8.8.8.8", 443),
        "host": "files.acme.example",
        "sni": "files.acme.example",
        "method": "GET",
        "target": "/a?q=b",
        "headers": {"Accept-Encoding": "identity"},
        "closed": True,
    }


@pytest.mark.parametrize(
    "status,headers,body,code",
    [
        (302, {"Location": "http://169.254.169.254/"}, b"", "ATTACHMENT_REDIRECT_DENIED"),
        (200, {"Content-Encoding": "gzip"}, b"zip", "ATTACHMENT_ENCODING_DENIED"),
        (200, {"Content-Length": "20000001"}, b"", "ATTACHMENT_SIZE_INVALID"),
        (200, {"Content-Length": "4"}, b"abc", "ATTACHMENT_BODY_INCOMPLETE"),
        (200, {}, b"123456", "ATTACHMENT_SIZE_INVALID"),
    ],
)
def test_response_bounds(monkeypatch, status, headers, body, code):
    monkeypatch.setattr(
        remote_documents, "destination", lambda *a: ("files.acme.example", "8.8.8.8", "/file")
    )
    monkeypatch.setattr(remote_documents, "MAX_BYTES", 5)

    class TLS:
        def wrap_socket(self, *a, **k):
            return object()

    monkeypatch.setattr(remote_documents.ssl, "create_default_context", TLS)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: object())

    class Response(io.BytesIO):
        def getheader(self, key, default=None):
            return headers.get(key, default)

    response = Response(body)
    response.status = status

    class Connection:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return response

        def close(self):
            pass

    monkeypatch.setattr(remote_documents.http.client, "HTTPSConnection", Connection)
    with pytest.raises(SourceError, match=code):
        remote_documents._download("url", {"files.acme.example"})


async def test_source_fetch_parse_review_and_repeat_sync(database, tenant, tmp_path):
    _, runtime = database
    data = source_data()
    body = docx()
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    product = list_products(runtime, tenant.supplier)[0]
    assert len(document_fetches.listing(runtime, tenant.supplier, product["id"])) == 1
    assert document_fetches.seed(runtime, tenant.worker, tenant.connection_id) == 0
    with pytest.raises(Forbidden):
        document_fetches.claim(runtime, tenant.buyer, tenant.connection_id)
    store = LocalObjectStore(tmp_path / "objects")
    assert (
        document_fetches.run_once(
            runtime,
            tenant.worker,
            tenant.connection_id,
            store,
            {"files.acme.example"},
            lambda *a: Download(body, "ACME-etag"),
        )
        == "fetched"
    )
    job = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    asset = job["asset_id"]
    detail = documents.get(runtime, tenant.supplier, asset)
    assert detail["origin"]["etag"] == "ACME-etag"
    with pytest.raises(Forbidden):
        documents.download(runtime, tenant.buyer, store, asset)
    assert isinstance(documents.run_once(runtime, tenant.worker, store, parsed), str)
    staged = proposal(runtime, tenant, asset)
    with pytest.raises(Forbidden):
        changes.approve(runtime, tenant.worker, UUID(staged["id"]), staged["payload_hash"])
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    assert document_fetches.claim(runtime, tenant.worker, tenant.connection_id) is None
    assert documents.download(runtime, tenant.buyer, store, asset)["body"] == body
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(text("SELECT count(*) FROM document_fetch")) == 2
    with pytest.raises(DBAPIError), transaction(runtime, tenant.worker) as conn:
        conn.execute(text("UPDATE document_fetch SET source_hash='changed'"))


async def test_fetch_old_lease_and_changed_product_cannot_attach(database, tenant, tmp_path):
    admin, runtime = database
    data = source_data()
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    store = LocalObjectStore(tmp_path / "objects")
    work = document_fetches.claim(runtime, tenant.worker, tenant.connection_id)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE document_fetch SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {"id": work["id"]},
        )
    renewed = document_fetches.claim(runtime, tenant.worker, tenant.connection_id)
    with pytest.raises(Conflict):
        document_fetches.finish(runtime, tenant.worker, store, work, "ACME.docx", Download(docx()))
    data.routes[0]["routeName"] = "ACME 新版本"
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    assert (
        document_fetches.finish(
            runtime, tenant.worker, store, renewed, "ACME.docx", Download(docx())
        )
        == "obsolete"
    )
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(text("SELECT count(*) FROM document_asset")) == 0
    assert (
        document_fetches.run_once(
            runtime,
            tenant.worker,
            tenant.connection_id,
            store,
            {"files.acme.example"},
            lambda *a: Download(docx()),
        )
        == "fetched"
    )


async def test_fetch_retry_limit_and_original_upload_dedup(database, tenant, tmp_path):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source_data()))
    product = list_products(runtime, tenant.supplier)[0]
    store = LocalObjectStore(tmp_path / "objects")

    def failed(*a):
        raise RuntimeError("secret signed URL must not persist")

    for expected in ("retry_pending", "retry_pending", "failed"):
        assert (
            document_fetches.run_once(
                runtime, tenant.worker, tenant.connection_id, store, {"files.acme.example"}, failed
            )
            == expected
        )
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE document_fetch SET next_attempt_at=now() WHERE connection_id=:id"),
                {"id": tenant.connection_id},
            )
    row = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    assert row["attempts"] == 3 and row["last_error"] == "ATTACHMENT_PROCESS_FAILED"
    assert document_fetches.claim(runtime, tenant.worker, tenant.connection_id) is None
    body = docx()
    original = documents.upload(runtime, tenant.supplier, store, product["id"], "ACME.docx", body)
    document_fetches.retry(runtime, tenant.supplier, row["id"])
    assert (
        document_fetches.run_once(
            runtime,
            tenant.worker,
            tenant.connection_id,
            store,
            {"files.acme.example"},
            lambda *a: Download(body),
        )
        == "fetched"
    )
    assert (
        str(document_fetches.listing(runtime, tenant.supplier, product["id"])[0]["asset_id"])
        == original["id"]
    )


async def test_fetch_http_retry_and_permanent_file_failure(
    database, authentication, tenant, tmp_path
):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source_data()))
    product = list_products(runtime, tenant.supplier)[0]
    store = LocalObjectStore(tmp_path / "objects")
    assert (
        document_fetches.run_once(
            runtime,
            tenant.worker,
            tenant.connection_id,
            store,
            {"files.acme.example"},
            lambda *a: Download(b"not a DOCX"),
        )
        == "failed"
    )
    email = f"{uuid4()}@acme.example"
    auth.create_user(
        admin,
        email,
        PASSWORD,
        {
            tenant.supplier.organization_id: ["supplier_admin"],
            tenant.buyer.organization_id: ["buyer_admin"],
        },
    )
    with TestClient(create_app(runtime, authentication, object_store=store)) as client:
        assert (
            client.get(
                "/v1/document-fetches", params={"product_id": str(product["id"])}
            ).status_code
            == 401
        )
        login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        client.headers.update(
            {
                "Authorization": "Bearer " + login.json()["access_token"],
                "X-Organization-Id": str(tenant.supplier.organization_id),
            }
        )
        result = client.get("/v1/document-fetches", params={"product_id": str(product["id"])})
        assert result.status_code == 200
        job = result.json()["items"][0]
        assert job["attempts"] == 1 and job["last_error"] == "ATTACHMENT_FILE_INVALID"
        assert client.post(f"/v1/document-fetches/{job['id']}/retry").status_code == 200
        assert (
            document_fetches.run_once(
                runtime,
                tenant.worker,
                tenant.connection_id,
                store,
                {"files.acme.example"},
                lambda *a: Download(docx()),
            )
            == "fetched"
        )
        job = client.get("/v1/document-fetches", params={"product_id": str(product["id"])}).json()[
            "items"
        ][0]
        detail = client.get(f"/v1/documents/{job['asset_id']}").json()
        assert detail["origin"]["source_snapshot_id"] and "routeAttachmentUrl" not in str(detail)
        client.headers["X-Organization-Id"] = str(tenant.buyer.organization_id)
        assert (
            client.get(
                "/v1/document-fetches", params={"product_id": str(product["id"])}
            ).status_code
            == 403
        )
        assert client.post(f"/v1/document-fetches/{job['id']}/retry").status_code == 403


def due(admin, identifier):
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE document_fetch SET next_attempt_at=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": identifier},
        )


async def test_daily_same_url_recheck_keeps_approval_and_preserves_changed_file_history(
    database, tenant, tmp_path
):
    admin, runtime = database
    data = source_data()
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    product = list_products(runtime, tenant.supplier)[0]
    store = LocalObjectStore(tmp_path / "objects")
    body = docx()

    def fetch_body(value):
        return document_fetches.run_once(
            runtime,
            tenant.worker,
            tenant.connection_id,
            store,
            {"files.acme.example"},
            lambda *a: Download(value, "ACME same ETag"),
        )

    assert fetch_body(body) == "fetched"
    job = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    old_asset = job["asset_id"]
    documents.run_once(runtime, tenant.worker, store, parsed)
    staged = proposal(runtime, tenant, old_asset)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    published = changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    assert fetch_body(body) is None  # Publication does not trigger an immediate duplicate fetch.
    job = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    due(admin, job["id"])
    assert fetch_body(body) == "unchanged"
    unchanged = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    assert unchanged["asset_id"] == old_asset and unchanged["last_outcome"] == "unchanged"
    assert (
        unchanged["next_check_at"] - unchanged["last_checked_at"]
    ).total_seconds() == document_fetches.RECHECK_SECONDS
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM document_asset WHERE product_id=:id"),
                {"id": product["id"]},
            )
            == 1
        )
    due(admin, job["id"])
    new_body = body + b"\nACME changed source bytes"
    assert (
        fetch_body(new_body) == "fetched"
    )  # Even an unchanged supplier ETag cannot conceal byte changes.
    changed = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    new_asset = changed["asset_id"]
    assert new_asset != old_asset and changed["last_outcome"] == "changed"
    assert documents.get(runtime, tenant.supplier, old_asset)["origin"] is not None
    assert documents.get(runtime, tenant.supplier, new_asset)["status"] == "queued"
    assert documents.download(runtime, tenant.buyer, store, old_asset)["body"] == body
    with pytest.raises(Forbidden):
        documents.download(runtime, tenant.buyer, store, new_asset)
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            str(
                conn.scalar(
                    text("SELECT published_document_id FROM supplier_product WHERE id=:id"),
                    {"id": product["id"]},
                )
            )
            == published["document_id"]
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM document_fetch_observation WHERE product_id=:id"),
                {"id": product["id"]},
            )
            == 3
        )
    with pytest.raises(DBAPIError), transaction(runtime, tenant.worker) as conn:
        conn.execute(
            text("DELETE FROM document_fetch_observation WHERE product_id=:id"),
            {"id": product["id"]},
        )
    with transaction(runtime, tenant.buyer) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM document_fetch_observation WHERE product_id=:id"),
                {"id": product["id"]},
            )
            == 0
        )


async def test_recheck_failure_keeps_origin_and_renews_retry_budget(database, tenant, tmp_path):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source_data()))
    product = list_products(runtime, tenant.supplier)[0]
    store = LocalObjectStore(tmp_path / "objects")
    assert (
        document_fetches.run_once(
            runtime,
            tenant.worker,
            tenant.connection_id,
            store,
            {"files.acme.example"},
            lambda *a: Download(docx()),
        )
        == "fetched"
    )
    job = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    old_asset = job["asset_id"]
    with admin.begin() as conn:
        conn.execute(text("UPDATE document_fetch SET attempts=3 WHERE id=:id"), {"id": job["id"]})
    due(admin, job["id"])

    def failure(*args):
        raise SourceError("ATTACHMENT_HTTP_FAILED")

    for expected in ("retry_pending", "retry_pending", "failed"):
        assert (
            document_fetches.run_once(
                runtime, tenant.worker, tenant.connection_id, store, {"files.acme.example"}, failure
            )
            == expected
        )
        due(admin, job["id"])
    failed = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    assert failed["attempts"] == 3 and failed["asset_id"] is None
    assert failed["last_successful_asset_id"] == old_asset and failed["last_checked_at"]
    assert documents.get(runtime, tenant.supplier, old_asset)["origin"]
    assert document_fetches.claim(runtime, tenant.worker, tenant.connection_id) is None


async def test_recheck_fenced_lease_does_not_append_stale_observation(database, tenant, tmp_path):
    admin, runtime = database
    original_bytes = docx()
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source_data()))
    product = list_products(runtime, tenant.supplier)[0]
    store = LocalObjectStore(tmp_path / "objects")
    document_fetches.run_once(
        runtime,
        tenant.worker,
        tenant.connection_id,
        store,
        {"files.acme.example"},
        lambda *a: Download(original_bytes),
    )
    job = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    due(admin, job["id"])
    work = document_fetches.claim(runtime, tenant.worker, tenant.connection_id)
    assert document_fetches.claim(runtime, tenant.worker, tenant.connection_id) is None
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE document_fetch SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {"id": job["id"]},
        )
    renewed = document_fetches.claim(runtime, tenant.worker, tenant.connection_id)
    with pytest.raises(Conflict):
        document_fetches.finish(
            runtime, tenant.worker, store, work, "ACME.docx", Download(original_bytes)
        )
    assert (
        document_fetches.finish(
            runtime, tenant.worker, store, renewed, "ACME.docx", Download(original_bytes)
        )
        == "unchanged"
    )
    with transaction(runtime, tenant.worker) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM document_fetch_observation WHERE fetch_id=:id"),
                {"id": job["id"]},
            )
            == 2
        )


async def test_unchanged_recheck_does_not_claim_success_for_missing_local_object(
    database, tenant, tmp_path
):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source_data()))
    product = list_products(runtime, tenant.supplier)[0]
    store = LocalObjectStore(tmp_path / "objects")
    body = docx()

    def execute():
        return document_fetches.run_once(
            runtime,
            tenant.worker,
            tenant.connection_id,
            store,
            {"files.acme.example"},
            lambda *a: Download(body),
        )

    assert execute() == "fetched"
    job = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    (store.root / str(tenant.supplier.organization_id) / str(job["asset_id"])).unlink()
    due(admin, job["id"])
    assert execute() == "retry_pending"
    after = document_fetches.listing(runtime, tenant.supplier, product["id"])[0]
    assert after["last_checked_at"] == job["last_checked_at"]
