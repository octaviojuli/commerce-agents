"""Model extraction with code-side verification, and assembly into ``RouteContent``.

The model proposes fields; code decides what survives:
- a named node must appear in the text it cites (otherwise it is re-cited from the day
  text when found there, or dropped); copied text (terms, notes, includes, stay names,
  alternatives) must be mostly present in its cited units;
- every number in a displayed field must stand as a whole number in its source text
  (otherwise the field is cleared, or the item dropped);
- visit mode, ticket, inclusion, highlight and the paid or shopping node types need their
  trigger words near the node's name, with negated phrases removed (otherwise they fall
  back to ``unknown`` or a plain node);
- model-worded fields (day title and summary, node description, transport names) keep
  only numbers the source states; the review view labels them as worded by the model;
- every evidence unit is checked for coverage; uncited units that mention money, shopping,
  conditions or exclusions are attached as-is to the day notes or notices, marked ``auto``.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from . import prompts
from .confidence import assess
from .llm import Model, ModelError
from .models import (
    ChildNode,
    Day,
    DayOut,
    Item,
    Node,
    Note,
    NoticeGroup,
    Quality,
    RouteContent,
    RouteOut,
    Source,
)
from .reader import Document
from .segment import DayBlock, Layout

PARSER = "route-kit-1"
# Blank form fields and table headers: not itinerary content, never "risky".
FORM_WORDS = re.compile(
    r"签名|签字|按手印|盖章|填写|身份证号|联系电话[:：]?\s*$|日期[:：]\s*$|^名称\s*\|\s*价格|合计\(小写\)"
)
SHORT_ROW = re.compile(r"^[^|]{0,8}(\|\s*[^|]{0,12}){2,}$")
MONEY = re.compile(r"\d|欧|元|美金|美元|镑|€|\$|￥")


# A quoted "no shopping" carries a zero or a negation; a line that only names shops does not.
NEGATION = re.compile(r"[无零0不没]")


def is_form(text: str) -> bool:
    """A short table row is a header only when it names no amount ("小费 | 5欧" is content)."""
    return bool(FORM_WORDS.search(text)) or (bool(SHORT_ROW.match(text)) and not MONEY.search(text))


def is_risky(text: str) -> bool:
    return bool(RISK.search(text)) and not is_form(text)


RISK = re.compile(
    r"购物|自费|另付|不含|自理|小费|如遇|取消|不退|成团|闭馆|费用|价格|元/人|欧/人|美金|美元|欧元|押金|须|必须|禁止"
)
NOTICE_KEYS = (
    ("health_age", r"高龄|年龄|健康|医疗|药|孕|疾病|身体"),
    ("booking_cancellation", r"取消|退团|退款|损失|违约|定团|报名"),
    ("money_tips", r"现金|银联|小费|刷卡|货币|兑换"),
    ("shopping_optional", r"购物|自费"),
    ("safety", r"安全|扒手|贵重|保管"),
    ("visa_documents", r"签证|护照|证件|销签"),
)
# Trigger words are searched near the node's name (see ``DayChecker.scope``). Positive
# values are searched after negated phrases are removed, so "不含门票" never supports
# ticket=included and "不入内" never supports inside.
TRIGGERS = {
    (
        "visit_mode",
        "inside",
    ): r"入内|进入|内部|入园|入场|进馆|含.{0,6}门票|首道门票|门票[含(（]|上塔|登顶|登塔|[寺馆宫院堂园殿]内|参观.{0,12}内",
    ("visit_mode", "outside"): r"外观|不入内|不登顶",
    ("visit_mode", "passing"): r"途经|经过|路过",
    ("visit_mode", "drive_by"): r"车游|车览|乘车游览|巴士游览",
    ("visit_mode", "distant_view"): r"远眺|远观|眺望|观景",
    ("visit_mode", "walk"): r"步行|漫步|徒步",
    ("ticket", "included"): r"含.{0,8}门?票|门票已含|首道门票|门票[含(（]|门票\s*含",
    ("ticket", "excluded"): r"门票自理|不含.{0,6}门票|门票.{0,4}自理|自费",
    ("ticket", "free_entry"): r"免费(?:参观|入场|入内|进入|游览|开放)|免门票|免票|无需门票",
    ("inclusion", "gift"): r"赠送",
    ("inclusion", "optional_paid"): r"自费|另付|费用自理|自愿|收费",
    ("inclusion", "recommended_not_included"): r"推荐|建议|可自行|自行前往|自由选择|可选",
    ("inclusion", "package_item"): r"套餐|打包|组合|含|包括",
}
NEGATIVE = {("visit_mode", "outside"), ("ticket", "excluded"), ("inclusion", "optional_paid")}
NEGATED = re.compile(
    r"(?:不|无|未|非)(?:含|包含|包括|入内|进入|登顶|登塔|上塔)[^，,。；;|\n]{0,10}"
)
HIGHLIGHT = r"特别|独家|升级|特色|特意|❀|★|☆|✿|亮点|精选|甄选|尊享|必[游看玩]|网红|赠送"
# A node type that changes what the customer panel counts needs its words in the source.
TYPE_TRIGGERS = {
    "optional": r"自费|另付|自愿|自理|费用|价格|/人|\d+\s*(?:欧|元|美金|美元|镑|€)|[€$]\s*\d",
    "shopping": r"购物|奥特莱斯|[Oo]utlet|OUTLET|百货|商场|市场|集市|店|免税|购买",
}
TYPE_FALLBACK = {"optional": "activity", "shopping": "poi"}
PAID_NEAR = re.compile(r"自费|另付|费用自理|需另付|自愿参加")
NAMED = {"poi", "activity", "shopping", "optional", "recommend", "package", "photo_spot"}
# A short section title outside the days ("预定须知", "三、自费项目：") labels, it says nothing.
HEADING = re.compile(r"^.{0,10}(?:须知|说明|事项|项目|提示|[:：])$")
# A heading such as 【赠送某市区游】 or "自费推荐行程：" labels every node below it.
SECTION = re.compile(r"赠送|自费|推荐|自选|可选|套餐|自愿")
PART_BREAK = re.compile(r"^(?:上午|中午|下午|傍晚|晚上|午餐后|晚餐后)")
# Lines that are day fields, not notes: the day's transport, stay and meal lines.
FIELD_LINE = re.compile(r"^(?:行|交通|住|住宿|宿|餐|用餐|早餐|午餐|晚餐|早|中|晚)\s*[:：]")


def field_line(text: str) -> bool:
    """A short structured line ("住宿：某地或周边", "住宿：无；交通：无"), not a sentence."""
    parts = [x.strip() for x in re.split(r"[；;]", text) if x.strip()]
    return bool(parts) and all(
        FIELD_LINE.match(x) and len(x) <= 18 and not re.search(r"[，。,!！?？]", x) for x in parts
    )


TRAVEL_LINE = re.compile(r"^(?:行|交通)\s*[:：]\s*")
# Section labels that carry no content on their own ("独家安排---", "自费推荐行程：").
LABEL_ONLY = re.compile(
    r"^(?:今日|今天)?(?:独家|特别|特色)?(?:安排|赠送|推荐|提示|提醒|备注|说明)?"
    r"(?:自费推荐行程|自费推荐|自费项目|推荐自费|独家安排|特别安排|特别赠送|赠送项目|温馨提示|备注|注意|注)?$"
)


def label_only(text: str) -> bool:
    core = re.sub(r"[\s\-—–~_:：❀★☆✿·•|/、,，。.!！]", "", text or "")
    return not core or (len(core) <= 8 and bool(LABEL_ONLY.fullmatch(core)))


TRAILING_LABEL = re.compile(
    r"(?:[，,、❀★]\s*|\s+)(?:今日)?(?:特别安排|独家安排|特别赠送)[❀★☆✿\-—:：\s]*$"
)
SECTION_TITLE = re.compile(
    r"^(?:贴心赠送|品质承诺|\S{0,2}大品质承诺|精心安排|特别安排|行程特色|产品特色)$"
)
LABEL_WORDS = re.compile(r"提示|注意|备注|说明|须知|推荐|自费|购物|温馨")


class LastDayEnd(BaseModel):
    last_unit: int


class DayStart(BaseModel):
    day: int
    first_unit: int


class DayStarts(BaseModel):
    starts: list[DayStart]


def refine_pdf_days(layout: Layout, model: Model, issues: list[dict]) -> None:
    """PDF day labels can sit mid-block; let the model place each day's first unit."""
    blocks = layout.days
    if len(blocks) < 2:
        return
    lo = max(1, blocks[0].header_unit - 30)
    hi = blocks[-1].units[-1]
    region = [u for u in layout.units if lo <= u.id <= hi]
    hints = [{"day": b.day, "label_unit": b.header_unit} for b in blocks]
    try:
        answer = model.ask(
            prompts.DAY_STARTS,
            {"units": [{"id": u.id, "text": u.text[:50]} for u in region], "hints": hints},
            DayStarts,
            max_tokens=2000,
        )
    except ModelError as error:
        issues.append(
            {"code": "PDF_DAY_BOUNDARIES_UNCHECKED", "path": "days", "detail": str(error)[:160]}
        )
        return
    starts = {s.day: s.first_unit for s in answer.starts}
    firsts = [starts.get(b.day) for b in blocks]
    ok = all(f is not None for f in firsts) and all(
        a < b for a, b in zip(firsts, firsts[1:], strict=False)
    )
    ok = ok and all(abs(f - b.header_unit) <= 40 for f, b in zip(firsts, blocks, strict=True))
    # Day 1 may start a little before its label, never deep in the cover text.
    ok = ok and firsts[0] >= blocks[0].header_unit - 8
    if not ok:
        issues.append(
            {"code": "PDF_DAY_BOUNDARIES_REJECTED", "path": "days", "detail": str(firsts)[:160]}
        )
        return
    overview = {u for b in blocks for u in b.overview_units}
    for index, block in enumerate(blocks):
        end = firsts[index + 1] - 1 if index + 1 < len(blocks) else hi
        block.units = [u for u in range(firsts[index], end + 1) if u not in overview]
        if block.header_unit not in block.units:
            block.units.append(block.header_unit)
            block.units.sort()
    inside = {u for b in blocks for u in b.units} | overview
    layout.outside = [u.id for u in layout.units if u.id not in inside]
    if firsts != [b.header_unit for b in blocks]:
        issues.append({"code": "PDF_DAY_BOUNDARIES_ADJUSTED", "path": "days", "detail": ""})


