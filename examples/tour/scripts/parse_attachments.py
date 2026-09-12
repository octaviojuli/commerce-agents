# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Read the catalog's .docx and .pdf 行程附件 into ``RouteDoc`` files, and rank them for review.

    python examples/tour/scripts/parse_attachments.py            # every public .docx and .pdf line
    python examples/tour/scripts/parse_attachments.py --select 30 --limit 200
    python examples/tour/scripts/parse_attachments.py --select 30 --selling
    python examples/tour/scripts/parse_attachments.py --schema   # data/route-schema.json only

Reads the ERP with the deployment's service account in ``examples/tour/.env`` (read-only:
``route/list``, the 团期 window and the attachment store), writes one ``{routeId}.json`` per
line under ``$TOUR_STATE_DIR/route-docs/`` (``data/.state/route-docs/`` unset), an
``index.json`` and a ``REPORT.md`` ranking the lines by completeness, and with ``--select N``
copies N of them into ``route-docs/selected/`` for the agency's product staff to review.
``--select`` alone takes the most complete documents; ``--selling`` takes the lines that have a
团期 in the next 180 days and no review behind them yet, which is what a round is worth
spending on. Nothing here is committed: the documents are the agency's own product data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from tour.api.erp_client import ErpClient, RouteQuery, RouteRecord, WindowReader  # noqa: E402
from tour.api.http_erp import HttpErpClient  # noqa: E402
from tour.api.itinerary_source import fetch_attachment  # noqa: E402
from tour.api.pdf_source import ImageOnlyPdf  # noqa: E402
from tour.api.private_lines import load_private_line_rules  # noqa: E402
from tour.api.route_doc import RouteDoc, json_schema  # noqa: E402
from tour.api.route_docs import SELECTED, RouteDocStore, is_reviewed  # noqa: E402
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
        "--select", type=int, default=0, help="copy N documents into selected/ for review"
    )
    parser.add_argument(
        "--selling",
        action="store_true",
        help="select the lines with a 团期 in the next 180 days, not the most complete",
    )
    parser.add_argument("--include-private", action="store_true", help="parse 包团/会销 lines too")
    args = parser.parse_args(argv)
    if args.selling and not args.select:
        parser.error("--selling selects a round: pass --select N with it")
    return args


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
        return record, None, "附件未取到（不是 .docx/.pdf、超过大小上限或存储未响应）"
    data, etag = fetched
    try:
        doc = parse_route(record, data, etag=etag)
    except ImageOnlyPdf:
        return record, None, "图片型 PDF（没有文字层），本轮跳过"
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
# What a 在售 round asks of a document instead: the line sells, so the round is worth a
# reviewer's fields being filled in as well as checked, and a .pdf's lower score is no reason
# to leave the line out of the catalog.
SELLING_FLOOR = 0.6
# How far ahead a 团期 counts as selling. A 线路 with none inside it is not what an advisor is
# asked for this quarter, whatever its document scores.
SELLING_DAYS = 180
COMPLETE_CRITERION = f"completeness-{SELECT_FLOOR}"
SELLING_CRITERION = f"selling-{SELLING_DAYS}d"


