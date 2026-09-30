"""Parse itinerary attachments into route JSON, detail pages and a quality index.

    python scripts/run.py --file route.docx --out out/ --env model.env
    python scripts/run.py --corpus corpus/ --out out/ --env model.env [--only CODE ...]
    python scripts/run.py ... --vision        # also transcribe text in pictures

A corpus directory holds the files plus ``manifest.json``: a list of
``{"code", "name", "days", "file"}``; entries without ``file`` get a "no attachment" page.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from route_kit import render, vision  # noqa: E402
from route_kit.extract import extract, file_sha256  # noqa: E402
from route_kit.llm import Model, load_env  # noqa: E402
from route_kit.models import RouteContent  # noqa: E402
from route_kit.reader import ReadError, read  # noqa: E402
from route_kit.segment import segment  # noqa: E402


def stale(out: Path, slug: str) -> None:
    """A failed rerun leaves no page from an earlier run behind."""
    for name in (f"{slug}.json", f"{slug}.html", f"{slug}.cover.jpg"):
        (out / "routes" / name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--corpus", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--env",
        type=Path,
        help="Env file with ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ROUTE_KIT_MODEL or TOUR_MODEL",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument(
        "--render-only", action="store_true", help="Re-render pages from existing JSON"
    )
    parser.add_argument(
        "--vision",
        action="store_true",
        help="Transcribe text in PDF image pages, scanned PDFs and DOCX pictures (model calls)",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "routes").mkdir(exist_ok=True)
    if args.file:
        entries = [{"code": args.file.stem, "name": "", "days": None, "file": args.file.name}]
        base = args.file.parent
    else:
        entries = json.loads((args.corpus / "manifest.json").read_text())
        base = args.corpus
    if args.only:
        entries = [e for e in entries if e["code"] in args.only]
    for entry in entries:  # codes name output files; files must stay inside the corpus
        if not re.fullmatch(r"[\w-]{1,64}", str(entry.get("code", ""))):
            sys.exit(f"manifest: invalid code {entry.get('code')!r}")
        if entry.get("file") and not (base / entry["file"]).resolve().is_relative_to(
            base.resolve()
        ):
            sys.exit(f"manifest: file outside the corpus for {entry['code']}")

    summaries = []
    model = None if args.render_only else Model(load_env(args.env), args.out / ".cache")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for entry in entries:
            slug = entry["code"]
            json_path = args.out / "routes" / f"{slug}.json"
            started = time.time()
            if not entry.get("file"):
                summaries.append({**entry, "status": "no_attachment"})
                render.write_missing(args.out / "routes" / f"{slug}.html", entry)
                print(f"{slug}: no attachment", flush=True)
                continue
            if args.render_only:
                if not json_path.exists():
                    continue
                # A hand-edited file is checked against the contract before it is rendered.
                content = json.loads(
                    RouteContent.model_validate_json(json_path.read_text()).model_dump_json(
                        by_alias=True
                    )
                )
            else:
                try:
                    doc = read(base / entry["file"], pictures=args.vision)
                    cover = None
                    if doc.pictures:
                        vision.transcribe(doc, model, pool)
                        cover = vision.cover_image(doc)
                    layout = segment(doc)
                    result = extract(
                        doc, layout, model, entry, pool, file_sha256(base / entry["file"])
                    )
                except ReadError as error:
                    stale(args.out, slug)
                    summaries.append({**entry, "status": "unreadable", "error": error.code})
                    print(f"{slug}: {error.code}", flush=True)
                    continue
                except Exception as error:  # one bad file never stops the batch
                    stale(args.out, slug)
                    code = f"FAILED: {type(error).__name__}"
                    summaries.append({**entry, "status": "failed", "error": code})
                    print(f"{slug}: {code}\n{traceback.format_exc(limit=3)}", flush=True)
                    continue
                cover_path = args.out / "routes" / f"{slug}.cover.jpg"
                if cover:
                    cover_path.write_bytes(cover)
                    result.source.cover_image = cover_path.name
                else:
                    cover_path.unlink(missing_ok=True)
                content = json.loads(result.model_dump_json(by_alias=True))
                json_path.write_text(json.dumps(content, ensure_ascii=False, indent=1))
            cover_file = (content.get("source") or {}).get("cover_image")
            cover_bytes = None
            if cover_file and (args.out / "routes" / cover_file).exists():
                cover_bytes = (args.out / "routes" / cover_file).read_bytes()
            render.write_route(args.out / "routes" / f"{slug}.html", content, cover_bytes)
            q = content["quality"]
            summaries.append(
                {
                    **entry,
                    "status": "ok",
                    "title": content["title"],
                    "days_found": q["days_found"],
                    "days_extracted": q["days_extracted"],
                    "units": q["units"],
                    "mapped": q["mapped_units"],
                    "direct": q.get("direct_units", q["mapped_units"]),
                    "auto_attached": q.get("auto_attached", 0),
                    "risky_unmapped": sum(1 for u in q["unmapped"] if u["risky"]),
                    "confidence": q.get("confidence"),
                    "review_reasons": q.get("review_reasons", []),
                    "issues": len(q["issues"]),
                }
            )
            print(
                f"{slug}: {q['days_extracted']}/{q['days_found']} days, "
                f"direct {q.get('direct_units')}/{q['units']}, auto {q.get('auto_attached')}, "
                f"issues {len(q['issues'])}, {time.time() - started:.0f}s",
                flush=True,
            )
    if model:
        print(f"model calls {model.calls}, cached {model.cached}")
    summary_path = args.out / "summary.json"
    if args.only and summary_path.exists():  # a partial run keeps the other routes listed
        done = {s["code"] for s in summaries}
        previous = json.loads(summary_path.read_text())
        listed = (
            [e["code"] for e in json.loads((base / "manifest.json").read_text())]
            if (args.corpus)
            else [s["code"] for s in previous]
        )
        summaries = [s for s in previous if s["code"] not in done] + summaries
        summaries = [s for s in summaries if s["code"] in listed]  # removed codes drop out
        summaries.sort(key=lambda s: listed.index(s["code"]))
    render.write_index(args.out / "index.html", summaries)
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