def split_days_by_model(
    layout: Layout, model: Model, expected: int | None, issues: list[dict]
) -> None:
    """No day label the rules can read (a drawn or vertical label, dates, a table): the model
    places each day's first unit. The answer is used only when it is a clean 1..N sequence that
    matches the registered day count; otherwise the layout stays as the rules left it."""
    units = layout.units
    if len(units) < 6:
        return
    try:
        answer = model.ask(
            prompts.DAY_SPLIT,
            {
                "expected_days": expected,
                "units": [{"id": u.id, "text": u.text[:60]} for u in units[:900]],
            },
            DayStarts,
            max_tokens=2000,
        )
    except ModelError as error:
        issues.append({"code": "DAY_SPLIT_UNCHECKED", "path": "days", "detail": str(error)[:160]})
        return
    starts = sorted(answer.starts, key=lambda s: s.day)
    days = [s.day for s in starts]
    firsts = [s.first_unit for s in starts]
    known = {u.id for u in units}
    ok = len(days) >= 2 and days == list(range(1, len(days) + 1))
    ok = (
        ok
        and all(f in known for f in firsts)
        and all(a < b for a, b in zip(firsts, firsts[1:], strict=False))
    )
    ok = ok and (not expected or len(days) == expected)
    if not ok:
        issues.append({"code": "DAY_SPLIT_REJECTED", "path": "days", "detail": str(firsts)[:160]})
        return
    layout.days = [
        DayBlock(
            day,
            None,
            first,
            list(range(first, (firsts[i + 1] if i + 1 < len(days) else units[-1].id + 1))),
        )
        for i, (day, first) in enumerate(zip(days, firsts, strict=True))
    ]
    layout.outside = [u.id for u in units if u.id < firsts[0]]
    layout.method = "model"
    issues[:] = [i for i in issues if i["code"] != "NO_DAY_HEADERS"]
    issues.append({"code": "DAYS_SPLIT_BY_MODEL", "path": "days", "detail": f"{len(days)} 天"})


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(
        r"[\s【】\[\]「」『』“”\"'‘’·•・,，。.、:：;；()（）<>《》!！?？\-—–~_|/]", "", text
    ).lower()


NAME_SUFFIX = re.compile(
    r"\s*[（(](?:外观|入内|入内参观|车游|车览|途经|远眺|含门票[^）)]*|含首道门票[^）)]*|门票自理|自费|赠送)[）)]\s*$"
    r"|\s*(?:外观|含(?:首道)?\S{0,8}票.*)$"
)
NAME_PREFIX = re.compile(r"^(?:特别安排|独家安排|特别赠送|今日特别安排)\s*[-—:：]*\s*")


def clean_name(name: str) -> str:
    previous = None
    name = (name or "").strip().strip("【】")
    while previous != name:
        previous = name
        name = NAME_PREFIX.sub("", NAME_SUFFIX.sub("", name)).strip()
    return name


