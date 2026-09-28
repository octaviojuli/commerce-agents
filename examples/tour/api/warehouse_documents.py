"""Bounded subprocess bridge from warehouse document jobs to the original Tour parser."""

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from cloud_warehouse.documents import DocumentParseError
from cloud_warehouse.route_doc import RouteDoc

STAGES = frozenset({"open", "segment", "days", "terms", "validation"})


def failure_code(stderr: bytes) -> str:
    # Read only fixed markers. Source text, paths, exception messages and secrets
    # are never retained or returned, even if a library writes them to stderr.
    stages = re.findall(
        rb"(?m)^ROUTE_PARSE_STAGE=(open|segment|days|terms|validation)$", stderr[-2048:]
    )
    return (
        "DOCUMENT_PARSE_" + stages[-1].decode().upper() + "_FAILED"
        if stages
        else "DOCUMENT_PARSE_FAILED"
    )


def parse_native(work, body):
    """Native parsing in a bounded child without database, ERP or model credentials."""
    request = {
        "product": work["product_snapshot"],
        "name": work["file_name"],
        "hash": work["file_hash"],
    }
    examples = str(Path(__file__).resolve().parents[2])
    environment = {
        key: value for key, value in os.environ.items() if key in ("PATH", "SYSTEMROOT", "LANG")
    }
    environment["PYTHONPATH"] = examples
    with (
        tempfile.TemporaryFile() as source,
        tempfile.TemporaryFile() as output,
        tempfile.TemporaryFile() as errors,
    ):
        source.write(json.dumps(request).encode() + b"\n" + body)
        source.seek(0)
        process = subprocess.Popen(
            [sys.executable, "-m", "tour.api.warehouse_documents"],
            stdin=source,
            stdout=output,
            stderr=errors,
            env=environment,
            start_new_session=True,
        )
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise DocumentParseError("DOCUMENT_PARSE_TIMEOUT") from None
        errors.seek(0, 2)
        errors.seek(max(0, errors.tell() - 2048))
        diagnostic = failure_code(errors.read(2048))
        if process.returncode:
            raise DocumentParseError(diagnostic)
        output.seek(0)
        result = output.read(12_000_001)
        if len(result) > 12_000_000:
            raise DocumentParseError("DOCUMENT_PARSE_OUTPUT_LIMIT")
        payload = json.loads(result)
        if isinstance(payload, dict) and "error" in payload:
            raise DocumentParseError(
                diagnostic if payload["error"] == "DOCUMENT_PARSE_FAILED" else payload["error"]
            )
        return RouteDoc.model_validate_json(result)


def parse(work, body, *, settings=None):
    settings = os.environ if settings is None else settings
    if settings.get("ROUTE_KIT_ENABLED") == "true":
        from .route_kit_worker import parse as parse_kit

        return parse_kit(work, body, settings=settings)
    doc = parse_native(work, body)
    if not settings.get("TOUR_EXTRACTION_MODEL"):
        return doc
    from cloud_warehouse.documents import ParsedDocument
    from cloud_warehouse.route_extraction import extract

    from .route_editor_model import ModelEditor

    try:
        model = ModelEditor(settings)
    except ValueError:
        return doc
    try:
        merged, fields, report = extract(doc, model)
        merged.quality.needs_review.append("模型字段抽取状态：" + report["status"])
        if report.get("code"):
            merged.quality.needs_review.append(report["code"])
        return ParsedDocument(merged, field_sources=fields)
    finally:
        model.close()


def main():
    import resource

    # Source bytes arrive on stdin. The child receives no private credentials.
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12_000_000, 12_000_000))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    from .erp_client import RouteRecord
    from .pdf_source import ImageOnlyPdf, PdfReadError
    from .route_parser import parse_route

    def stage(value):
        if value in STAGES:
            print("ROUTE_PARSE_STAGE=" + value, file=sys.stderr, flush=True)

    stage("open")
    request = json.loads(sys.stdin.buffer.readline(50_000))
    body = sys.stdin.buffer.read(20_000_001)
    if len(body) > 20_000_000:
        raise ValueError("Input too large")
    product = request["product"]
    record = RouteRecord(
        route_id=int(product["external_id"]) if product["external_id"].isdigit() else 0,
        route_code=product["code"],
        route_name=product["name"],
        days=product["days"] or 0,
        depart_city=product["gateway"] or "",
        company_name="",
        from_price=0,
        tags=(),
        itinerary_tags=(),
        price_tags=(),
        features=(),
        image_url=None,
        attachment_name=request["name"],
        attachment_url="",
    )
    try:
        result = parse_route(record, body, etag=request["hash"], progress=stage)
    except ImageOnlyPdf as error:
        code = "DOCUMENT_TEXT_MISSING" if error.text_chars == 0 else "DOCUMENT_TEXT_INSUFFICIENT"
        sys.stdout.write(json.dumps({"error": code}))
        return
    except PdfReadError as error:
        sys.stdout.write(json.dumps({"error": DocumentParseError(error.code).code}))
        return
    except Exception as error:
        code = getattr(error, "code", "DOCUMENT_PARSE_FAILED")
        sys.stdout.write(json.dumps({"error": DocumentParseError(code).code}))
        return
    sys.stdout.write(result.model_dump_json())


if __name__ == "__main__":
    main()
