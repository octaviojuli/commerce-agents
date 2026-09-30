"""Recognise a day label ("第三天", "DAY-3", "D3", "第23-26天") at the start of a line.

One definition, shared by the reader (to notice a page whose table was read out of order) and
the segmenter (to find the day blocks).
"""

from __future__ import annotations

import re

CN = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "两": 2,
}


def cn_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if not text or any(ch not in CN and ch != "十" for ch in text):
        return None
    if text == "十":
        return 10
    if "十" in text:
        tens, _, ones = text.partition("十")
        return (CN.get(tens, 1) if tens else 1) * 10 + (CN.get(ones, 0) if ones else 0)
    return CN.get(text)


NUM = r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})"
HEADER = re.compile(
    r"^\s*(?:"
    rf"第\s*{NUM}\s*(?:天\s*)?(?:[-~至到–—]\s*(?:第\s*)?{NUM}\s*)?天"
    rf"|(?:DAY|Day|day|D)\s*[-_ ]?\s*{NUM}(?!\d)(?:\s*[-~至–—]\s*{NUM})?"
    r")"
    r"(?P<rest>.*)$"
)
DATE_PREFIX = re.compile(r"^\s*\d{1,2}[./月]\d{1,2}日?\s+")


def day_label(text: str):
    text = DATE_PREFIX.sub("", text)
    match = HEADER.match(text)
    if not match:
        return None
    numbers = [cn_number(g) for g in match.groups()[:-1] if g]
    numbers = [n for n in numbers if n]
    if not numbers or numbers[0] > 60:
        return None
    start = numbers[0]
    end = numbers[1] if len(numbers) > 1 and numbers[1] > start else None
    return start, end