def digits(text: str) -> str:
    """NFKC text with thousands separators removed: "1,299" and "1299" compare equal."""
    return re.sub(r"(?<=\d)[,，](?=\d{3})", "", unicodedata.normalize("NFKC", text or ""))


def numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", digits(text))


def missing_numbers(value: str, source: str) -> list[str]:
    """Numbers in ``value`` that do not stand as whole numbers in ``source`` (1 is not in 10)."""
    src = digits(source)
    return [n for n in numbers(value) if not re.search(rf"(?<![\d.]){re.escape(n)}(?!\d)", src)]


def bigram_cover(text: str, source: str) -> float:
    a, b = norm(text), norm(source)
    if not a:
        return 1.0
    if a in b:
        return 1.0
    grams = [a[i : i + 2] for i in range(max(1, len(a) - 1))]
    return sum(1 for g in grams if g in b) / len(grams)


class DayChecker:
    def __init__(self, text_by_id: dict[int, str], allowed: list[int]):
        self.text = text_by_id
        self.allowed = set(allowed)
        self.day_text = "\n".join(text_by_id[i] for i in allowed)
        self.issues: list[dict] = []
        self.cited: set[int] = set()
        self.subject = ""

    def issue(self, code, path, detail=""):
        entry = {"code": code, "path": path, "detail": detail[:160]}
        if self.subject:
            # `path` indexes the model's list before filtering; the name is what finds the node.
            entry["subject"] = self.subject[:80]
        self.issues.append(entry)

    def cites(self, cite) -> list[int]:
        kept = [c for c in cite or [] if c in self.allowed]
        return sorted(set(kept))

    def cited_text(self, cite) -> str:
        return "\n".join(self.text[c] for c in cite)

    def find_units(self, name: str) -> list[int]:
        key = norm(name)
        return [i for i in sorted(self.allowed) if key and key in norm(self.text[i])]

    def check_numbers(self, obj, field: str, source: str, path: str):
        value = getattr(obj, field, "") or ""
        if not value:
            return
        missing = missing_numbers(value, source)
        if missing:
            self.issue("NUMBER_NOT_IN_SOURCE", f"{path}.{field}", f"{value}（{','.join(missing)}）")
            setattr(obj, field, "")

    def text_ok(self, value: str, cite: list[int], least: float = 0.7) -> bool:
        """Copied text: most of it appears in its cited units, or in one unit of the day."""
        if not value:
            return True
        if cite and bigram_cover(value, self.cited_text(cite)) >= least:
            return True
        return any(bigram_cover(value, self.text[i]) >= 0.8 for i in self.allowed)

    def scope(self, name: str, source: str, context: str) -> str:
        """The text near a node's name: its own words, not a neighbour's in the same line."""
        flat, ctx = re.sub(r"\s", "", source), re.sub(r"\s", "", context)
        key = re.sub(r"\s", "", name or "")

        def around(text: str) -> str | None:
            if not key:
                return None
            spans = [m.start() for m in re.finditer(re.escape(key), text)]
            if not spans:
                return None
            return "\n".join(text[max(0, i - 20) : i + len(key) + 40] for i in spans)

        own = around(flat)
        return (own if own is not None else flat) + "\n" + (around(ctx) or ctx[-60:])

    def check_attrs(self, obj, scope: str, path: str, name: str = ""):
        positive = NEGATED.sub("", scope)
        key = re.escape(re.sub(r"\s", "", name or ""))
        for attr in ("visit_mode", "ticket", "inclusion"):
            value = getattr(obj, attr, None)
            pattern = TRIGGERS.get((attr, value))
            if not pattern:
                continue
            text = scope if (attr, value) in NEGATIVE else positive
            supported = bool(re.search(pattern, text))
            # "外观" right beside the name outranks an "入内" elsewhere in the line.
            if supported and value == "inside" and key:
                supported = not re.search(rf"{key}[】)）]?[(（]?外观|外观[【(（]?{key}", scope)
            if not supported:
                self.issue("ATTRIBUTE_UNSUPPORTED", f"{path}.{attr}", f"{value}")
                setattr(obj, attr, "unknown")

    def near_unit(self, name: str) -> int | None:
        """A unit that holds the name with a small spelling difference (e.g. 群 added)."""
        key = norm(name)
        if len(key) < 4:
            return None
        best = max(sorted(self.allowed), key=lambda i: bigram_cover(name, self.text[i]))
        return best if bigram_cover(name, self.text[best]) >= 0.85 else None

    def photo_spot_name(self, node: Node) -> None:
        """ "打卡机位3---" and the place 【name】 often sit on separate lines."""
        label = re.fullmatch(r"(?:打卡)?机位\s*\d+\s*[-—]*", node.name.strip())
        if not label:
            return
        node.spot_label = node.spot_label or node.name.strip(" -—")
        ordered = sorted(self.allowed)
        start = ordered.index(min(node.cite)) if node.cite and min(node.cite) in ordered else None
        if start is None:
            return
        for unit in ordered[start : start + 3]:
            for found in re.finditer(r"【([^】]{1,40})】", self.text[unit]):
                if LABEL_WORDS.search(found.group(1)):
                    continue  # 【温馨提示】 is a section label, not a place
                node.name = clean_name(found.group(1))
                node.cite = sorted(set(node.cite) | {unit})
                return

    def before(self, cite: list[int]) -> str:
        """The unit just before a node often carries its label (自费推荐行程：, 赠送：)."""
        first = min(cite) if cite else None
        ordered = sorted(self.allowed)
        if first in ordered and ordered.index(first) > 0:
            return self.text[ordered[ordered.index(first) - 1]]
        return ""

    def section(self, cite: list[int]) -> str:
        """The nearest heading above a node, within its part of the day, that labels it."""
        ordered = sorted(self.allowed)
        first = min(cite) if cite else None
        if first not in ordered:
            return ""
        for unit in reversed(ordered[max(0, ordered.index(first) - 12) : ordered.index(first)]):
            text = self.text[unit].strip()
            # A heading: 【赠送…】 opening a line, or a short line ending in a colon.
            heading = re.match(r"^[|\s]*(?:特别)?【[^】]{0,30}】", text) or (
                len(text) <= 30 and re.search(r"[:：]\s*$", text)
            )
            if heading and SECTION.search(text[:20]):
                return text[:30]
            if PART_BREAK.match(text):
                break
        return ""

    def check_type(self, node: Node, scope: str, path: str):
        pattern = TYPE_TRIGGERS.get(node.type)
        if pattern and not re.search(pattern, scope):
            self.issue("TYPE_UNSUPPORTED", f"{path}.type", f"{node.type} {node.name}")
            node.review.append(f"原文没有支持“{node.type}”类型的词，已改为一般节点")
            node.type = TYPE_FALLBACK[node.type]
        elif (
            node.type in ("poi", "activity")
            and "自费" in self.section(node.cite)
            and not re.search(r"含.{0,6}门票|赠送", NEGATED.sub("", scope.split("\n")[0]))
        ):
            # Listed under a "自费推荐行程：" heading: the customer pays for it.
            self.issue("TYPE_FROM_SECTION", f"{path}.type", node.name)
            node.type = "optional"
            if node.inclusion == "unknown":
                node.inclusion = "optional_paid"
            node.review.append("位于“自费”标题下，已归为自费项目，请核对")
        elif node.type in ("poi", "activity") and PAID_NEAR.search(scope):
            self.issue("TYPE_CHECK", f"{path}.type", node.name)
            node.review.append("名称附近提到自费或另付，请核对是否为自费项目")

    def node(
        self,
        node: ChildNode | Node,
        path: str,
        named: bool,
        context: str = "",
        parent_type: str = "",
    ) -> bool:
        outer, self.subject = self.subject, clean_name(node.name) or node.name
        try:
            return self._node(node, path, named, context, parent_type)
        finally:
            self.subject = outer

    def _node(
        self,
        node: ChildNode | Node,
        path: str,
        named: bool,
        context: str = "",
        parent_type: str = "",
    ) -> bool:
        node.name = clean_name(node.name) or node.name
        node.cite = self.cites(node.cite)
        is_node = isinstance(node, Node)
        if is_node and node.type == "photo_spot":
            self.photo_spot_name(node)
        if named and (not node.cite or norm(node.name) not in norm(self.cited_text(node.cite))):
            found = self.find_units(node.name)
            if found:
                node.cite = sorted(set(node.cite) | set(found[:2]))
            else:
                near = self.near_unit(node.name)
                if near is None:
                    self.issue("NAME_NOT_IN_SOURCE", path, node.name)
                    return False
                node.cite = sorted(set(node.cite) | {near})
                self.issue("NAME_APPROXIMATE", path, node.name)
                if is_node:
                    node.review.append("名称与原文写法不完全一致，请核对")
        if not node.cite:
            self.issue("NO_EVIDENCE", path, node.name)
            return False
        source = self.cited_text(node.cite)
        context = context or self.before(node.cite)
        scope = self.scope(node.name, source, context) + "\n" + self.section(node.cite)
        # A notice's name is a short heading; its copied text is the description.
        if (
            is_node
            and node.type == "notice"
            and not self.text_ok(node.description or node.name, node.cite, 0.6)
        ):
            self.issue("TEXT_NOT_IN_SOURCE", path, node.description or node.name)
            return False
        # Transport, meeting and free-time names are worded by the model: numbers only.
        if not named and missing_numbers(node.name, source + "\n" + self.day_text):
            self.issue("NUMBER_NOT_IN_SOURCE", f"{path}.name", node.name)
            return False
        if node.inclusion == "package_item" and parent_type != "package":
            self.issue("ATTRIBUTE_UNSUPPORTED", f"{path}.inclusion", "package_item")
            node.inclusion = "unknown"
        self.check_attrs(node, scope, path, node.name)
        grounded = source + "\n" + context
        for field in ("duration_text", "price_text", "clock_text", "description"):
            self.check_numbers(node, field, grounded, path)
        includes = [x for x in node.includes if bigram_cover(x.removeprefix("含"), grounded) >= 0.6]
        if len(includes) != len(node.includes):
            dropped = [x for x in node.includes if x not in includes]
            self.issue("TEXT_NOT_IN_SOURCE", f"{path}.includes", "、".join(dropped))
            node.includes = includes
        if is_node:
            self.check_type(node, scope, path)
            if node.highlight and not re.search(HIGHLIGHT, scope):
                self.issue("ATTRIBUTE_UNSUPPORTED", f"{path}.highlight", node.name)
                node.highlight = False
            if node.spot_label and norm(node.spot_label) not in norm(grounded):
                node.spot_label = ""
            if node.min_participants is not None and missing_numbers(
                str(node.min_participants), grounded
            ):
                self.issue("NUMBER_NOT_IN_SOURCE", f"{path}.min_participants", "")
                node.min_participants = None
            self.check_numbers(node, "booking_note", grounded, path)
            if node.type == "shopping":
                node.description = ""
            if node.transport:
                for field in (
                    "distance_text",
                    "duration_text",
                    "times_text",
                    "service_no",
                    "departure_local_time",
                    "arrival_local_time",
                ):
                    self.check_numbers(node.transport, field, grounded, path + ".transport")
                offset = node.transport.arrival_day_offset
                if offset is not None and not re.search(
                    rf"(?:\+\s*{offset}|{offset}天)" + (r"|次日|翌日" if offset == 1 else ""),
                    grounded,
                ):
                    self.issue(
                        "NUMBER_NOT_IN_SOURCE", path + ".transport.arrival_day_offset", str(offset)
                    )
                    node.transport.arrival_day_offset = None
            for part in ("alternative", "disclaimer", "extra_cost_note"):
                sub = getattr(node, part)
                if sub is None:
                    continue
                sub.cite = self.cites(sub.cite) or node.cite
                condition = getattr(sub, "condition", "")
                if not self.text_ok(sub.text, sub.cite, 0.6) or not self.text_ok(
                    condition, sub.cite, 0.6
                ):
                    self.issue("TEXT_NOT_IN_SOURCE", f"{path}.{part}", sub.text)
                    setattr(node, part, None)
                    continue
                if missing_numbers(sub.text + condition, self.cited_text(sub.cite) + grounded):
                    self.issue("NUMBER_NOT_IN_SOURCE", f"{path}.{part}", sub.text)
                    setattr(node, part, None)
                    continue
                self.cited.update(sub.cite)
            kept = []
            for index, child in enumerate(node.children):
                if self.node(child, f"{path}.children[{index}]", True, grounded, node.type):
                    kept.append(child)
            node.children = kept
        self.cited.update(node.cite)
        return True


