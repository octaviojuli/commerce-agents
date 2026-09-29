"""Turn the model's reading of a message into checked field changes.

Evidence must be the customer's own words. Places and holidays are parsed by rule;
the party is merged child by child so a partial answer never fills in the others.
A field with no saved value is filled directly; a field that already has a value
becomes a proposal the advisor adopts or keeps.
"""

import re
from datetime import date

from cloud_warehouse import advisor_holidays, destinations, travel_requirements

from . import need as needs
from .grounding import chinese_numbers
from .need import Need

PUNCT = str.maketrans("，。；：！？（）", ",.;:!?()")


def grounded(message: str, evidence: str) -> str:
    """The span of ``message`` the evidence quotes, ignoring punctuation width; or ""."""
    evidence = (evidence or "").strip()
    if not evidence:
        return ""
    if evidence in message:
        return evidence
    source = [(c.translate(PUNCT), i) for i, c in enumerate(message) if not c.isspace()]
    target = "".join(c.translate(PUNCT) for c in evidence if not c.isspace())
    joined = "".join(c for c, _ in source)
    at = joined.find(target) if target else -1
    if at >= 0:
        return message[source[at][1] : source[at + len(target) - 1][1] + 1]
    parts = [p.strip() for p in re.split(r"[、，,；;/…]+|\.{2,}", evidence) if len(p.strip()) >= 2]
    if len(parts) > 1:
        found = [grounded(message, p) for p in parts]
        if all(found):
            return max(found, key=len)
    return ""


def places(message: str, saved) -> tuple[dict | None, str]:
    """Destinations stated in the message, by rule. None when the message names none."""
    if travel_requirements.route_question(message):
        return None, ""
    spans = []
    must, examples, exclude, regions = travel_requirements.location_intent(message, spans)
    if not (must or examples or exclude or regions):
        return None, ""
    current = saved or needs.Places()
    if re.search(r"加上|再加|还要去|也要去", message) and not re.search(
        r"改成|改去|换成|只去", message
    ):
        must = [*current.must, *must]
    if exclude and not must and not regions and not examples:
        must = [x for x in current.must if x not in exclude]
        examples = [x for x in current.examples if x not in exclude]
        regions = current.regions
    exclude = list(dict.fromkeys([*current.exclude, *exclude]))
    must = (
        [x for x in dict.fromkeys(destinations.canonical_terms(" ".join(must))) if x not in exclude]
        if must
        else []
    )
    value = {"must": must, "examples": examples, "regions": regions, "exclude": exclude}
    return value, "，".join(dict.fromkeys(spans))[:200]


def window(message: str, value, today: date):
    """Holiday words come from the operator table; a bare month is a whole month, inferred."""
    # "改元旦" means travelling around the holiday, as operators define "前后".
    holiday = advisor_holidays.window(message if "前后" in message else message + " 前后", today)
    if holiday and holiday.get("value"):
        v = holiday["value"]
        if "前后" not in message:
            holiday["evidence"] = holiday["evidence"].replace("前后", "")
        return (
            {"start": v["start"], "end": v["end"], "label": holiday["evidence"]},
            holiday["evidence"],
            holiday["hint"],
        )
    if not value:
        return None, "", ""
    from .queries import DATE

    exact = list(DATE.finditer(message))
    if len(exact) == 1 and not re.search(r"前后|左右|放宽|附近|不限|至|到\s*\d", message):
        m = exact[0]
        try:
            year = int(m[1]) if m[1] else int(str(value.get("start", today.isoformat()))[:4])
            day = date(year, int(m[2]), int(m[3])).isoformat()
            value = {"start": day, "end": day, "label": m[0]}
        except (ValueError, TypeError):
            pass
    hint = ""
    if not re.search(r"20\d{2}\s*年", message):
        hint = "原话没说年份，按最近的日期推断"
    label = value.get("label", "") if isinstance(value, dict) else ""
    return {**value, "label": label}, "", hint


