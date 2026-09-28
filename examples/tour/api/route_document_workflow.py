"""Local, resumable entry point for the same document parser used by warehouse jobs."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import dotenv_values

from cloud_warehouse.documents import file_type
from cloud_warehouse.integrations import fingerprint
from cloud_warehouse.route_content import candidate, issues
from cloud_warehouse.route_doc import RouteDoc
from cloud_warehouse.route_editing import POLICY, rewrite_day

from .warehouse_documents import parse


def edit_step(args):
    from .route_editor_model import ModelEditor

    if not args.model_config:
        raise ValueError("--edit requires an explicitly authorized --model-config")
    original = json.loads((args.output / "candidate.json").read_text())
    digest = fingerprint(original)
    state_file = args.output / "editing.private.json"
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state["input_hash"] != digest or state["policy"] != POLICY:
            raise ValueError("Editing output belongs to another source or policy")
        doc = RouteDoc.model_validate(state["body"])
    else:
        doc = candidate(original, normalize_titles=True)
        state = {"policy": POLICY, "input_hash": digest, "next_day": 0, "reports": []}
    valid = bool(doc.days) and [d.day for d in doc.days] == list(range(1, doc.summary.days + 1))
    index = state["next_day"]
    if valid and index < len(doc.days):
        editor = ModelEditor(dotenv_values(args.model_config))
        try:
            doc.days[index], report = rewrite_day(doc.days[index], editor)
        finally:
            editor.close()
        state["reports"].append(dict(report, day=doc.days[index].day))
        state["next_day"] += 1
        state["model"] = editor.model
    elif not valid and not state["reports"]:
        state["reports"].append({"status": "retained", "code": "DAYS_INCOMPLETE"})
    state["body"] = doc.model_dump(mode="json")
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    (args.output / "edited-candidate.json").write_text(doc.model_dump_json(indent=2))
    print(
        json.dumps(
            {
                "status": "complete"
                if not valid or state["next_day"] == len(doc.days)
                else "editing",
                "days_completed": state["next_day"],
                "total_days": len(doc.days),
                "warehouse_written": False,
                "published": False,
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True, help="Private product snapshot JSON")
    parser.add_argument(
        "--output", type=Path, required=True, help="Private resumable result directory"
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help="Explicitly authorize model editing of extracted itinerary text",
    )
    parser.add_argument("--model-config", type=Path, help="Private existing model environment file")
    from .route_parser import PARSER_VERSION

    args = parser.parse_args()
    settings = dotenv_values(args.model_config) if args.model_config else os.environ
    from cloud_warehouse.route_extraction import POLICY as EXTRACTION_POLICY

    extraction_model = settings.get("TOUR_EXTRACTION_MODEL")
    extraction_policy = EXTRACTION_POLICY if extraction_model else None
    os.umask(0o077)
    body = args.file.read_bytes()
    file_type(args.file.name, body)
    product = json.loads(args.product.read_text())
    if not all(key in product for key in ("external_id", "code", "name", "days", "gateway")):
        raise ValueError("Product snapshot requires external_id, code, name, days and gateway")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_path = args.output / "work.private.json"
    digest = hashlib.sha256(body).hexdigest()
    if state_path.exists():
        work = json.loads(state_path.read_text())
        if (
            work["file_hash"] != digest
            or work["product_snapshot"] != product
            or work.get("parser") != PARSER_VERSION
            or work.get("extraction_policy") != extraction_policy
            or work.get("extraction_model") != extraction_model
        ):
            raise ValueError("Output directory belongs to another input or parser mode")
    else:
        work = {
            "product_snapshot": product,
            "file_name": args.file.name,
            "file_hash": digest,
            "parser": PARSER_VERSION,
            "extraction_policy": extraction_policy,
            "extraction_model": extraction_model,
        }
    if (args.output / "candidate.json").exists():
        if args.edit:
            edit_step(args)
            return
        print(
            json.dumps({"status": "complete", "cached": True, "output": str(args.output.resolve())})
        )
        return
    document = parse(work, body, settings=settings) if args.model_config else parse(work, body)
    from cloud_warehouse.documents import ParsedDocument

    if isinstance(document, ParsedDocument):
        (args.output / "field-sources.private.json").write_text(
            json.dumps(document.field_sources, ensure_ascii=False, indent=2)
        )
        document = document.document
    state_path.write_text(json.dumps(work, ensure_ascii=False))
    (args.output / "candidate.json").write_text(document.model_dump_json(indent=2))
    validation = {
        "input_sha256": digest,
        "parser": document.source.parser,
        "days": len(document.days),
        "issues": issues(document),
        "needs_review": document.quality.needs_review,
        "published": False,
        "warehouse_written": False,
    }
    (args.output / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2)
    )
    if args.edit:
        edit_step(args)
        return
    print(
        json.dumps(
            {
                "status": "complete",
                "days": len(document.days),
                "issues": len(validation["issues"]),
                "output": str(args.output.resolve()),
                "warehouse_written": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
