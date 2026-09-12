# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``scripts/parse_attachments.py``'s selection: which documents a review round is spent on.

``--select N`` alone takes the most complete documents, department by department.
``--select N --selling`` takes the lines with a 团期 in the next 180 days instead — read a
window at a time where the client offers it, a call per 线路 where it does not — leaves out the
lines a round has already answered for, and writes the criterion into ``selected/selected.json``
so the round says what it is a round of. The ERP here is the fixture client: the suite never
reads an agency's own."""

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from tour.api.erp_client import DepartureRecord, RouteQuery
from tour.api.mock_erp import MockErpClient
from tour.api.tests.test_route_docs import _doc, _write

from .conftest import PINNED_TODAY

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "parse_attachments.py"
ROUTE = 1021


def _script():
    """The script as a module. It is a script and not a package module, so it is loaded by
    path; importing it runs nothing but the imports."""
    spec = importlib.util.spec_from_file_location("tour_parse_attachments", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _script()


class WindowErp(MockErpClient):
    """A fixture client that reads a whole window in one call, as the agency's own does."""

    calls: list[str] = []

    async def list_window(
        self, depart_from: date | None, depart_to: date | None
    ) -> list[DepartureRecord]:
        self.calls.append("list_window")
        return [
            row
            for route_id in (1021, 1022)
            for row in await self.list_departures(route_id, "", depart_from, depart_to)
        ]

    async def list_departures(self, route_id, route_name, depart_from, depart_to):
        self.calls.append("list_departures")
        return await super().list_departures(route_id, route_name, depart_from, depart_to)


def _row(route_id: int, department: str, completeness: float) -> dict:
    return {
        "route_id": route_id,
        "name": f"线路 {route_id}",
        "department": department,
        "days": 8,
        "parsed_days": 8,
        "completeness": completeness,
        "needs_review": [],
        "bytes": 1024,
        "file": f"{route_id}.json",
    }


async def test_the_window_is_read_whole_where_the_client_offers_it(script):
    erp = WindowErp(today=PINNED_TODAY)
    erp.calls = []
    window = script._window(PINNED_TODAY)
    assert window == (PINNED_TODAY, PINNED_TODAY + timedelta(days=script.SELLING_DAYS))
    records = await erp.search_routes(RouteQuery())
    assert await script._selling_ids(erp, records, window) == {1021, 1022}
    assert erp.calls.count("list_window") == 1


async def test_a_client_with_no_window_call_is_asked_line_by_line(script):
    """The fixtures, and any client without ``list_window``: a call per 线路, which is what the
    ERP's own period list costs when it filters on the route name."""
    erp = MockErpClient(today=PINNED_TODAY)
    records = await erp.search_routes(RouteQuery())
    selling = await script._selling_ids(erp, records, script._window(PINNED_TODAY))
    assert selling == {r.route_id for r in records}
    week = (PINNED_TODAY, PINNED_TODAY + timedelta(days=7))
    assert await script._selling_ids(erp, records, week) < selling


async def test_a_line_with_no_团期_in_the_window_is_not_in_the_round(script):
    rows = [_row(ROUTE, "新疆部", 1.0), _row(1022, "新疆部", 1.0)]
    assert [r["route_id"] for r in script._selling(rows, 5, {ROUTE}, set())] == [ROUTE]


async def test_a_selling_line_below_the_floor_is_not_in_the_round(script):
    """0.6 and not the 0.9 a completeness round asks: the line sells, so a reviewer filling a
    field in is worth as much as one checking it, and a .pdf scores lower than a .docx."""
    rows = [_row(ROUTE, "新疆部", 0.6), _row(1022, "新疆部", 0.59)]
    assert [r["route_id"] for r in script._selling(rows, 5, {ROUTE, 1022}, set())] == [ROUTE]


async def test_the_round_is_spread_over_the_departments_best_document_first(script):
    rows = [
        _row(1, "欧洲部", 1.0),
        _row(2, "欧洲部", 0.9),
        _row(3, "斯里兰卡", 0.8),
        _row(4, "斯里兰卡", 0.7),
    ]
    selling = {1, 2, 3, 4}
    assert [r["route_id"] for r in script._selling(rows, 3, selling, set())] == [1, 3, 2]


def test_the_lines_a_round_has_answered_for_already_are_left_out(script, tmp_path):
    """The published documents and the lines reading one of them as their twin: neither is
    worth a second round, and the twin is not even a second product."""
    _write(tmp_path, "", _doc())
    _write(tmp_path, "published", _doc(reviewed=True))
    _write(tmp_path, "", _doc(route_id=1022))
    _write(tmp_path, "", _doc(route_id=1023, text="游览【天池】。"))
    reviewed = script._reviewed_ids(tmp_path)
    assert reviewed == {ROUTE, 1022}
    rows = [_row(ROUTE, "新疆部", 1.0), _row(1022, "新疆部", 1.0), _row(1023, "新疆部", 1.0)]
    chosen = script._selling(rows, 5, {ROUTE, 1022, 1023}, reviewed)
    assert [r["route_id"] for r in chosen] == [1023]


def test_the_round_says_which_criterion_chose_it(script, tmp_path):
    for route_id in (ROUTE, 1022):
        (tmp_path / f"{route_id}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "selected").mkdir()
    (tmp_path / "selected" / "9999.json").write_text("{}", encoding="utf-8")
    window = script._window(PINNED_TODAY)
    chosen = [_row(ROUTE, "新疆部", 0.8)]
    script._write_selection(
        tmp_path,
        chosen,
        script.SELLING_CRITERION,
        {"window": [window[0].isoformat(), window[1].isoformat()]},
    )
    written = json.loads((tmp_path / "selected" / "selected.json").read_text(encoding="utf-8"))
    assert written["criterion"] == "selling-180d" and written["count"] == 1
    assert written["window"] == [str(PINNED_TODAY), str(window[1])]
    assert [line["route_id"] for line in written["lines"]] == [ROUTE]
    # The round is the directory's whole content: what an earlier round left is gone.
    assert sorted(p.name for p in (tmp_path / "selected").glob("*.json")) == [
        f"{ROUTE}.json",
        "selected.json",
    ]


def test_selling_selects_a_round_and_says_so_without_one(script):
    assert script.parse_args(["--select", "20", "--selling"]).selling
    assert not script.parse_args(["--select", "20"]).selling
    with pytest.raises(SystemExit):
        script.parse_args(["--selling"])