def meal_column(units: dict[int, str], unit: int) -> int | None:
    """Index of the MEAL column in the table header row above an overview row."""
    for back in range(unit - 1, max(0, unit - 40), -1):
        cells = [c.strip() for c in units.get(back, "").split("|")]
        for index, cell in enumerate(cells):
            if len(cells) > 2 and re.fullmatch(r"(?i)meals?|餐|餐食|用餐|膳食|三餐", cell):
                return index
    return None


def check_day(
    out: DayOut, day: Day, units: dict[int, str], header_unit: int
) -> tuple[Day, set[int]]:
    allowed = day.units + day.overview_units
    checker = DayChecker(units, allowed)
    items = []
    for index, node in enumerate(out.items):
        # A bare heading ("自费推荐行程：") is not a stop; one that carries text is kept.
        if label_only(node.name) and not node.children and not node.description.strip():
            if not any(is_risky(units[c]) for c in checker.cites(node.cite)):
                checker.cited.update(checker.cites(node.cite))
            continue
        if checker.node(node, f"items[{index}]", node.type in NAMED):
            items.append(node)
    out.items = items
    for meal_name in ("breakfast", "lunch", "dinner"):
        meal = getattr(out.meals, meal_name)
        meal.cite = checker.cites(meal.cite)
        if meal.status != "unknown" and not meal.cite:
            checker.issue("NO_EVIDENCE", f"meals.{meal_name}", meal.text)
            setattr(out.meals, meal_name, type(meal)())
            continue
        checker.check_numbers(meal, "text", checker.cited_text(meal.cite), f"meals.{meal_name}")
        checker.cited.update(meal.cite)
    if all(getattr(out.meals, m).status == "unknown" for m in ("breakfast", "lunch", "dinner")):
        for unit in day.units + day.overview_units:
            text = units[unit]
            column = meal_column(units, unit) if "|" in text else None
            cells = [c.strip() for c in text.split("|")]
            empty_cell = (
                column is not None
                and column < len(cells)
                and cells[column]
                in (
                    "/",
                    "／",
                    "—",
                    "-",
                    "×",
                    "无",
                )
            )
            if re.search(r"(?:^|\s)餐\s*[:：]?\s*[/／—-]\s*$", text) or empty_cell:
                for m in ("breakfast", "lunch", "dinner"):
                    setattr(
                        out.meals,
                        m,
                        type(out.meals.breakfast)(status="self", text="不含", cite=[unit]),
                    )
                checker.cited.add(unit)
                break
    # "餐：午、晚" lists the meals included; a meal it leaves out is not included.
    for unit in day.units:
        listed = re.search(r"(?:^|\s)餐\s*[:：]\s*([早午中晚酒店、,，\s]{1,12})$", units[unit])
        if not listed:
            continue
        said = listed.group(1)
        present = {
            "breakfast": "早" in said,
            "lunch": "午" in said or "中" in said,
            "dinner": "晚" in said,
        }
        if any(present.values()):
            for m, is_listed in present.items():
                if not is_listed and getattr(out.meals, m).status == "unknown":
                    setattr(
                        out.meals,
                        m,
                        type(out.meals.breakfast)(status="self", text="不含", cite=[unit]),
                    )
                    checker.cited.add(unit)
        break
    out.stay.cite = checker.cites(out.stay.cite)
    if out.stay.kind != "unknown" and not out.stay.cite:
        checker.issue("NO_EVIDENCE", "stay", ",".join(out.stay.names))
        out.stay = type(out.stay)()
    stay_text = checker.cited_text(out.stay.cite) + "\n" + checker.day_text
    names = [n for n in out.stay.names if bigram_cover(n, stay_text) >= 0.7]
    if len(names) != len(out.stay.names):
        dropped = [n for n in out.stay.names if n not in names]
        checker.issue("NAME_NOT_IN_SOURCE", "stay.names", "、".join(dropped))
        out.stay.names = names
    for field in ("grade_text", "check_in_text", "city", "room_type"):
        checker.check_numbers(out.stay, field, stay_text, "stay")
    if out.stay.grade_text:
        out.stay.grade_basis = "supplier_description"
    if out.stay.consecutive_nights is not None and missing_numbers(
        str(out.stay.consecutive_nights), stay_text
    ):
        checker.issue("NUMBER_NOT_IN_SOURCE", "stay.consecutive_nights", "")
        out.stay.consecutive_nights = None
    checker.cited.update(out.stay.cite)
    for part in ("services", "port_call"):
        sub = getattr(out, part)
        if sub is not None:
            sub.cite = checker.cites(sub.cite)
            if not sub.cite:
                setattr(out, part, None)
            else:
                checker.cited.update(sub.cite)
                sub_text = checker.cited_text(sub.cite)
                fields = (
                    ("arrive_text", "depart_text", "all_aboard_text", "port")
                    if part == "port_call"
                    else ("note",)
                )
                for field in fields:
                    checker.check_numbers(sub, field, sub_text, part)
    notes = []
    for index, note in enumerate(out.notes):
        note.cite = checker.cites(note.cite)
        if not note.cite:
            continue
        # A label that trails a note ("…，特别安排❀") belongs to the next line, not this one.
        note.text = TRAILING_LABEL.sub("", note.text).strip()
        text = note.text
        # The day's own transport, stay and meal lines, and bare labels, are fields or
        # headings: they are covered, not repeated at the foot of the day.
        # A line with fees or conditions ("住宿：酒店不提供洗漱用品，请自备") stays a note.
        if not is_risky(text) and (
            field_line(text)
            or label_only(text)
            or (re.fullmatch(r"夜宿(?:飞机|火车|邮轮|船)上?", text) and out.stay.kind != "unknown")
        ):
            if TRAVEL_LINE.match(text) and not out.travel_text:
                out.travel_text = TRAVEL_LINE.sub("", text)
            checker.cited.update(note.cite)
            continue
        if not checker.text_ok(note.text, note.cite) or missing_numbers(
            note.text, checker.cited_text(note.cite)
        ):
            checker.issue("TEXT_NOT_IN_SOURCE", f"notes[{index}]", note.text)
            continue
        notes.append(note)
        checker.cited.update(note.cite)
    out.notes = notes
    # Model-worded day fields (title, summary, travel line) keep only numbers the day states.
    for field in ("title", "summary", "travel_text"):
        checker.check_numbers(out, field, checker.day_text, "day")
    for field in ("countries", "cities"):
        names = getattr(out, field)
        kept = [name for name in names if norm(name) in norm(checker.day_text)]
        if kept != names:
            checker.issue("TEXT_NOT_IN_SOURCE", f"day.{field}", "目的地须有当天原文依据")
        setattr(out, field, kept)
    out.travel_text = TRAVEL_LINE.sub("", out.travel_text).strip()
    if re.fullmatch(r"[无/／—\-]*", out.travel_text):
        out.travel_text = ""
    if not out.title:
        out.title = units[header_unit]
    out.title_cite = checker.cites(out.title_cite)
    checker.cited.update(out.title_cite)
    checker.cited.add(header_unit)
    merged = Day(**{**day.model_dump(), **out.model_dump()})
    merged.issues = checker.issues
    merged.status = "extracted"
    return merged, checker.cited