ROOM_KINDS = {
    "大床": "doubles",
    "双人": "doubles",
    "双床": "twins",
    "标间": "twins",
    "单人": "singles",
    "单间": "singles",
    "单住": "singles",
}
ROOM_TOKEN = re.compile(r"(" + "|".join(ROOM_KINDS) + r")|(\d+)\s*[间个]")
# Words that make a count something other than the rooms wanted now: a step ("再加一间"), a
# refusal ("不要两间"), an earlier count ("原来两间", "两间改一间"), a choice, a question or a
# supposition. The model reads these; the rule stays out.
ROOM_UNCLEAR = re.compile(
    r"再加|增加|多加|追加|减|多订|多要|少订|少要|去掉|不要|不用|不住|不是|别|没|原|之前|本来|以前|先|后来"
    r"|改|换|调|变|退|还是|或|如果|假如|要是|的话|多少|几|吗|呢|[？?]"
)


def rooms(message: str) -> dict:
    """Room counts a plain statement gives, by rule: {"doubles": 1, ...}; or {} for any other.

    A plain statement names each room kind once, each with one count beside it ("1间大床房",
    "大床房1间"); a count between two room words ("2间大床1间双床") goes to the one that has no
    count yet. Anything else — a word from ``ROOM_UNCLEAR``, a kind named twice, a count
    that belongs to no room or to either of two — gives {}, and the model's reading stands.
    """
    text = chinese_numbers(message)
    if ROOM_UNCLEAR.search(text):
        return {}
    tokens = list(ROOM_TOKEN.finditer(text))
    kinds = [i for i, t in enumerate(tokens) if t[1]]
    if len({ROOM_KINDS[tokens[i][1]] for i in kinds}) != len(kinds):
        return {}
    owners, unclear = {}, []
    for i, token in enumerate(tokens):
        if token[2] is None:
            continue
        near = []
        if (
            i > 0
            and tokens[i - 1][1]
            and re.fullmatch(r"[\s房]*", text[tokens[i - 1].end() : token.start()])
        ):
            near.append(i - 1)
        if (
            i + 1 < len(tokens)
            and tokens[i + 1][1]
            and re.fullmatch(r"[\s的]*", text[token.end() : tokens[i + 1].start()])
        ):
            near.append(i + 1)
        if len(near) == 1:
            owners[i] = near[0]
        elif near:
            unclear.append((i, near))
    for i, near in unclear:
        free = [k for k in near if k not in owners.values()]
        if len(free) != 1:
            return {}
        owners[i] = free[0]
    if sorted(owners.values()) != kinds:
        return {}
    return {ROOM_KINDS[tokens[k][1]]: int(tokens[i][2]) for i, k in owners.items()}


def room_count(value) -> int | None:
    """The model's count for one room kind; None when it is not a number ("一间")."""
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def room_text(counts: dict) -> str:
    names = {"doubles": "大床", "twins": "双床", "singles": "单间"}
    return "、".join(
        f"{name}{counts[k]}间" if counts[k] is not None else f"{name}未读准"
        for k, name in names.items()
        if k in counts
    )


COUPLE = re.compile(
    r"(?:我|俺)(?:跟|和|带)(?:老婆|老公|媳妇|爱人|太太|先生|对象|女朋友|男朋友|老伴)"
    r"|夫妻(?:俩|两个|二人)?|两口子|小两口|老两口|我们俩|二人世界|就我们两个"
    r"|(?:两|2|俩)(?:个|位)?大人"
)
# An older couple: both travellers are the seniors, not two adults beside two seniors.
ELDERLY = re.compile(r"老伴|老两口|老头子|老太婆")
COMPANIONS = re.compile(
    r"孩子|小孩|儿童|宝宝|娃|小朋友|儿子|女儿|老人|长辈|爸|妈|父母|公婆|岳"
    r"|朋友|同事|亲戚|亲友|兄|弟|姐|妹|另外|还有|再加"
)


def couple(value: dict, elderly: bool) -> dict:
    seniors = [x for x in value.get("seniors") or [] if isinstance(x, dict)]
    if elderly:
        seniors = (seniors + [{"age": None}, {"age": None}])[:2]
        return {**value, "adults": 0, "children": [], "seniors": seniors}
    return {**value, "adults": 2 - len(seniors), "children": [], "seniors": seniors}


