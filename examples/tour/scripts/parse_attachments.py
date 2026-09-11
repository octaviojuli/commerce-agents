# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Read the catalog's .docx 行程附件 into ``RouteDoc`` files, and rank them for review.

    python examples/tour/scripts/parse_attachments.py            # every public .docx line
    python examples/tour/scripts/parse_attachments.py --select 30 --limit 200
    python examples/tour/scripts/parse_attachments.py --schema   # data/route-schema.json only

Reads the ERP with the deployment's service account in ``examples/tour/.env`` (read-only:
``route/list`` and the attachment store), writes one ``{routeId}.json`` per line under
``$TOUR_STATE_DIR/route-docs/`` (``data/.state/route-docs/`` unset), an ``index.json`` and a
``REPORT.md`` ranking the lines by completeness, and with ``--select N`` copies the N most
complete into ``route-docs/selected/`` for the agency's product staff to review. Nothing here
is committed: the documents are the agency's own product data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from tour.api.erp_client import RouteQuery, RouteRecord  # noqa: E402
from tour.api.http_erp import HttpErpClient  # noqa: E402
from tour.api.itinerary_source import fetch_attachment  # noqa: E402
from tour.api.private_lines import load_private_line_rules  # noqa: E402
from tour.api.route_doc import RouteDoc, json_schema  # noqa: E402
from tour.api.route_parser import parse_route  # noqa: E402

EXAMPLE_DIR = HERE.parents[1]
DATA_DIR = EXAMPLE_DIR / "data"
SCHEMA_FILE = DATA_DIR / "route-schema.json"
FETCH_CONCURRENCY = 4