def check_route(
    out: RouteOut, units: dict[int, str], allowed: list[int], listed_name: str = ""
) -> tuple[RouteOut, list[dict], set[int]]:
    """Cover and terms: every kept item is copied from units it cites, numbers included."""
    allowed_set = set(allowed)
    issues: list[dict] = []
    cited: set[int] = set()
    all_text = "\n".join(units.values())

    def issue(code, path, detail=""):
        issues.append({"code": code, "path": path, "detail": (detail or "")[:160]})

    def source(cite):
        return "\n".join(units[c] for c in cite)

    def recite(value: str) -> list[int]:
        """Units that hold the value: one close unit, or up to three adjacent ones."""
        best = [i for i in allowed if bigram_cover(value, units[i]) >= 0.8]
        if best:
            return best[:3]
        ranked = sorted(allowed, key=lambda i: bigram_cover(value, units[i]), reverse=True)
        top = sorted(ranked[:3])
        if top and top[-1] - top[0] <= 4 and bigram_cover(value, source(top)) >= 0.7:
            return top
        return []

    def verify(item, path, field="text"):
        item.cite = sorted({c for c in item.cite if c in allowed_set})
        value = getattr(item, field, "") or ""
        if not item.cite or bigram_cover(value, source(item.cite)) < 0.7:
            item.cite = recite(value)
            if not item.cite:
                issue("TEXT_NOT_IN_SOURCE", path, value)
                return False
        if missing_numbers(value, source(item.cite)):
            issue("NUMBER_NOT_IN_SOURCE", path, value)
            return False
        cited.update(item.cite)
        return True

    def numbers_of(item, fields, path):
        for field in fields:
            value = getattr(item, field, "") or ""
            if value and missing_numbers(value, source(item.cite)):
                issue("NUMBER_NOT_IN_SOURCE", f"{path}.{field}", value)
                setattr(item, field, "")

    # Title and cover: model-worded, so held to the document's own words and numbers.
    outside_text = source(allowed) + "\n" + listed_name
    if out.title and (
        bigram_cover(out.title, outside_text) < 0.6 or missing_numbers(out.title, outside_text)
    ):
        issue("TEXT_NOT_IN_SOURCE", "title", out.title)
        out.title = ""
    if out.subtitle and (
        bigram_cover(out.subtitle, outside_text) < 0.6
        or missing_numbers(out.subtitle, outside_text)
    ):
        issue("TEXT_NOT_IN_SOURCE", "subtitle", out.subtitle)
        out.subtitle = ""
    countries = [c for c in out.countries if norm(c) and norm(c) in norm(all_text)]
    if len(countries) != len(out.countries):
        issue("TEXT_NOT_IN_SOURCE", "countries", "、".join(set(out.countries) - set(countries)))
        out.countries = countries
    if out.depart_city and norm(out.depart_city) not in norm(all_text):
        issue("TEXT_NOT_IN_SOURCE", "depart_city", out.depart_city)
        out.depart_city = ""
    if out.nights is not None and not re.search(rf"(?<!\d){out.nights}\s*晚", digits(all_text)):
        issue("NUMBER_NOT_IN_SOURCE", "nights", str(out.nights))
        out.nights = None

    for field in ("cover_facts", "selling_points", "highlights", "inclusions", "exclusions"):
        setattr(
            out, field, [x for i, x in enumerate(getattr(out, field)) if verify(x, f"{field}[{i}]")]
        )
    # Selling points are short tags: split listed ones, drop headings, long lines and repeats.
    tags: list[Item] = []
    for item in out.selling_points:
        parts = re.split(r"[，；;|丨]+|,(?!\d{3})", item.text)
        # "含全餐、推自费退团费、0购物" lists tags; "成都、重庆双飞" is one tag.
        parts = [
            x
            for part in parts
            for x in (
                part.split("、") if all(len(norm(y)) >= 3 for y in part.split("、")) else [part]
            )
        ]
        for part in parts:
            key = norm(part)
            if (
                2 <= len(key) <= 12
                and not label_only(part)
                and not SECTION_TITLE.search(key)
                and not missing_numbers(part, source(item.cite))
            ):
                tags.append(Item(text=part.strip(), cite=item.cite))
    keys = [norm(t.text) for t in tags]
    out.selling_points = [
        t
        for i, t in enumerate(tags)
        if keys[i] not in keys[:i] and not any(keys[i] != k and keys[i] in k for k in keys)
    ]
    # The same point said twice (a cover picture and the body text often repeat each other):
    # keep the fuller wording.
    for field in ("selling_points", "highlights", "cover_facts"):
        items = getattr(out, field)
        texts = [getattr(x, "title", "") + x.text for x in items]
        keep = []
        for i, x in enumerate(items):
            repeated = any(
                j != i
                and getattr(items[j], "category", "") == getattr(x, "category", "")
                and bigram_cover(texts[i], texts[j]) >= 0.85
                and (len(norm(texts[j])), -j) > (len(norm(texts[i])), -i)
                for j in range(len(items))
            )
            if not repeated:
                keep.append(x)
        setattr(out, field, keep)
    for i, highlight in enumerate(out.highlights):
        numbers_of(highlight, ("title",), f"highlights[{i}]")
    prices = []
    for i, price in enumerate(out.prices):
        if not verify(price, f"prices[{i}]"):
            continue
        if missing_numbers(price.amount, source(price.cite)):
            issue("NUMBER_NOT_IN_SOURCE", f"prices[{i}].amount", price.text)
            continue
        prices.append(price)
    out.prices = prices
    shopping = []
    for i, stop in enumerate(out.shopping):
        if verify(stop, f"shopping[{i}]", "name"):
            numbers_of(stop, ("duration_text",), f"shopping[{i}]")
            text = source(stop.cite)
            stop.categories = [c for c in stop.categories if bigram_cover(c, text) >= 0.6]
            shopping.append(stop)
    out.shopping = shopping
    optional_items = []
    for i, item in enumerate(out.optional_items):
        if verify(item, f"optional_items[{i}]", "name"):
            numbers_of(item, ("price_text", "duration_text", "note"), f"optional_items[{i}]")
            optional_items.append(item)
    out.optional_items = optional_items
    for field in ("single_room", "child", "tips", "cancellation", "deposit"):
        setattr(
            out.policies,
            field,
            [
                x
                for i, x in enumerate(getattr(out.policies, field))
                if verify(x, f"policies.{field}[{i}]")
            ],
        )
    for key in ("inclusions", "exclusions"):
        seen: set[str] = set()
        kept = []
        for item in getattr(out, key):
            if norm(item.text) not in seen:
                seen.add(norm(item.text))
                kept.append(item)
        setattr(out, key, kept)
    for group in out.notices:
        group.items = [
            x for i, x in enumerate(group.items) if verify(x, f"notices.{group.category}[{i}]")
        ]
    out.notices = [g for g in out.notices if g.items]
    out.title_cite = sorted({c for c in out.title_cite if c in allowed_set})
    out.departure_dates_cite = sorted({c for c in out.departure_dates_cite if c in allowed_set})
    if out.departure_dates_text and missing_numbers(
        out.departure_dates_text, source(out.departure_dates_cite) or outside_text
    ):
        issue("NUMBER_NOT_IN_SOURCE", "departure_dates_text", out.departure_dates_text)
        out.departure_dates_text = ""

    def validate_fact(fact, path):
        fact.cite = [c for c in fact.cite if c in allowed_set]
        values = fact.model_dump(mode="json", exclude={"cite"})
        meaningful = any(v not in (None, "", [], {}) for v in values.values())
        if not meaningful:
            return fact
        evidence = source(fact.cite)
        numeric = " ".join(str(v) for v in values.values() if isinstance(v, (str, int, float)))
        if (
            not evidence
            or not fact.raw
            or bigram_cover(fact.raw, evidence) < 0.7
            or missing_numbers(numeric, evidence)
        ):
            issue("EXTENSION_UNSUPPORTED", path, "扩展字段缺少原文依据，保留未说明")
            return type(fact)()
        cited.update(fact.cite)
        return fact

    for key in ("applicability", "formation", "meeting"):
        setattr(out, key, validate_fact(getattr(out, key), key))
    for key in ("child", "senior", "pregnancy", "visa", "insurance"):
        setattr(
            out.traveler_requirements,
            key,
            validate_fact(getattr(out.traveler_requirements, key), "traveler_requirements." + key),
        )
    out.cancellation_tiers = [
        validate_fact(x, f"cancellation_tiers.{i}") for i, x in enumerate(out.cancellation_tiers)
    ]
    out.shopping_cite = [c for c in out.shopping_cite if c in allowed_set]
    if out.shopping_status == "none":
        # The model judges what counts as "no shopping"; the code only checks that the words it
        # quotes are in the cited line and say "none" (a zero or a negation), not just name shops.
        quote = norm(out.shopping_quote)
        if not (
            quote
            and quote in norm(source(out.shopping_cite))
            and NEGATION.search(out.shopping_quote)
        ):
            issue("ATTRIBUTE_UNSUPPORTED", "shopping_status", "原文未明确无购物")
            out.shopping_status = "unknown"
    if out.shopping_status != "none":
        out.shopping_quote = ""
    out.meal_counts = validate_fact(out.meal_counts, "meal_counts")
    if out.shopping:
        out.shopping_status = "present"
    cited.update(out.title_cite, out.departure_dates_cite, out.shopping_cite)
    return out, issues, cited


