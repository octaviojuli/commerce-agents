"""Two bounded child processes: uncredentialed file decoding, then model-only extraction."""

import base64
import dataclasses
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cloud_warehouse.documents import DocumentParseError

MAX_WIRE = 64_000_000
NOT_ITINERARY_EXIT = 3  # the model stage found the file is not a tour itinerary


def _child(stage, data, settings, timeout):
    env = {k: os.environ[k] for k in ("PATH", "LANG", "SYSTEMROOT") if k in os.environ}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    if stage == "model":
        env.update(
            {
                k: str(settings[k])
                for k in (
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_BASE_URL",
                    "ROUTE_KIT_MODEL",
                    "TOUR_MODEL",
                    "ROUTE_KIT_CACHE",
                )
                if settings.get(k)
            }
        )
    with (
        tempfile.TemporaryFile() as source,
        tempfile.TemporaryFile() as output,
        tempfile.TemporaryFile() as errors,
    ):
        source.write(data)
        source.seek(0)
        process = subprocess.Popen(
            [sys.executable, "-m", "tour.api.route_kit_worker", stage],
            stdin=source,
            stdout=output,
            stderr=errors,
            env=env,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise DocumentParseError("DOCUMENT_PARSE_TIMEOUT") from None
        if process.returncode == NOT_ITINERARY_EXIT and stage == "model":
            # Not a route (a product sheet, a form): the merchant is asked to check the file.
            raise DocumentParseError("DOCUMENT_TEXT_INSUFFICIENT")
        if process.returncode:
            raise DocumentParseError(
                "DOCUMENT_PARSE_OPEN_FAILED" if stage == "read" else "DOCUMENT_PARSE_DAYS_FAILED"
            )
        output.seek(0)
        body = output.read(MAX_WIRE + 1)
        if len(body) > MAX_WIRE:
            raise DocumentParseError("DOCUMENT_PARSE_OUTPUT_LIMIT")
        return body


def parse(work, body, *, settings=None):
    from route_kit.models import RouteContent

    settings = os.environ if settings is None else settings
    if not (settings.get("ROUTE_KIT_MODEL") or settings.get("TOUR_MODEL")) or not settings.get(
        "ANTHROPIC_API_KEY"
    ):
        raise DocumentParseError("DOCUMENT_PARSER_UNAVAILABLE")
    request = dict(
        name=work["file_name"], sha256=work["file_hash"], product=work["product_snapshot"]
    )
    decoded = _child("read", json.dumps(request).encode() + b"\n" + body, {}, 120)
    # Cache is private per supplier/connection, never shared across tenant identities.
    cache_root = settings.get("ROUTE_KIT_CACHE", "/tmp/route-kit-cache")
    cache = Path(cache_root) / str(work["supplier_org_id"]) / str(work["connection_id"])
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = _child("model", decoded, {**settings, "ROUTE_KIT_CACHE": str(cache)}, 600)
    from cloud_warehouse.documents import ParsedDocument

    result = json.loads(result)
    return ParsedDocument(
        RouteContent.model_validate(result["content"]),
        media={"cover": base64.b64decode(result["cover"], validate=True)}
        if result.get("cover")
        else {},
    )


def main():
    import resource

    from route_kit import reader, segment, vision

    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WIRE, MAX_WIRE))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024**2, 1536 * 1024**2))
    if sys.argv[1] == "read":
        resource.setrlimit(resource.RLIMIT_CPU, (100, 100))
        header = json.loads(sys.stdin.buffer.readline(50_000))
        data = sys.stdin.buffer.read(20_000_001)
        if len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != header["sha256"]:
            raise ValueError("INVALID_SOURCE")
        suffix = Path(header["name"]).suffix.lower()
        if suffix not in (".pdf", ".docx"):
            raise ValueError("UNSUPPORTED_FORMAT")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ("source" + suffix)
            path.write_bytes(data)
            doc = reader.read(path, pictures=True)
        doc.file_name = header["name"]
        # All image decoding/re-encoding happens here, with no credentials.
        prepared = []
        for picture in doc.pictures:
            image = vision._jpeg(picture.data, vision.MAX_SIDE)
            prepared.append(base64.b64encode(image).decode() if image else "")
        envelope = dataclasses.asdict(doc)
        for p, encoded in zip(envelope["pictures"], prepared, strict=True):
            p["data"] = encoded
        sys.stdout.write(json.dumps({"request": header, "document": envelope}))
    else:
        from route_kit.extract import extract
        from route_kit.llm import Model
        from route_kit.reader import NotItinerary

        from cloud_warehouse.route_kit_content import dump, prepare

        envelope = json.loads(sys.stdin.buffer.read(MAX_WIRE + 1))
        header, wire = envelope["request"], envelope["document"]
        wire["lines"] = [reader.Line(**x) for x in wire["lines"]]
        wire["pictures"] = [
            reader.Picture(**{**x, "data": base64.b64decode(x["data"], validate=True)})
            for x in wire["pictures"]
        ]
        doc = reader.Document(**wire)
        model = Model(dict(os.environ), Path(os.environ["ROUTE_KIT_CACHE"]))
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                vision.transcribe(doc, model, pool, prepared=True)
                layout = segment.segment(doc)
                product = header["product"]
                result = extract(
                    doc,
                    layout,
                    model,
                    dict(code=product["code"], name=product["name"], days=product.get("days")),
                    pool,
                    header["sha256"],
                )
                result = prepare(result)
                sys.stdout.write(
                    json.dumps(
                        {
                            "content": dump(result),
                            "cover": next(
                                (
                                    base64.b64encode(p.data).decode()
                                    for p, r in zip(doc.pictures, doc.transcribed, strict=True)
                                    if r.get("cover")
                                ),
                                "",
                            ),
                        },
                        ensure_ascii=False,
                    )
                )
        except NotItinerary:
            sys.exit(NOT_ITINERARY_EXIT)
        finally:
            model.client.close()


if __name__ == "__main__":
    main()
