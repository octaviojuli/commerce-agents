"""Native subprocess diagnostics never disclose supplier text or credentials."""

import pytest

from cloud_warehouse.documents import DocumentParseError
from tour.api.tests.test_itinerary_source import docx, paragraph
from tour.api.warehouse_documents import failure_code, parse


def test_diagnostic_tail_only_returns_fixed_stage_code():
    assert (
        failure_code(b"private-path key=secret source-text\nROUTE_PARSE_STAGE=terms\nprivate text")
        == "DOCUMENT_PARSE_TERMS_FAILED"
    )
    assert failure_code(b"private-path key=secret source-text") == "DOCUMENT_PARSE_FAILED"
    assert failure_code(b"ROUTE_PARSE_STAGE=open\n" + b"x" * 3000) == "DOCUMENT_PARSE_FAILED"
    assert failure_code(b"ROUTE_PARSE_STAGE=terms secret=private") == "DOCUMENT_PARSE_FAILED"


def test_native_child_ignores_external_mode_and_credentials(monkeypatch):
    import hashlib

    body = docx(paragraph("第1天 ACME 城"), paragraph("ACME 公园外观。"))
    monkeypatch.setenv("TOUR_DOCUMENT_MODE", "untrusted-obsolete-value")
    doc = parse(
        {
            "product_snapshot": {
                "external_id": "1",
                "code": "ACME",
                "name": "ACME",
                "days": 1,
                "gateway": "ACME",
            },
            "file_name": "ACME.docx",
            "file_hash": hashlib.sha256(body).hexdigest(),
        },
        body,
    )
    assert doc.source.extraction_method == "native" and len(doc.days) == 1


def test_invalid_pdf_returns_classified_code_without_stderr_text():
    with pytest.raises(DocumentParseError) as error:
        parse(
            {
                "product_snapshot": {
                    "external_id": "1",
                    "code": "ACME",
                    "name": "ACME",
                    "days": 1,
                    "gateway": "ACME",
                },
                "file_name": "ACME.pdf",
                "file_hash": "a" * 64,
            },
            b"%PDF-PRIVATE-PATH-SECRET",
        )
    assert error.value.code == "DOCUMENT_PDF_READ_FAILED"
    assert "PRIVATE" not in str(error.value)


def test_skill_model_file_controls_extraction_without_changing_process_environment(
    tmp_path, monkeypatch, capsys
):
    import json
    import os

    from cloud_warehouse.route_doc import RouteDoc
    from tour.api.route_document_workflow import main

    attachment = tmp_path / "ACME.docx"
    attachment.write_bytes(docx(paragraph("第1天 ACME 城"), paragraph("ACME 公园外观。")))
    product = tmp_path / "product.json"
    product.write_text(
        json.dumps(
            {"external_id": "1", "code": "ACME", "name": "ACME", "days": 1, "gateway": "ACME"}
        )
    )
    config = tmp_path / "model.env"
    config.write_text("TOUR_EXTRACTION_MODEL=ACME-explicit\n")
    output = tmp_path / "result"
    captured = []

    class Model:
        def __init__(self, settings):
            captured.append(dict(settings))

        def __call__(self, prompt, data, schema):
            return schema(document=RouteDoc.model_validate(data["output_template"]), citations={})

        def close(self):
            pass

    monkeypatch.setattr("tour.api.route_editor_model.ModelEditor", Model)
    monkeypatch.setenv("TOUR_EXTRACTION_MODEL", "ACME-process")
    monkeypatch.setattr(
        "sys.argv",
        [
            "workflow",
            "--file",
            str(attachment),
            "--product",
            str(product),
            "--output",
            str(output),
            "--model-config",
            str(config),
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    assert captured == [{"TOUR_EXTRACTION_MODEL": "ACME-explicit"}]
    assert os.environ["TOUR_EXTRACTION_MODEL"] == "ACME-process"
    original = (output / "candidate.json").read_bytes()
    config.write_text("TOUR_EXTRACTION_MODEL=ACME-changed\n")
    with pytest.raises(ValueError, match="another input"):
        main()
    assert (output / "candidate.json").read_bytes() == original
