# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Read a period of the workbench's conversations and print what they came to: what the
advisors searched for, which 定制方案 they built and what those ask the 计调 to confirm, where
the system fell short, and which data-file changes look worth reviewing. It opens the state
directory read-only and changes nothing — a word this report proposes for
``data/tag-rules.json`` is written by hand, after someone has read it.

    python examples/tour/scripts/review_sessions.py --state-dir examples/tour/data/.state
    python examples/tour/scripts/review_sessions.py --state-dir /srv/tour-state \
        --since 2026-09-01 --out review.md

The sections and the parsing are ``api/review.py``; this is the command around them.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tour.api.review import (  # noqa: E402
    MEMORY_FILE,
    SESSIONS_FILE,
    read_sessions,
    render,
    review_sessions,
)
from tour.api.store import SqliteSessionStore  # noqa: E402


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ACME 旅行社 会话复盘报告（只读，不改任何数据）",
    )
    parser.add_argument(
        "--state-dir",
        required=True,
        type=Path,
        help=f"TOUR_STATE_DIR：{SESSIONS_FILE} 与 {MEMORY_FILE} 所在的目录",
    )
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=None,
        help="只看这一天（含）之后更新过的会话，YYYY-MM-DD",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="报告写到这个文件，默认打到标准输出",
    )
    args = parser.parse_args(argv)
    if not (args.state_dir / SESSIONS_FILE).is_file():
        parser.error(f"{args.state_dir / SESSIONS_FILE} 不存在")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = SqliteSessionStore(args.state_dir / SESSIONS_FILE)
    review = review_sessions(
        read_sessions(store, since=args.since),
        memory_path=args.state_dir / MEMORY_FILE,
        since=args.since,
        plan_store=store,
    )
    report = render(review)
    if args.out is None:
        print(report)
    else:
        args.out.write_text(report, encoding="utf-8")
        print(f"报告已写入 {args.out}（{len(review.sessions)} 个会话）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