def _spread(rows: list[dict], count: int, floor: float) -> list[dict]:
    """The ``count`` best documents at or above ``floor`` taken one department at a
    time, so a review round sees every department's layout and not thirty lines of
    one; the ranking within a department is the overall one, and only when the strong
    documents run out are weaker ones taken, the same way."""
    chosen: list[dict] = []
    for tier in (
        [r for r in rows if r["completeness"] >= floor],
        [r for r in rows if r["completeness"] < floor],
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


def _window(today: date | None = None) -> tuple[date, date]:
    """The 团期 window a 在售 round is read over: today and the ``SELLING_DAYS`` after it."""
    start = today or date.today()
    return start, start + timedelta(days=SELLING_DAYS)


async def _selling_ids(erp: ErpClient, records: Sequence[RouteRecord], window: tuple) -> set[int]:
    """The 线路 with a 团期 inside the window — what the agency is actually selling, which is
    not what it has reviewed. One read where the client reads a window whole
    (``erp_client.WindowReader``), else the ERP's own per-线路 period list, which is what the
    fixtures and a client without the whole-window call cost."""
    start, end = window
    if isinstance(erp, WindowReader):
        rows = await erp.list_window(start, end)
    else:
        rows = [
            row
            for record in records
            for row in await erp.list_departures(record.route_id, record.route_name, start, end)
        ]
    return {row.route_id for row in rows if start <= row.depart_date <= end}


def _reviewed_ids(out: Path) -> set[int]:
    """The 线路 a review round has already answered for: the published documents, and the lines
    reading one of them because their 行程 is the same word for word (``api/route_docs.py``'s
    twins). Neither is worth a second round."""
    store = RouteDocStore.load(out)
    return {doc.route_id for doc in store.docs() if is_reviewed(doc)}


def _selling(rows: list[dict], count: int, selling: set[int], reviewed: set[int]) -> list[dict]:
    """The round the 团期 choose: the lines selling inside the window, none of them reviewed
    already, none below ``SELLING_FLOOR``, spread over the departments like any other round."""
    wanted = [
        row
        for row in rows
        if row["route_id"] in selling
        and row["route_id"] not in reviewed
        and row["completeness"] >= SELLING_FLOOR
    ]
    return _spread(wanted, count, SELLING_FLOOR)


def _write_selection(out: Path, chosen: list[dict], criterion: str, about: dict) -> None:
    """The chosen documents copied into ``selected/``, with the criterion that chose them:
    a round is read months later and the file has to say what it is a round of."""
    folder = out / SELECTED
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    for row in chosen:
        shutil.copy(out / row["file"], folder / row["file"])
    (folder / "selected.json").write_text(
        json.dumps(
            {"criterion": criterion, **about, "count": len(chosen), "lines": chosen},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


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


def _report(
    rows: list[dict], failures: list[dict], out: Path, selected: list[dict], criterion: str = ""
) -> str:
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
            f"## 本轮复核 {len(selected)} 条（selected/，{criterion or COMPLETE_CRITERION}）",
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
    window = _window()
    selling: set[int] = set()
    erp = HttpErpClient.from_login(base, mobile, password)
    try:
        catalog = await erp.search_routes(RouteQuery())
        wanted = [
            r
            for r in catalog
            if (r.attachment_url or "").lower().split("?")[0].endswith((".docx", ".pdf"))
            and (args.include_private or not private.is_private(r))
        ]
        wanted.sort(key=lambda r: r.route_id)
        if args.limit:
            wanted = wanted[: args.limit]
        if args.selling:
            selling = await _selling_ids(erp, wanted, window)
    finally:
        await erp.aclose()
    print(f"catalog {len(catalog)} lines; {len(wanted)} public .docx/.pdf to parse -> {out}")
    if args.selling:
        print(
            f"团期 {window[0]}–{window[1]}: {len(selling)} lines selling, "
            f"{len(selling & {r.route_id for r in wanted})} of them to parse"
        )
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
    criterion = ""
    if args.select and args.selling:
        # The published documents and their twins are read before selected/ is rewritten,
        # because the round is what is not answered for yet.
        reviewed = _reviewed_ids(out)
        criterion = SELLING_CRITERION
        selected = _selling(rows, args.select, selling, reviewed)
        _write_selection(
            out,
            selected,
            criterion,
            {
                "window": [window[0].isoformat(), window[1].isoformat()],
                "completeness_floor": SELLING_FLOOR,
                "selling_lines": len(selling),
                "already_reviewed": len(reviewed),
            },
        )
    elif args.select:
        criterion = COMPLETE_CRITERION
        selected = _spread(rows, args.select, SELECT_FLOOR)
        _write_selection(out, selected, criterion, {"completeness_floor": SELECT_FLOOR})
    report = _report(rows, failures, out, selected, criterion)
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