def merge_party(saved: needs.Party | None, change: dict) -> dict:
    """Merge a partial party. Children are matched by age, then by position.

    A reading that states a total and also counts a senior among the adults ("一家四口加我妈，
    共5人" as 3 adults and a senior) is corrected in that reading only. A composition that no
    longer adds up to a total said earlier drops that total, so the change is kept and the
    change sheet shows the new count to check.
    """
    change = counted_once(change)
    base = (saved or needs.Party()).model_dump(mode="json")
    stated = change.get("total_count") is not None
    if stated:
        base["total_count"] = change["total_count"]
    if "adults" in change and change["adults"] is not None:
        base["adults"] = change["adults"]
    if "seniors" in change and change["seniors"] is not None:
        base["seniors"] = [s for s in change["seniors"] if isinstance(s, dict)]
    if "children" in change and change["children"] is not None:
        new = [c for c in change["children"] if isinstance(c, dict)]
        old = base.get("children")
        if not new:
            base["children"] = []
        elif old and len(new) <= len(old):
            merged = [dict(c) for c in old]
            used = set()
            for c in new:
                index = next(
                    (
                        i
                        for i, o in enumerate(merged)
                        if i not in used
                        and c.get("age") is not None
                        and o.get("age") == c.get("age")
                    ),
                    None,
                )
                if index is None:
                    index = next(
                        (
                            i
                            for i in range(len(merged))
                            if i not in used and merged[i].get("age") is None
                        ),
                        None,
                    )
                if index is None and len(new) == len(old):
                    index = next(i for i in range(len(merged)) if i not in used)
                if index is None:
                    continue
                used.add(index)
                for key in ("age", "bed"):
                    if c.get(key) is not None:
                        merged[index][key] = c[key]
            base["children"] = merged
        else:
            base["children"] = [{"age": c.get("age"), "bed": c.get("bed")} for c in new]
    if not stated and base.get("total_count") is not None and base.get("adults") is not None:
        composed = base["adults"] + len(base.get("children") or []) + len(base.get("seniors") or [])
        if composed != base["total_count"]:
            base["total_count"] = None
    return base


def counted_once(change: dict) -> dict:
    total, adults = change.get("total_count"), change.get("adults")
    seniors = [s for s in change.get("seniors") or [] if isinstance(s, dict)]
    children = [c for c in change.get("children") or [] if isinstance(c, dict)]
    if (
        total is not None
        and adults is not None
        and seniors
        and adults + len(children) + len(seniors) > total
        and adults + len(children) == total
    ):
        return {**change, "adults": max(0, adults - len(seniors))}
    return change


