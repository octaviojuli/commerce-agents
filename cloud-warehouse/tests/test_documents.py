import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, changes, distribution, documents
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.api import create_app
from cloud_warehouse.assets import LocalObjectStore
from cloud_warehouse.catalog import list_products, synchronize
from cloud_warehouse.changes import Conflict
from cloud_warehouse.integrations import SourceError
from cloud_warehouse.persistence import Forbidden, transaction
from cloud_warehouse.route_content import day_basis
from cloud_warehouse.route_doc import Day, Quality, RouteDoc, Source, Summary
from shopping_agent import ShoppingSessionContext
from tour.api.warehouse_documents import parse

from .test_api import PASSWORD
from .test_sync import Connector


def docx(label="ACME"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + "".join(
                f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>"
                for day in range(1, 4)
                for line in (f"D{day} {label} 城市", f"游览【{label} 景点 {day}】，含门票。")
            )
            + "</w:body></w:document>",
        )
    return output.getvalue()


@pytest.fixture
async def source(database, tenant, tmp_path):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    product = list_products(runtime, tenant.supplier)[0]
    store = LocalObjectStore(tmp_path / "objects")
    body = docx()
    uploaded = documents.upload(runtime, tenant.supplier, store, product["id"], "ACME.docx", body)
    return store, product, UUID(uploaded["id"]), body


def parsed(work, body):
    product = work["product_snapshot"]
    return RouteDoc(
        route_id=1,
        route_code=product["code"],
        name=product["name"],
        department="",
        summary=Summary(days=3),
        days=[Day(day=n, title=f"ACME 第{n}天", text="ACME 景点") for n in range(1, 4)],
        source=Source(
            attachment_name=work["file_name"],
            attachment_url="",
            bytes=len(body),
            etag=work["file_hash"],
            parsed_at=datetime.now(UTC),
            parser="acme-test-v1",
        ),
        quality=Quality(completeness=1),
    )


def complete_fixture_review(body):
    """An explicit human review of fictional fixtures, including unknown meals/stays."""
    content = RouteDoc.model_validate(body).model_copy(deep=True)
    content.inclusions = content.inclusions or ["ACME 已核对的包含项目"]
    content.exclusions = content.exclusions or ["ACME 已核对的自理项目"]
    for day in content.days:
        for key in ("breakfast", "lunch", "dinner"):
            meal = getattr(day.meals, key)
            if meal.included is None:
                meal.text, meal.included = "ACME 复核自理", False
        if day.overnight == "unknown":
            day.overnight = "home"
        day.summary_basis_hash = day_basis(day)
    return content


def proposal(runtime, tenant, asset):
    detail = documents.get(runtime, tenant.supplier, asset)
    command = documents.DocumentReview(
        target_id=detail["product_id"],
        expected_version=detail["product_version"],
        parse_id=detail["parse"]["id"],
        content=RouteDoc.model_validate(detail["parse"]["body"]),
        note="ACME 人工逐日核对原文件",
    )
    return documents.stage(runtime, tenant.supplier, command)


def test_private_objects_are_immutable_and_integrity_checked(tmp_path):
    store = LocalObjectStore(tmp_path / "objects")
    org, asset = uuid4(), uuid4()
    body = b"ACME"
    store.put(org, asset, body)
    assert store.read(org, asset, hashlib.sha256(body).hexdigest()) == body
    with pytest.raises(FileExistsError):
        store.put(org, asset, b"changed")
    with pytest.raises(ValueError):
        store.read(org, asset, "wrong")
    linked = uuid4()
    (store.root / str(org) / str(linked)).symlink_to(store.root / str(org) / str(asset))
    with pytest.raises(OSError):
        store.read(org, linked, hashlib.sha256(body).hexdigest())


def test_document_archive_validation():
    assert documents.file_type("ACME.docx", docx()).endswith("document")
    for name, body in (("ACME.exe", b"MZ"), ("ACME.pdf", docx()), ("ACME.docx", b"PKbroken")):
        with pytest.raises(Conflict):
            documents.file_type(name, body)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            '<!DOCTYPE doc [<!ENTITY external SYSTEM "file:///private-data">]><doc>&external;</doc>',
        )
    with pytest.raises(Conflict):
        documents.file_type("ACME.docx", output.getvalue())