def load_env(path: Path) -> None:
    """``KEY=VALUE`` lines into the environment, the environment winning."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def write_schema(path: Path = SCHEMA_FILE) -> None:
    path.write_text(
        json.dumps(json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--schema", action="store_true", help="write data/route-schema.json and stop"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="output directory (default: state dir/route-docs)"
    )
    parser.add_argument("--limit", type=int, default=0, help="parse at most this many lines")
    parser.add_argument(
        "--select", type=int, default=0, help="copy the N most complete into selected/"
    )
    parser.add_argument("--include-private", action="store_true", help="parse 包团/会销 lines too")
    return parser.parse_args(argv)


def _out_dir(given: Path | None) -> Path:
    if given is not None:
        return given
    state = os.environ.get("TOUR_STATE_DIR", "").strip()
    return (Path(state) if state else DATA_DIR / ".state") / "route-docs"


async def _one(
    erp_sem: asyncio.Semaphore, record: RouteRecord, out: Path
) -> tuple[RouteRecord, RouteDoc | None, str]:
    async with erp_sem:
        fetched = await fetch_attachment(record.attachment_url or "", etag=None)
    if fetched is None:
        return record, None, "附件未取到（不是 .docx、超过大小上限或存储未响应）"
    data, etag = fetched
    try:
        doc = parse_route(record, data, etag=etag)
    except ValueError as error:
        return record, None, f"附件无法读取：{error}"
    (out / f"{record.route_id}.json").write_text(
        json.dumps(doc.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return record, doc, ""


# A document below this is not a first-round review candidate: the round is for checking
# fields that were read, not for filling in ones that were not.
SELECT_FLOOR = 0.9


def _spread(rows: list[dict], count: int) -> list[dict]:
    """The ``count`` best documents at or above ``SELECT_FLOOR`` taken one department at a
    time, so a first review round sees every department's layout and not thirty lines of
    one; the ranking within a department is the overall one, and only when the strong
    documents run out are weaker ones taken, the same way."""
    chosen: list[dict] = []
    for tier in (
        [r for r in rows if r["completeness"] >= SELECT_FLOOR],
        [r for r in rows if r["completeness"] < SELECT_FLOOR],
    ):
        by_department: dict[str, list[dict]] = {}
        for row in tier:
            by_department.setdefault(row["department"], []).append(row)
        queues = list(by_department.values())
        while len(chosen) < count and any(queues):
            for queue in queues:
                if queue and len(chosen) < count:
                    chosen.append(queue.pop(0))
    return chosen


def _reason(note: str) -> str:
    """A note as the class it counts under: the parser's per-line doubts (第4天【茶园】疑似
    购物点…, 封面写明赠送…) are one reason each, whatever line they name."""
    if "疑似购物点" in note:
        return "有景点疑似购物点，待产品确认"
    if "购物口径" in note:
        return "附件写了“不算购物店”，购物口径待产品确认"
    if note.startswith("封面写明赠送"):
        return "封面写明赠送，待核对各天的赠送标记"
    if note.startswith("附件天数"):
        return "附件天数与 ERP 天数不一致"
    return note


def _report(rows: list[dict], failures: list[dict], out: Path, selected: list[dict]) -> str:
    lines = ["# 行程附件解析报告", ""]
    lines.append(
        f"解析 {len(rows) + len(failures)} 条线路：成功 {len(rows)}，失败 {len(failures)}。"
    )
    if rows:
        bands = Counter(
            "1.0"
            if r["completeness"] >= 1
            else "0.8–0.99"
            if r["completeness"] >= 0.8
            else "0.6–0.79"
            if r["completeness"] >= 0.6
            else "<0.6"
            for r in rows
        )
        lines.append(
            "完整度分布：" + "、".join(f"{k} {v}" for k, v in sorted(bands.items(), reverse=True))
        )
        reasons = Counter(_reason(note) for r in rows for note in r["needs_review"])
        if reasons:
            lines.append("待复核原因：" + "、".join(f"{k} {v}" for k, v in reasons.most_common()))
    if selected:
        lines += [
            "",
            f"## 第一轮复核 {len(selected)} 条（selected/）",
            "",
            "| routeId | 部门 | 天数 | 完整度 | 线路 |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {r['route_id']} | {r['department']} | {r['days']} | {r['completeness']} | {r['name']} |"
            for r in selected
        ]
    lines += [
        "",
        "## 全部（按完整度）",
        "",
        "| routeId | 部门 | 天数 | 完整度 | 待复核 | 线路 |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['route_id']} | {r['department']} | {r['days']} | {r['completeness']} | {'；'.join(r['needs_review']) or '—'} | {r['name']} |"
        for r in rows
    ]
    if failures:
        lines += ["", "## 未解析", "", "| routeId | 线路 | 原因 |", "|---|---|---|"]
        lines += [f"| {f['route_id']} | {f['name']} | {f['reason']} |" for f in failures]
    text = "\n".join(lines) + "\n"
    (out / "REPORT.md").write_text(text, encoding="utf-8")
    return text


async def run(args: argparse.Namespace) -> int:
    load_env(EXAMPLE_DIR / ".env")
    base = os.environ.get("TOUR_ERP_BASE_URL", "").strip()
    mobile = os.environ.get("TOUR_ERP_MOBILE", "").strip()
    password = os.environ.get("TOUR_ERP_PASSWORD", "").strip()
    if not (base and mobile and password):
        print(
            "TOUR_ERP_BASE_URL, TOUR_ERP_MOBILE and TOUR_ERP_PASSWORD are needed", file=sys.stderr
        )
        return 2
    out = _out_dir(args.out)
    out.mkdir(parents=True, exist_ok=True)
    private = load_private_line_rules()
    erp = HttpErpClient.from_login(base, mobile, password)
    try:
        catalog = await erp.search_routes(RouteQuery())
    finally:
        await erp.aclose()
    wanted = [
        r
        for r in catalog
        if (r.attachment_url or "").lower().split("?")[0].endswith(".docx")
        and (args.include_private or not private.is_private(r))
    ]
    wanted.sort(key=lambda r: r.route_id)
    if args.limit:
        wanted = wanted[: args.limit]
    print(f"catalog {len(catalog)} lines; {len(wanted)} public .docx to parse -> {out}")
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    results = await asyncio.gather(*(_one(sem, r, out) for r in wanted))
    rows: list[dict] = []
    failures: list[dict] = []
    for record, doc, reason in results:
        if doc is None:
            failures.append(
                {"route_id": record.route_id, "name": record.route_name.strip(), "reason": reason}
            )
            continue
        rows.append(
            {
                "route_id": record.route_id,
                "name": doc.name,
                "department": doc.department,
                "days": doc.summary.days,
                "parsed_days": len(doc.days),
                "completeness": doc.quality.completeness,
                "needs_review": doc.quality.needs_review,
                "bytes": doc.source.bytes,
                "file": f"{record.route_id}.json",
            }
        )
    rows.sort(key=lambda r: (-r["completeness"], len(r["needs_review"]), r["route_id"]))
    (out / "index.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    selected: list[dict] = []
    if args.select:
        chosen = out / "selected"
        if chosen.exists():
            shutil.rmtree(chosen)
        chosen.mkdir()
        selected = _spread(rows, args.select)
        for row in selected:
            shutil.copy(out / row["file"], chosen / row["file"])
        (chosen / "selected.json").write_text(
            json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    report = _report(rows, failures, out, selected)
    print(report.split("\n## ")[0])
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.schema:
        write_schema()
        print(f"wrote {SCHEMA_FILE}")
        return 0
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
