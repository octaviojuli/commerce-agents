"""Bounded lexical fact associations; evidence for review, not a truth oracle."""

import re

from .route_doc import Day

ALIASES = {
    "不得少于": "不少于",
    "不低于": "不少于",
    "至少": "不少于",
    "不得超过": "不超过",
    "至多": "不超过",
    "最多": "不超过",
    "大约": "约",
    "大概": "约",
    "外部参观": "外观",
    "外部游览": "外观",
    "进入参观": "入内",
    "入内参观": "入内",
    "途经": "途径",
    "不包含": "不含",
    "包含": "含",
}
ALIAS_RE = re.compile("|".join(sorted(ALIASES, key=len, reverse=True)))
NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:[.:：/]\d+)*(?:\+\d+)?")
ENTITY_PREFIX = re.compile(
    r"(?:^|[。；;\n])\s*([^。；;，,：:\n]{1,60}?)\s*(?:安排)?"
    r"(?:外观|入内|途径|游览|参观|停留|含门票|不含门票)"
)
ACTION_PREFIX = re.compile(
    r"^(?:早餐后|午餐后|晚餐后|随后|之后|抵达|前往|上午|下午|晚上|游览|参观|外观|入内|途径|乘车前往)\s*"
)
FACTS = (
    (
        "费用",
        re.compile(
            r"(?:不含|含)(?:景点)?(?:门票|船票|讲解(?:费)?|早餐|午餐|晚餐|机票|签证(?:费)?|小费)"
        ),
    ),
    ("游览方式", re.compile(r"外观|入内|途径")),
    ("约定", re.compile(r"不少于|不超过|约(?=\s*\d)|参考|或同级|自费|自理|自愿|赠送|另付|免费")),
    (
        "条件",
        re.compile(
            r"如遇|若遇|闭馆|取消|替代|以[^，,。；;\n]{0,30}为准|视[^，,。；;\n]{0,30}(?:而定|安排)"
        ),
    ),
    (
        "数值",
        re.compile(
            r"(?:不少于|不超过|约)?\s*(?<![A-Za-z0-9])\d+(?:[.:：/]\d+)*(?:\+\d+)?\s*(?:公里|千米|[Kk][Mm]|小时|分钟|分鐘|晚|天|元|欧元|美元|美金|人|岁|%|％)?"
        ),
    ),
    ("航班", re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2,4}(?![A-Za-z0-9])")),
)


def normalize(value: str) -> str:
    return ALIAS_RE.sub(lambda m: ALIASES[m[0]], value)


def entities(day: Day) -> list[str]:
    """Only names present in source fields/text; no gazetteer or model guesses."""
    names = list(day.places) + [s.name for s in day.sights]
    if day.hotel:
        names.append(day.hotel.name)
    for block in day.blocks:
        body = normalize("\n".join(block.paragraphs))
        names.extend(re.findall(r"【([^【】\n]{1,60})】", body))
        names.extend(m[1] for m in ENTITY_PREFIX.finditer(body))
    result = set()
    for name in names:
        name = normalize(name.strip().strip("【】"))
        name = ACTION_PREFIX.sub("", name).strip()
        name = re.split(r"(?:不含|含)(?:门票|船票)|市区观光|[（(]", name)[0].strip()
        name = re.sub(r"\s+", "", name)
        if (
            (2 <= len(name) <= 60 or bool(re.fullmatch(r"[A-Z]", name)))
            and not NUMBER.search(name)
            and not re.search(r"[，,。；;：:]", name)
        ):
            result.add(name)
    return sorted(result, key=lambda value: (-len(value), value))[:300]


def claims(body: str, names: list[str]) -> list[dict]:
    """Bind recognized facts to explicit subjects, tolerating changed punctuation.

    Unnamed facts retain order per category. Ambiguous prose still needs the
    separate semantic review and human approval; this never certifies coverage.
    """
    pattern = (
        re.compile("|".join(r"\s*".join(re.escape(c) for c in n) for n in names)) if names else None
    )
    result = []
    text = normalize(body)
    mentions = list(pattern.finditer(text)) if pattern else []
    for kind, rule in FACTS:
        for match in rule.finditer(text):
            # A digit that belongs to a recognized name is not a quantity.
            if any(m.start() <= match.start() < m.end() for m in mentions):
                continue
            preceding = [m for m in mentions if m.end() <= match.start()]
            following = [m for m in mentions if m.start() >= match.end()]
            subject = re.sub(r"\s+", "", preceding[-1][0]) if preceding else ""
            # Prefix verbs can identify the immediately following subject;
            # an unrelated later city cannot own earlier flight/date facts.
            if (
                kind in {"游览方式", "条件"}
                and following
                and following[0].start() - match.end() <= 3
            ):
                subject = re.sub(r"\s+", "", following[0][0])
            result.append(
                {
                    "subject": subject,
                    "kind": kind,
                    "value": re.sub(r"\s+", "", match[0]),
                    "excerpt": body.strip()[:1000],
                    "offset": match.start(),
                }
            )
            if len(result) > 400:
                raise ValueError("EDITOR_FACT_BOUND")
    return result


def evidence(day: Day) -> list[dict]:
    names = entities(day)
    result = []
    for n, block in enumerate(day.blocks):
        result.extend(
            item | {"source_id": n + 1, "source_block_id": block.block_id}
            for item in claims("\n".join(block.paragraphs), names)
        )
        if len(result) > 400:
            raise ValueError("EDITOR_FACT_BOUND")
    return result


def compare(day: Day, output) -> list[dict]:
    names = entities(day)
    problems = []
    for index, block in enumerate(output.blocks):
        before = [
            item | {"source_id": i, "source_block_id": day.blocks[i - 1].block_id}
            for i in block.source_ids
            for item in claims("\n".join(day.blocks[i - 1].paragraphs), names)
        ]
        # Headings may repeat a fact; the factual body must stand on its own.
        after = claims("\n".join(block.paragraphs), names)
        keys = sorted({(x["subject"], x["kind"]) for x in before + after})
        for subject, kind in keys:
            old = [x for x in before if (x["subject"], x["kind"]) == (subject, kind)]
            new = [x for x in after if (x["subject"], x["kind"]) == (subject, kind)]
            old_values, new_values = [x["value"] for x in old], [x["value"] for x in new]
            same = old_values == new_values
            if same:
                continue
            problems.append(
                {
                    "path": f"days/{day.day - 1}/blocks/{index}",
                    "day": day.day,
                    "subject": subject or "原文未明确命名的对象",
                    "kind": kind,
                    "expected": old_values,
                    "observed": new_values,
                    "source_ids": block.source_ids,
                    "source_block_ids": [day.blocks[i - 1].block_id for i in block.source_ids],
                    "source_text": "\n".join(dict.fromkeys(x["excerpt"] for x in old)),
                    "proposed_text": "\n".join(block.paragraphs),
                }
            )
    return problems


class FactMismatch(ValueError):
    def __init__(self, issues):
        super().__init__("EDITOR_FACT_ASSOCIATION")
        self.issues = issues