def test_document_review_publish_download_and_revocation(database, authentication, tenant, source):
    _, runtime = database
    store, product, asset, body = source
    assert documents.upload(runtime, tenant.supplier, store, product["id"], "ACME.docx", body)[
        "duplicate"
    ]
    with pytest.raises(Forbidden):
        documents.download(runtime, tenant.buyer, store, asset)
    assert documents.run_once(runtime, tenant.worker, store, parsed)
    detail = documents.get(runtime, tenant.supplier, asset)
    assert detail["status"] == "parsed" and detail["parse"]["body"]["quality"]["reviewed_by"] == ""
    with pytest.raises(Forbidden):
        documents.download(runtime, tenant.buyer, store, asset)
    staged = proposal(runtime, tenant, asset)
    with pytest.raises(Forbidden):
        changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    result = changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    assert changes.apply(runtime, tenant.supplier, UUID(staged["id"])) == result
    public = documents.published(runtime, tenant.buyer, UUID(result["document_id"]))
    assert "reviewed_by" not in public and public["historical"] is False
    assert "source" not in public["body"] and "field_sources" not in public
    assert documents.download(runtime, tenant.buyer, store, asset)["body"] == body
    assert "review_note" not in public and "change_id" not in public
    assert documents.current(runtime, tenant.buyer, product["id"])["id"] == UUID(
        result["document_id"]
    )
    with transaction(runtime, tenant.worker) as conn:
        for table in ("document_parse", "document_publication"):
            # App roles cannot update immutable evidence even if they own this organization.
            with pytest.raises(DBAPIError), conn.begin_nested():
                conn.execute(text(f"UPDATE {table} SET body='{{}}'::jsonb"))
    grant = distribution.listing(runtime, authentication, tenant.supplier)["items"][0]
    distribution.control(
        runtime,
        tenant.supplier,
        grant["id"],
        distribution.Control(
            version=grant["version"],
            active=False,
            valid_from=grant["valid_from"],
            expires_at=grant["expires_at"],
            note="ACME document access revoked",
        ),
    )
    with pytest.raises(Forbidden):
        documents.published(runtime, tenant.buyer, UUID(result["document_id"]))
    with pytest.raises(Forbidden):
        documents.download(runtime, tenant.buyer, store, asset)


def test_stale_product_and_incomplete_review_cannot_publish(database, tenant, source):
    admin, runtime = database
    store, product, asset, _ = source
    documents.run_once(runtime, tenant.worker, store, parsed)
    staged = proposal(runtime, tenant, asset)
    broken = dict(staged["preview"])
    broken["content"] = {**broken["content"], "days": []}
    with pytest.raises(Conflict, match="逐日"):
        changes.stage(runtime, tenant.supplier, "document", broken)
    forged = dict(staged["preview"])
    forged["content"] = {
        **forged["content"],
        "source": {**forged["content"]["source"], "etag": "forged"},
    }
    with pytest.raises(Conflict, match="来源"):
        changes.stage(runtime, tenant.supplier, "document", forged)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_product SET version=version+1 WHERE id=:id"),
            {"id": product["id"]},
        )
    with pytest.raises(Conflict):
        changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(text("SELECT count(*) FROM document_publication")) == 0


def test_parse_lease_takeover_failure_and_explicit_retry(database, tenant, source):
    admin, runtime = database
    store, _, asset, body = source
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = list(pool.map(lambda _: documents.claim(runtime, tenant.worker), range(2)))
    assert sum(item is not None for item in work) == 1
    old = next(item for item in work if item)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE document_asset SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {"id": asset},
        )
    new = documents.claim(runtime, tenant.worker)
    with pytest.raises(Conflict):
        documents.finish_parse(runtime, tenant.worker, old, parsed(old, body))
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE document_asset SET lease_until=now()-interval '1 second',attempts=3 WHERE id=:id"
            ),
            {"id": asset},
        )
    assert documents.claim(runtime, tenant.worker) is None
    assert documents.get(runtime, tenant.supplier, asset)["status"] == "failed"
    documents.retry(runtime, tenant.supplier, asset)

    def fail(*_):
        raise RuntimeError("secret source detail")

    result = documents.run_once(runtime, tenant.worker, store, fail)
    assert result["error"] == "DOCUMENT_PARSE_FAILED" and "secret" not in str(
        documents.get(runtime, tenant.supplier, asset)
    )
    documents.retry(runtime, tenant.supplier, asset)
    assert isinstance(documents.run_once(runtime, tenant.worker, store, parsed), str)
    with pytest.raises(Conflict):
        documents.finish_parse(runtime, tenant.worker, new, parsed(new, body))


