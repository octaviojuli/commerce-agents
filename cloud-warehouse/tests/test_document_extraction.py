from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import documents
from cloud_warehouse.integrations import fingerprint
from cloud_warehouse.persistence import Forbidden, transaction

from .test_documents import parsed
from .test_documents import source as source


def test_raw_extraction_is_immutable_scoped_and_bound_to_original_hash(database, tenant, source):
    _, runtime = database
    store, _, asset, _ = source

    def external(work, body):
        doc = parsed(work, body)
        extraction = {"input_sha256": work["file_hash"], "markdown": "ACME source", "blocks": []}
        doc.source.extraction_method = "hybrid"
        doc.source.extraction_hash = fingerprint(extraction)
        return documents.ParsedDocument(
            doc, {"extraction": extraction, "comparison": {"selected": "native"}}
        )

    identifier = UUID(documents.run_once(runtime, tenant.worker, store, external))
    evidence = documents.extraction_detail(runtime, tenant.supplier, asset, identifier)
    assert evidence["available"] and evidence["evidence"]["extraction"]["markdown"] == "ACME source"
    with pytest.raises(Forbidden):
        documents.extraction_detail(runtime, tenant.buyer, asset, identifier)
    with (
        transaction(runtime, tenant.worker) as conn,
        pytest.raises(DBAPIError),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE document_parse SET extraction='{}'::jsonb WHERE id=:id"),
            {"id": identifier},
        )


def test_retirement_preserves_jobs_and_requires_explicit_new_generation(
    database, tenant, source, monkeypatch
):
    import importlib.util
    from pathlib import Path

    admin, runtime = database
    store, _, asset, _ = source
    with admin.begin() as conn:
        conn.execute(
            text("""INSERT INTO document_extraction_job(asset_id,generation,supplier_org_id,connection_id,product_id,payload,not_before,expires_at)
          SELECT id,parse_generation,supplier_org_id,connection_id,product_id,'{"history":"ACME"}'::jsonb,now(),now()+interval '30 minutes'
          FROM document_asset WHERE id=:id"""),
            {"id": asset},
        )
    spec = importlib.util.spec_from_file_location(
        "retirement", Path(__file__).parents[1] / "migrations/versions/0037_native_documents.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with admin.begin() as conn:
        monkeypatch.setattr(module.op, "execute", lambda sql: conn.execute(text(sql)))
        module.upgrade()
        assert (
            conn.scalar(
                text("SELECT payload->>'history' FROM document_extraction_job WHERE asset_id=:id"),
                {"id": asset},
            )
            == "ACME"
        )
    row = documents.get(runtime, tenant.supplier, asset)
    assert row["status"] == "failed" and row["last_error"] == "DOCUMENT_EXTRACTION_RETIRED"
    assert documents.claim(runtime, tenant.worker) is None
    documents.retry(runtime, tenant.supplier, asset)
    work = documents.claim(runtime, tenant.worker)
    assert work["parse_generation"] == 2 and "extraction_state" not in work
    documents.finish_parse(
        runtime,
        tenant.worker,
        work,
        parsed(work, store.read(tenant.supplier.organization_id, asset, work["file_hash"])),
    )
    with (
        transaction(runtime, tenant.worker) as conn,
        pytest.raises(DBAPIError),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE document_extraction_job SET payload='{}'::jsonb WHERE asset_id=:id"),
            {"id": asset},
        )
