"""Operator-maintained suggested travel windows; never statutory holiday assertions."""

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path


def window(message, today):
    path = Path(os.environ.get("WAREHOUSE_ADVISOR_HOLIDAYS", Path(__file__).with_suffix(".json")))
    table = json.loads(path.read_text())
    explicit = re.search(r"(20\d{2})年", message)
    for name in (*table["recurring"], *table["dated"]):
        if name not in message:
            continue
        entries = table["dated"].get(name, [])
        if name in table["recurring"]:
            rule = table["recurring"][name]
            entries = [
                {**rule, "start": f"{y}-{rule['start']}", "end": f"{y}-{rule['end']}"}
                for y in range(today.year, today.year + 3)
            ]
        for entry in entries:
            start, end = date.fromisoformat(entry["start"]), date.fromisoformat(entry["end"])
            if explicit and start.year != int(explicit[1]) or not explicit and end < today:
                continue
            if "前后" in message:
                start -= timedelta(days=entry["around_before"])
            return {
                "value": {"start": str(start), "end": str(end)},
                "source": "inferred",
                "evidence": name + ("前后" if "前后" in message else ""),
                "hint": f"按运营表建议 {start} 至 {end} 出发；并非放假安排，待确认",
            }
    return None