def test_original_parser_process_and_http_document_flow(database, authentication, tenant, source):
    admin, runtime = database
    store, product, asset, _ = source
    result = documents.run_once(runtime, tenant.worker, store, parse)
    assert isinstance(result, str), result
    assert len(documents.get(runtime, tenant.supplier, asset)["parse"]["body"]["days"]) == 3
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.supplier.organization_id: ["supplier_admin"]})
    with TestClient(create_app(runtime, authentication, object_store=store)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        listed = client.get(f"/v1/documents?product_id={product['id']}", headers=headers)
        assert listed.status_code == 200 and listed.json()["items"][0]["id"] == str(asset)
        assert client.get(f"/v1/documents/{asset}", headers=headers).json()["parse"]["id"] == result
        response = client.get(f"/v1/documents/{asset}/file", headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get(f"/v1/documents/{asset}/file").status_code == 401
        upload = client.post(
            f"/v1/documents?product_id={product['id']}",
            headers={**headers, "X-File-Name": "ACME.pdf"},
            content=b"not PDF",
        )
        assert upload.status_code == 409
        detail = documents.get(runtime, tenant.supplier, asset)
        proposal = client.post(
            "/v1/document-proposals",
            headers=headers,
            json={
                "target_id": str(product["id"]),
                "expected_version": detail["product_version"],
                "parse_id": str(detail["parse"]["id"]),
                "content": complete_fixture_review(detail["parse"]["body"]).model_dump(mode="json"),
                "note": "ACME HTTP 文档复核",
            },
        )
        assert proposal.status_code == 201
        listing = client.get("/v1/changes", headers=headers).json()["items"]
        change = next(row for row in listing if row["id"] == proposal.json()["id"])
        assert change["target_label"] == product["name"]
        assert change["document_source"]["asset_id"] == str(asset)
        assert change["document_source"]["parse_id"] == result
        assert change["document_source"]["file_hash"] == detail["file_hash"]
        client.post(
            f"/v1/changes/{change['id']}/approve",
            headers=headers,
            json={"payload_hash": change["payload_hash"]},
        ).raise_for_status()
        published = client.post(f"/v1/changes/{change['id']}/apply", headers=headers)
        assert published.status_code == 200
        refreshed = client.get(f"/v1/documents/{asset}", headers=headers).json()
        assert refreshed["current_product_version"] == detail["product_version"] + 1
        assert refreshed["publications"][0]["id"] == published.json()["document_id"]


async def test_advisor_reads_only_current_reviewed_document_and_keeps_old_versions(
    database, tenant, source
):
    admin, runtime = database
    store, product, asset, body = source
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    context = ShoppingSessionContext(session_id="ACME", user_id=str(tenant.buyer.user_id))
    route = "WP-" + str(product["id"])
    assert "尚未发布行程" in (await backend.get_product_details(context, route)).specs["文档状态"]
    documents.run_once(runtime, tenant.worker, store, parsed)
    assert documents.current(runtime, tenant.buyer, product["id"]) is None
    staged = proposal(runtime, tenant, asset)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    result = changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    details = await backend.get_product_details(context, route)
    assert '"day": 3' in details.specs["已复核行程"]
    assert details.specs["文档版本ID"] == result["document_id"]
    old = documents.published(runtime, tenant.buyer, UUID(result["document_id"]))
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_product SET version=version+1,name='ACME 更新线路' WHERE id=:id"),
            {"id": product["id"]},
        )
    assert documents.current(runtime, tenant.buyer, product["id"]) is None
    assert "尚未发布行程" in (await backend.get_product_details(context, route)).specs["文档状态"]
    historical = documents.published(runtime, tenant.buyer, UUID(result["document_id"]))
    assert historical["historical"] and historical["body"] == old["body"]
    # The identical file bound to a changed product creates new evidence, not inherited review.
    newer = documents.upload(runtime, tenant.supplier, store, product["id"], "ACME.docx", body)
    assert not newer["duplicate"] and UUID(newer["id"]) != asset
    with pytest.raises(Forbidden):
        documents.download(runtime, tenant.buyer, store, UUID(newer["id"]))
    (store.root / str(tenant.supplier.organization_id) / str(asset)).write_bytes(b"corrupted")
    with pytest.raises(SourceError, match="DOCUMENT_OBJECT_UNAVAILABLE"):
        documents.download(runtime, tenant.buyer, store, asset)


async def test_same_file_in_other_supplier_does_not_inherit_access_or_review(
    database, tenant, source
):
    from types import SimpleNamespace

    from cloud_warehouse.admin import onboard
    from cloud_warehouse.persistence import Principal

    admin, runtime = database
    store, product, asset, body = source
    documents.run_once(runtime, tenant.worker, store, parsed)
    ids = onboard(admin, "ACME Other Supplier", "ACME Other Buyer", f"{uuid4()}@acme.example")
    organization = UUID(ids["supplier_id"])
    user = auth.create_user(
        admin, f"{uuid4()}@acme.example", PASSWORD, {organization: ["supplier_admin"]}
    )
    other = SimpleNamespace(
        supplier=Principal(user, organization),
        worker=Principal(UUID(ids["worker_id"]), organization),
    )
    await synchronize(runtime, other.worker, UUID(ids["connection_id"]), Connector())
    product2 = list_products(runtime, other.supplier)[0]
    asset2 = UUID(
        documents.upload(runtime, other.supplier, store, product2["id"], "ACME.docx", body)["id"]
    )
    assert documents.get(runtime, other.supplier, asset2)["status"] == "queued"
    for identifier in (asset,):
        with pytest.raises(Forbidden):
            documents.get(runtime, other.supplier, identifier)
        with pytest.raises(Forbidden):
            documents.download(runtime, other.supplier, store, identifier)
    documents.run_once(runtime, other.worker, store, parsed)
    staged = proposal(runtime, other, asset2)
    # A parse ID from another supplier cannot be rebound to our product.
    forged = {**staged["preview"], "target_id": str(product["id"])}
    with pytest.raises(Forbidden):
        changes.stage(runtime, tenant.supplier, "document", forged)
    changes.approve(runtime, other.supplier, UUID(staged["id"]), staged["payload_hash"])
    result = changes.apply(runtime, other.supplier, UUID(staged["id"]))
    with pytest.raises(Forbidden):
        documents.published(runtime, tenant.buyer, UUID(result["document_id"]))
    with pytest.raises(Forbidden):
        documents.download(runtime, tenant.buyer, store, asset2)
    assert documents.current(runtime, tenant.buyer, product["id"]) is None


def test_pdf_parser_process_accepts_text_and_rejects_scans():
    from tour.api.pdf_source import PDFTOTEXT
    from tour.api.tests.test_pdf_source import _pdf

    if PDFTOTEXT is None:
        pytest.skip("pdftotext is not installed")
    body = _pdf(
        [
            line
            for day in range(1, 4)
            for line in [f"D{day} ACME city", *[f"ACME itinerary detail {n}" for n in range(8)]]
        ]
    )
    work = {
        "product_snapshot": {
            "external_id": "ACME-1",
            "code": "ACME",
            "name": "ACME route",
            "days": 3,
            "gateway": "ACME",
        },
        "file_name": "ACME.pdf",
        "file_hash": hashlib.sha256(body).hexdigest(),
    }
    result = parse(work, body)
    assert "pdf" in result.source.parser and result.source.etag == work["file_hash"]
    assert [(location.day, location.pages) for location in result.source.page_locations] == [
        (1, [1]),
        (2, [1]),
        (3, [1]),
    ]
    for content, code in [
        (_pdf([]), "DOCUMENT_TEXT_MISSING"),
        (_pdf(["ACME short cover"]), "DOCUMENT_TEXT_INSUFFICIENT"),
        (b"%PDF-broken ACME file", "DOCUMENT_PDF_READ_FAILED"),
    ]:
        with pytest.raises(documents.DocumentParseError, match=code):
            parse(work, content)


def test_pdf_locations_persist_through_review_without_becoming_human_field_evidence(
    database, tenant, source
):
    from tour.api.pdf_source import PDFTOTEXT
    from tour.api.tests.test_pdf_source import _pdf_pages

    if PDFTOTEXT is None:
        pytest.skip("pdftotext is not installed")
    _, runtime = database
    store, product, old_asset, _ = source
    documents.run_once(runtime, tenant.worker, store, parsed)
    assert documents.get(runtime, tenant.supplier, old_asset)["status"] == "parsed"
    details = [f"ACME itinerary detail number {index}" for index in range(12)]
    body = _pdf_pages(
        [
            ["ACME source cover", "D1 ACME city", *details],
            [],
            ["ACME first day continued", "D2 ACME city", *details],
            ["D3 ACME city", *details],
        ]
    )
    asset = documents.upload(
        runtime, tenant.supplier, store, product["id"], "ACME-pages.pdf", body
    )["id"]
    documents.run_once(runtime, tenant.worker, store, parse)
    detail = documents.get(runtime, tenant.supplier, UUID(asset))
    original = RouteDoc.model_validate(detail["parse"]["body"])
    assert [(loc.section, loc.day, loc.pages) for loc in original.source.page_locations] == [
        ("cover", None, [1]),
        ("itinerary", 1, [1, 3]),
        ("itinerary", 2, [3]),
        ("itinerary", 3, [4]),
    ]
    command = documents.DocumentReview(
        target_id=product["id"],
        expected_version=product["version"],
        parse_id=detail["parse"]["id"],
        content=original.model_copy(deep=True),
        note="ACME 原文核对",
    )
    command.content.source.page_locations[1].pages = [4]
    with pytest.raises(Conflict, match="不可改写文档来源"):
        documents.stage(runtime, tenant.supplier, command)
    command.content = original.model_copy(deep=True)
    command.content.days[0].text = "ACME 人工补充内容，另经人工核实"
    command.content = complete_fixture_review(command.content)
    staged = documents.stage(runtime, tenant.supplier, command)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    result = changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    publication = documents.published(runtime, tenant.supplier, UUID(result["document_id"]))
    assert (
        publication["body"]["source"]["page_locations"]
        == detail["parse"]["body"]["source"]["page_locations"]
    )
    assert publication["field_sources"]["/days/0/text"]["method"] == "human_review"
    assert (
        publication["field_sources"]["/source/page_locations/1/pages/0"]["method"]
        == "parser_metadata"
    )
    assert documents.download(runtime, tenant.supplier, store, UUID(asset))["body"] == body


@pytest.mark.parametrize("code", sorted(documents.DocumentParseError.CODES) + ["secret detail"])
def test_parse_failure_codes_are_persisted_without_source_details(database, tenant, source, code):
    _, runtime = database
    store, _, asset, _ = source

    def fail(*_):
        raise documents.DocumentParseError(code)

    result = documents.run_once(runtime, tenant.worker, store, fail)
    expected = code if code in documents.DocumentParseError.CODES else "DOCUMENT_PARSE_FAILED"
    assert result == {"id": str(asset), "status": "failed", "error": expected}
    detail = documents.get(runtime, tenant.supplier, asset)
    assert detail["last_error"] == expected
    assert detail["parse"] is None and detail["publications"] == []
    assert "secret" not in str(detail)
    documents.retry(runtime, tenant.supplier, asset)
    assert documents.get(runtime, tenant.supplier, asset)["last_error"] is None


def test_merchant_itinerary_draft_stays_private_and_never_publishes(database, tenant, source):
    from cloud_warehouse import merchant_business
    from cloud_warehouse.persistence import Principal

    admin, runtime = database
    store, product, asset, _ = source
    assert documents.run_once(runtime, tenant.worker, store, parsed)
    detail = merchant_business.route(runtime, tenant.supplier, product["id"])
    assert detail["document"] is None
    assert len(detail["draft_document"]["body"]["days"]) == 3
    with pytest.raises(Forbidden):
        merchant_business.route(runtime, tenant.buyer, product["id"])
    with admin.begin() as conn:
        editor = uuid4()
        conn.execute(
            text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
            {"id": editor, "email": f"{editor}@acme.example"},
        )
        conn.execute(
            text(
                "INSERT INTO membership(user_id,organization_id,roles) VALUES(:id,:org,ARRAY['inventory_manager'])"
            ),
            {"id": editor, "org": tenant.supplier.organization_id},
        )
        assert (
            conn.scalar(
                text("SELECT published_document_id FROM supplier_product WHERE id=:id"),
                {"id": product["id"]},
            )
            is None
        )
    stock_user = Principal(editor, tenant.supplier.organization_id)
    assert merchant_business.route(runtime, stock_user, product["id"])["draft_document"] is None