def changes(need: Need, understanding, message: str, today: date, *, turn=None):
    """Checked changes: ``fills`` go in directly, ``proposals`` wait for the advisor."""
    fills, proposals, rejected = {}, [], list(understanding.rejected if understanding else [])
    by_field = {c.field: c for c in (understanding.changes if understanding else [])}
    parsed_places, place_evidence = places(message, need.get("destinations"))
    if parsed_places is not None:
        by_field.pop("destinations", None)
    elif "destinations" in by_field and travel_requirements.route_question(message):
        by_field.pop("destinations")
    candidates = []
    total_match = re.search(
        r"(?<!\d)(\d{1,2})\s*(?:个|位|名)?(?:客人|人)(?![民币])", chinese_numbers(message)
    )
    adult_evidence = bool(
        re.search(r"成人|大人|\d+大(?:\d|$)|全是大人|都是大人", chinese_numbers(message))
    )
    from .model import Change

    if total_match and "party" not in by_field:
        by_field["party"] = Change(
            field="party", value={"total_count": int(total_match[1])}, evidence=message[:200]
        )
    # The rule checks the model's room counts; it never makes a room request of its own.
    said_rooms = rooms(message) if "rooms" in by_field else {}
    review = set()
    if parsed_places is not None:
        candidates.append(("destinations", parsed_places, "said", place_evidence, ""))
    holiday_value, holiday_evidence, holiday_hint = window(message, None, today)
    if holiday_value:
        by_field.pop("window", None)
        candidates.append(("window", holiday_value, "inferred", holiday_evidence, holiday_hint))
    for field, change in by_field.items():
        value = change.value
        source, hint = change.source, change.hint
        evidence = grounded(message, change.evidence)
        if field == "window":
            from .queries import DATES

            if DATES.search(message) and not re.search(r"改成|改为|改到|放宽|确定.*出发", message):
                continue
            value, _, window_hint = window(message, value, today)
            if window_hint:
                source, hint = "inferred", hint or window_hint
        if field == "party" and isinstance(value, dict):
            value = dict(value)
            if total_match:
                value["total_count"] = int(total_match[1])
                if not adult_evidence and not COUPLE.search(message):
                    value.pop("adults", None)
                if not re.search(r"孩子|儿童|小孩|宝宝|小朋友|全是大人|都是大人", message):
                    value.pop("children", None)
            if (
                need.get("party") is None
                and COUPLE.search(message)
                and not COMPANIONS.search(message)
                and value.get("adults") in (None, 0, 2)
                and not value.get("children")
                and len(value.get("seniors") or []) <= 2
            ):
                # Only an initial, complete two-person party can use this shorthand.
                # Later partial answers must retain the already recorded travellers.
                value = couple(value, bool(ELDERLY.search(message)))
            value = merge_party(need.get("party"), value)
        if field == "rooms" and said_rooms and isinstance(value, dict):
            # The model has misread "1间大床" as 2. Where the plain count differs, the advisor
            # sees both and decides; neither reading is saved on its own.
            read = {k: room_count(value.get(k)) for k in said_rooms}
            if read != said_rooms:
                value = {**value, **said_rooms}
                source, hint = (
                    "inferred",
                    "房间数请核对：原话逐项数为 "
                    + room_text(said_rooms)
                    + "，模型读作 "
                    + room_text(read),
                )
                evidence = evidence or grounded(message, message[:200])
                review.add(field)
        if (
            field == "preferences"
            and isinstance(value, list)
            and not re.search(r"孩子|小孩|儿童|宝宝|娃|小朋友", message)
        ):
            # Family travel is not the same as travelling with children.
            value = [v for v in value if v != "family"]
            if not value:
                continue
        if field == "days" and re.search(r"一周|一个星期|七天左右|7天左右", message):
            value, source, hint = {"min": 6, "max": 8}, "inferred", "约一周按 6–8 天"
        if source == "said" and not evidence:
            rejected.append(field)
            continue
        if source == "inferred" and not hint:
            hint = "按原话推断，待确认"
        candidates.append((field, value, source, evidence, hint))
    for field, value, source, evidence, hint in candidates:
        try:
            parsed = needs.parse(field, value)
        except (ValueError, TypeError):
            rejected.append(field)
            continue
        if (parsed is None or parsed == []) and need.get(field) in (None, []):
            continue
        current = need.get(field)
        if _same(current, parsed):
            continue
        item = {
            "field": field,
            "old": needs._json(current),
            "new": needs._json(parsed),
            "source": source,
            "evidence": evidence,
            "hint": hint,
            "turn": turn,
        }
        grows = (
            isinstance(current, list) and isinstance(parsed, list) and set(current) <= set(parsed)
        )
        if field in review:
            proposals.append(item)
        elif blank(current) or grows or (field == "party" and _only_adds(current, parsed)):
            fills[field] = item
        else:
            proposals.append(item)
    return fills, proposals, list(dict.fromkeys(rejected))


def blank(value) -> bool:
    """Unset, or a place value that names nowhere ("找个海岛" gives a theme, not a place)."""
    if value in (None, []):
        return True
    return isinstance(value, needs.Places) and not (value.must or value.regions or value.examples)


def _same(a, b):
    if isinstance(a, needs.Party) and isinstance(b, needs.Party):
        return a.model_dump() == b.model_dump()
    if hasattr(a, "model_dump") and hasattr(b, "model_dump"):
        return a.model_dump() == b.model_dump()
    return a == b


def _only_adds(old: needs.Party, new: needs.Party) -> bool:
    """Answering an open party question (ages, beds, "no children") fills; it changes nothing.

    Adding or removing a traveller, or changing a known age or bed, is a change.
    """
    if old.total_count is not None and new.total_count != old.total_count:
        return False
    if old.adults is not None and new.adults != old.adults:
        return False
    if len(new.seniors) != len(old.seniors):
        return False
    if any(
        o.age is not None and n.age != o.age for o, n in zip(old.seniors, new.seniors, strict=True)
    ):
        return False
    if old.children is None:
        return True
    if new.children is None or len(new.children) != len(old.children):
        return False
    return all(
        (o.age is None or n.age == o.age) and (o.bed is None or n.bed == o.bed)
        for o, n in zip(old.children, new.children, strict=True)
    )