def extract(
    doc: Document, layout: Layout, model: Model, meta: dict, pool: ThreadPoolExecutor, sha256: str
) -> RouteContent:
    units = {u.id: u.text for u in layout.units}
    issues: list[dict] = [{"code": p, "path": "days", "detail": ""} for p in layout.problems]

    if not layout.days:
        split_days_by_model(layout, model, meta.get("days"), issues)
    blocks = layout.days
    if doc.media == "pdf" and layout.method != "model":
        refine_pdf_days(layout, model, issues)
    # Where the last day ends is a reading question, not a layout rule: the terms (接待标准, fee
    # lists, notices) follow it under headings that vary by supplier, so the model is asked once.
    if blocks:
        last = blocks[-1]
        try:
            answer = model.ask(
                prompts.LAST_DAY_END,
                {"units": [{"id": i, "text": units[i][:80]} for i in last.units[:120]]},
                LastDayEnd,
                max_tokens=200,
            )
            if answer.last_unit in last.units:
                cut = last.units.index(answer.last_unit) + 1
                if cut < len(last.units):
                    layout.outside.extend(last.units[cut:])
                    last.units = last.units[:cut]
                    issues.append(
                        {
                            "code": "LAST_DAY_TRIMMED",
                            "path": f"days[{len(blocks) - 1}]",
                            "detail": "",
                        }
                    )
        except ModelError as error:
            issues.append({"code": "LAST_DAY_UNCHECKED", "path": "days", "detail": str(error)})
    outside = sorted(set(layout.outside))
    pictured = {u.id for u in layout.units if u.origin == "image"}
    day_titles = [{"day": b.day, "header": units[b.header_unit][:60]} for b in blocks]

    def run_route():
        data = {
            "listed_name": meta.get("name", ""),
            "units": [
                {"id": i, "text": units[i], **({"picture": True} if i in pictured else {})}
                for i in outside
            ],
            "days": day_titles,
        }
        return model.ask(prompts.ROUTE, data, RouteOut, max_tokens=16000, accept=route_usable)

    def route_usable(out: RouteOut) -> bool:
        return bool(out.title or out.highlights or out.inclusions or out.exclusions or out.notices)

    def usable(out: DayOut) -> bool:
        # An answer with no title, nodes, meals or stay says nothing about a real day.
        return bool(
            out.title
            or out.items
            or out.stay.kind != "unknown"
            or any(
                getattr(out.meals, m).status != "unknown" for m in ("breakfast", "lunch", "dinner")
            )
        )

    def run_day(block):
        data = {
            "route": meta.get("name", ""),
            "day": block.day,
            "day_end": block.day_end,
            "units": [{"id": i, "text": units[i]} for i in block.units],
            "overview": [{"id": i, "text": units[i]} for i in block.overview_units],
        }
        base = Day(
            day=block.day,
            day_end=block.day_end,
            units=block.units,
            overview_units=block.overview_units,
        )

        def dropped(day: Day) -> int:
            lost = ("NAME_NOT_IN_SOURCE", "NO_EVIDENCE", "TEXT_NOT_IN_SOURCE")
            return sum(1 for i in day.issues if i["code"] in lost) + (0 if day.items else 5)

        try:
            out = model.ask(prompts.DAY, data, DayOut, max_tokens=12000, accept=usable)
            checked, cited = check_day(out, base, units, block.header_unit)
            if dropped(checked) >= 3 and len(block.units) > 3:
                feedback = "\n".join(
                    f"{i['code']} {i['path']} {i['detail']}" for i in checked.issues[:20]
                )
                try:
                    again = model.ask(
                        prompts.DAY,
                        data,
                        DayOut,
                        max_tokens=12000,
                        feedback=feedback,
                        accept=usable,
                    )
                except ModelError as error:  # the first answer stays
                    checked.issues.append(
                        {"code": "RETRY_FAILED", "path": "", "detail": str(error)[:160]}
                    )
                    return checked, cited
                second, second_cited = check_day(again, base, units, block.header_unit)
                if dropped(second) < dropped(checked):
                    checked, cited = second, second_cited
                checked.issues.append({"code": "RETRIED", "path": "", "detail": ""})
            return checked, cited
        except ModelError as error:
            base.status = "source_only"
            base.title = units[block.header_unit]
            code = "DAY_EMPTY_RESULT" if "EMPTY" in str(error) else "DAY_MODEL_FAILED"
            base.issues = [{"code": code, "path": "", "detail": str(error)[:200]}]
            return base, set()

    route_future = pool.submit(run_route)
    day_futures = [pool.submit(run_day, b) for b in blocks]
    results = [f.result() for f in day_futures]
    days = [d for d, _ in results]
    try:
        route_out, route_issues, route_cited = check_route(
            route_future.result(), units, outside, meta.get("name", "")
        )
    except ModelError as error:
        route_out, route_issues, route_cited = (
            RouteOut(),
            [{"code": "ROUTE_MODEL_FAILED", "path": "", "detail": str(error)[:200]}],
            set(),
        )
    issues.extend(route_issues)

    cited = set(route_cited)
    for _, day_cited in results:
        cited |= day_cited
    headers = {b.header_unit for b in blocks}
    direct = cited | headers
    in_days = {u for b in blocks for u in b.units}
    # Description sentences that continue a cited unit belong to that node.
    label = re.compile(r"^(?:【|餐|住|行|注|温馨|提示|备注|如遇|若|第\s*\d|D\d)")
    for block in blocks:
        for prev, unit in zip(block.units, block.units[1:], strict=False):
            text = units[unit]
            if prev in cited and unit not in cited and not label.match(text) and not is_risky(text):
                cited.add(unit)
    unmapped = []
    skipped = 0
    for unit in layout.units:
        if unit.id in cited or unit.id in headers:
            continue
        heading = HEADING.search(re.sub(r"\s", "", unit.text))
        risky = is_risky(unit.text) and not heading
        if unit.id not in in_days and len(unit.text) < 12 and not risky:
            skipped += 1  # short headings outside days: section labels, table headers
            continue
        unmapped.append(
            {
                "unit": unit.id,
                "text": unit.text[:200],
                "in_day": unit.id in in_days,
                "risky": risky,
            }
        )

    # Nothing about money, shopping, conditions or safety may silently leave the page:
    # a risky unit no field took is attached as-is to its day's notes or to the notices.
    day_of = {u: d for b, d in zip(blocks, days, strict=True) for u in b.units}
    groups = {}
    for entry in unmapped:
        if not entry["risky"]:
            continue
        text = units[entry["unit"]]
        day = day_of.get(entry["unit"])
        if day is not None:
            day.notes.append(Note(text=text, cite=[entry["unit"]], auto=True))
            day.issues.append({"code": "AUTO_ATTACHED", "path": "notes", "detail": text[:120]})
        else:
            category = next((c for c, pattern in NOTICE_KEYS if re.search(pattern, text)), "other")
            groups.setdefault(category, []).append(Item(text=text, cite=[entry["unit"]], auto=True))
            issues.append(
                {"code": "AUTO_ATTACHED", "path": f"notices.{category}", "detail": text[:120]}
            )
        entry["attached"] = True
    for category, items in groups.items():
        existing = next((g for g in route_out.notices if g.category == category), None)
        if existing:
            existing.items.extend(items)
        else:
            route_out.notices.append(NoticeGroup(category=category, items=items))
    attached = sum(1 for u in unmapped if u.get("attached"))
    unmapped = [u for u in unmapped if not u.get("attached")]

    days_count = max(((d.day_end or d.day) for d in days), default=0)
    content = RouteContent(
        **route_out.model_dump(),
        code=meta.get("code", ""),
        listed_name=meta.get("name", ""),
        days_count=days_count,
        days=days,
        source=Source(
            file_name=doc.file_name,
            media=doc.media,
            pages=doc.pages,
            sha256=sha256,
            image_pages=doc.image_pages,
            scanned=doc.scanned,
            pictures=doc.transcribed,
            parser=PARSER,
            model=model.name,
            extracted_at=datetime.now(UTC).isoformat(timespec="seconds"),
        ),
        units=[
            {"id": u.id, "text": u.text, "page": u.page, "line": u.line, "origin": u.origin}
            for u in layout.units
        ],
    )
    if not content.title:
        content.title = meta.get("name", "") or doc.file_name
    if meta.get("days") and meta["days"] != days_count:
        issues.append(
            {
                "code": "DAYS_DIFFER_FROM_LISTING",
                "path": "days",
                "detail": f"附件 {days_count} 天，线路登记 {meta['days']} 天",
            }
        )
    read = {r["page"] for r in doc.transcribed if r.get("page") and not r.get("error")}
    unread = [p for p in doc.image_pages if p not in read]
    if unread:
        issues.append(
            {
                "code": "IMAGE_PAGES_NOT_READ",
                "path": "source",
                "detail": "第 " + "、".join(map(str, unread)) + " 页为图片，其中文字未读取",
            }
        )
    if doc.garbled_pages:
        issues.append(
            {
                "code": "PDF_TEXT_GARBLED",
                "path": "source",
                "detail": "第 "
                + "、".join(map(str, doc.garbled_pages))
                + " 页文字层乱码，已改用图片转写",
            }
        )
    if doc.reordered_pages:
        issues.append(
            {
                "code": "PDF_PAGE_REORDERED",
                "path": "source",
                "detail": "第 "
                + "、".join(map(str, doc.reordered_pages))
                + " 页表格读取顺序错乱，已按版式重读",
            }
        )
    if doc.unordered_pages:
        issues.append(
            {
                "code": "PDF_PAGE_UNORDERED",
                "path": "source",
                "detail": "第 "
                + "、".join(map(str, doc.unordered_pages))
                + " 页表格顺序错乱，已改用图片转写",
            }
        )
    if doc.pictures_unread:
        issues.append(
            {
                "code": "PICTURES_NOT_READ",
                "path": "source",
                "detail": f"{doc.pictures_unread} 张图片的文字未读取（未用 --vision 或超过上限）",
            }
        )
    for record in doc.transcribed:
        if record.get("error"):
            issues.append({"code": "PICTURE_NOT_READ", "path": "source", "detail": record["key"]})
    image_units = sum(1 for u in layout.units if u.origin == "image")
    if image_units:
        issues.append(
            {
                "code": "IMAGE_TEXT_TRANSCRIBED",
                "path": "source",
                "detail": f"{len(doc.transcribed)} 张图片，转写 {image_units} 条，发布前核对",
            }
        )
    for day in days:
        for item in day.issues:
            issues.append({**item, "path": f"D{day.day}.{item['path']}"})
    content.quality = Quality(
        days_expected=meta.get("days"),
        days_found=days_count,
        days_extracted=sum(1 for d in days if d.status == "extracted"),
        units=len(layout.units),
        mapped_units=len(layout.units) - len(unmapped),
        direct_units=len(direct),
        auto_attached=attached,
        inferred_units=len(layout.units) - len(unmapped) - len(direct) - attached,
        unmapped=unmapped,
        issues=issues,
        removed=doc.removed,
        image_units=sum(1 for u in layout.units if u.origin == "image"),
    )
    content.quality.confidence, content.quality.review_reasons = assess(content)
    return content


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
