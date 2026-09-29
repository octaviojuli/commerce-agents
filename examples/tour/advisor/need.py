"""One versioned need per deal: fields with evidence, the party per traveller, and the gates.

The model proposes values; this module decides what they mean for the deal. Readiness,
clarity and the chain of consequences of a change are computed here, never by the model.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["said", "inferred", "explore", "advisor"]
FIELDS = (
    "destinations",
    "window",
    "days",
    "depart_city",
    "party",
    "rooms",
    "budget",
    "preferences",
    "themes",
)
LABELS = {
    "destinations": "目的地",
    "window": "时间",
    "days": "天数",
    "depart_city": "出发",
    "party": "人数",
    "rooms": "房间",
    "budget": "预算",
    "preferences": "偏好",
    "themes": "主题",
}
# What the program does when a field changes (prototype "连锁影响").
SEARCH_FIELDS = {"destinations", "window", "days", "depart_city"}
RANK_FIELDS = {"budget", "preferences", "themes"}
PRICE_FIELDS = {"party", "rooms"}
PREFERENCES = {
    "slow_pace": "节奏别太累",
    "no_shopping": "不进购物店",
    "no_self_pay": "少自费",
    "family": "适合孩子",
    "senior": "照顾老人",
}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Places(Model):
    must: list[str] = Field(default_factory=list, max_length=20)
    examples: list[str] = Field(default_factory=list, max_length=20)
    regions: list[str] = Field(default_factory=list, max_length=10)
    exclude: list[str] = Field(default_factory=list, max_length=20)

    @property
    def any(self):
        return bool(self.must or self.examples or self.regions)


class Window(Model):
    start: date
    end: date
    label: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("出发窗口的结束日期不能早于开始日期")
        return self


class Days(Model):
    min: Annotated[int, Field(ge=1, le=60)]
    max: Annotated[int, Field(ge=1, le=60)]

    @model_validator(mode="after")
    def ordered(self):
        if self.max < self.min:
            raise ValueError("天数范围无效")
        return self


class Child(Model):
    """One child. Age and bed are each unknown until said; nothing is assumed."""

    age: Annotated[int, Field(ge=0, le=17)] | None = None
    bed: bool | None = None


class Senior(Model):
    age: Annotated[int, Field(ge=55, le=110)] | None = None


class Party(Model):
    """Travellers. ``children=None`` means not asked yet; ``[]`` means none travel."""

    total_count: Annotated[int, Field(ge=1, le=80)] | None = None
    adults: Annotated[int, Field(ge=0, le=50)] | None = None
    children: list[Child] | None = Field(default=None, max_length=30)
    seniors: list[Senior] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def seniors_are_not_also_adults(self):
        # "一家四口加我妈，共5人" read as 3 adults and a senior counts her twice: a stated total
        # wins, and the surplus comes off the adults.
        named = len(self.children or []) + len(self.seniors)
        if (
            self.total_count is not None
            and self.adults is not None
            and self.adults + named > self.total_count
        ):
            self.adults = max(0, self.total_count - named)
        return self

    @property
    def total(self):
        if self.total_count is not None:
            return self.total_count
        if self.adults is None:
            return None
        return self.adults + len(self.children or []) + len(self.seniors)


class Rooms(Model):
    doubles: Annotated[int, Field(ge=0, le=50)] = 0
    twins: Annotated[int, Field(ge=0, le=50)] = 0
    singles: Annotated[int, Field(ge=0, le=50)] = 0
    note: str = Field(default="", max_length=200)

    @property
    def total(self):
        return self.doubles + self.twins + self.singles


class Budget(Model):
    per_person: Decimal = Field(gt=0, le=10_000_000)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")


class Value(Model):
    value: Any = None
    source: Source | None = None
    evidence: str = Field(default="", max_length=200)
    hint: str = Field(default="", max_length=300)
    turn: int | None = None


TYPES = {
    "destinations": Places,
    "window": Window,
    "days": Days,
    "depart_city": str,
    "party": Party,
    "rooms": Rooms,
    "budget": Budget,
    "preferences": list,
    "themes": list,
}


def parse(field, value):
    """Validate one field value into its type; raise ValueError when it does not fit."""
    if value is None:
        return None
    kind = TYPES[field]
    if kind is str:
        text = str(value).strip()
        if not text or len(text) > 40:
            raise ValueError("出发地无效")
        return text
    if kind is list:
        items = [str(v).strip() for v in value if str(v).strip()]
        if field == "preferences":
            items = [v for v in items if v in PREFERENCES]
        return list(dict.fromkeys(items))[:12]
    if field == "rooms":
        value = rooms_value(value)
    return kind.model_validate(value)


ROOM_WORDS = {"doubles": r"大床", "twins": r"双床|标间|双标", "singles": r"单间|单人间|单住"}


def rooms_value(value):
    """Rooms as the model may write them: text ("一间大床房"), or with a derived total."""
    if isinstance(value, str):
        from .grounding import chinese_numbers

        text, out = chinese_numbers(value), {"note": value[:200]}
        for key, words in ROOM_WORDS.items():
            m = re.search(rf"(\d+)\s*间\s*(?:{words})|(?:{words})\S{{0,2}}?(\d+)\s*间", text)
            if m:
                out[key] = int(m.group(1) or m.group(2))
        if len(out) == 1:
            raise ValueError("房间没读出房型")
        return out
    if isinstance(value, dict):
        # A count the customer did not mention is none of that room ({"twins": null} is 0).
        known = {
            k: (v if v is not None else (0 if k != "note" else ""))
            for k, v in value.items()
            if k in ("doubles", "twins", "singles", "note")
        }
        if not any(known.get(k) for k in ("doubles", "twins", "singles")):
            raise ValueError("房间没读出房型")
        return known
    return value


class Need(Model):
    destinations: Value = Field(default_factory=Value)
    window: Value = Field(default_factory=Value)
    days: Value = Field(default_factory=Value)
    depart_city: Value = Field(default_factory=Value)
    party: Value = Field(default_factory=Value)
    rooms: Value = Field(default_factory=Value)
    budget: Value = Field(default_factory=Value)
    preferences: Value = Field(default_factory=Value)
    themes: Value = Field(default_factory=Value)

    def get(self, field):
        raw = getattr(self, field).value
        return parse(field, raw) if raw is not None else None

    def dump(self):
        return self.model_dump(mode="json")


def set_field(need: Need, field, value, source, evidence="", hint="", turn=None) -> Need:
    parsed = parse(field, value)
    data = need.dump()
    data[field] = {
        "value": _json(parsed),
        "source": source,
        "evidence": evidence[:200],
        "hint": hint[:300],
        "turn": turn,
    }
    return Need.model_validate(data)


def _json(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


# ---------------------------------------------------------------- gates


def gates(need: Need) -> dict:
    """What the deal can do now. Missing items are layered by what they block."""
    places, window = need.get("destinations"), need.get("window")
    direction = bool((places and places.any) or need.get("themes"))
    search = []
    if not direction:
        search.append("destinations")
    if not window:
        search.append("window")
    quote = []
    party = need.get("party")
    if party is None or party.adults is None:
        quote.append("adults")
    if party is None or party.children is None:
        quote.append("children")
    else:
        if any(c.age is None for c in party.children):
            quote.append("child_ages")
        if any(c.bed is None for c in party.children):
            quote.append("child_beds")
    if party is not None and any(s.age is None for s in party.seniors):
        quote.append("senior_ages")
    if (
        party
        and party.total_count is not None
        and party.adults is not None
        and party.children is not None
        and party.total_count != party.adults + len(party.children) + len(party.seniors)
    ):
        quote.append("party_total")
    rooms = need.get("rooms")
    if not rooms or not rooms.total:
        quote.append("rooms")
    inferred = [
        f
        for f in FIELDS
        if getattr(need, f).source == "inferred" and getattr(need, f).value is not None
    ]
    return {
        "search": {"ready": not search, "missing": search},
        "quote": {"ready": not search and not quote, "missing": search + quote},
        "later": ["passports"],
        "inferred": inferred,
    }


MISSING_TEXT = {
    "destinations": "往哪个方向",
    "window": "什么时候出发",
    "adults": "几位大人",
    "children": "有没有孩子同行",
    "child_ages": "孩子几岁",
    "child_beds": "孩子是否占床",
    "senior_ages": "老人年龄",
    "party_total": "核对总人数与成人、儿童、老人构成",
    "rooms": "住几间房",
    "passports": "护照是否齐全（下单前）",
}


def clarity(need: Need) -> int:
    weights = {
        "window": 25,
        "destinations": 25,
        "party": 20,
        "days": 10,
        "depart_city": 5,
        "budget": 10,
        "rooms": 5,
    }
    score = 0
    for field, weight in weights.items():
        if need.get(field):
            score += weight
    if need.get("themes") and not need.get("destinations"):
        score += 15
    return min(score, 100)


# ---------------------------------------------------------------- display


def show(field, value) -> str:
    """Short Chinese display of a field value, as on the need card and the change card."""
    if value is None or value == []:
        return "未提"
    if field == "destinations":
        v = value if isinstance(value, Places) else Places.model_validate(value)
        parts = []
        if v.must:
            parts.append(" · ".join(v.must))
        elif v.regions:
            parts.append(" · ".join(v.regions))
        if v.examples:
            parts.append("比如" + "、".join(v.examples))
        if v.exclude:
            parts.append("不去" + "、".join(v.exclude))
        return "，".join(parts) or "未提"
    if field == "window":
        v = value if isinstance(value, Window) else Window.model_validate(value)
        span = f"{v.start.month}/{v.start.day}–{v.end.month}/{v.end.day}"
        return f"{span} {v.label}".strip()
    if field == "days":
        v = value if isinstance(value, Days) else Days.model_validate(value)
        return f"{v.min} 天" if v.min == v.max else f"{v.min}–{v.max} 天"
    if field == "party":
        v = value if isinstance(value, Party) else Party.model_validate(value)
        return party_text(v)
    if field == "rooms":
        v = value if isinstance(value, Rooms) else Rooms.model_validate(value)
        parts = [
            f"{label} {n} 间"
            for label, n in (("大床", v.doubles), ("双床", v.twins), ("单间", v.singles))
            if n
        ]
        return " + ".join(parts) or "未提"
    if field == "budget":
        v = value if isinstance(value, Budget) else Budget.model_validate(value)
        return (
            f"每人 ≤ ¥{int(v.per_person):,}"
            if v.currency == "CNY"
            else f"每人 ≤ {v.currency} {v.per_person}"
        )
    if field == "preferences":
        return "、".join(PREFERENCES.get(p, p) for p in value)
    if field == "themes":
        return "、".join(value)
    return str(value)


def party_text(party: Party) -> str:
    if party.adults is None:
        text = f"共 {party.total_count} 人 · 成人构成待确认" if party.total_count else "人数未定"
        if party.children == []:
            text += " · 无儿童"
        elif party.children:
            text += f" · {len(party.children)} 位儿童"
        return text
    text = f"{party.adults} 大"
    if party.children is None:
        text += " · 孩子未问"
    elif party.children:
        ages = [f"{c.age} 岁" for c in party.children if c.age is not None]
        text += f" {len(party.children)} 小" + (f"（{'、'.join(ages)}）" if ages else "")
    if party.seniors:
        ages = [f"{s.age} 岁" for s in party.seniors if s.age is not None]
        text += f" + {len(party.seniors)} 老" + (f"（{'、'.join(ages)}）" if ages else "")
    return (f"共 {party.total_count} 人 · " if party.total_count is not None else "") + text


def beds_text(party: Party | None) -> str:
    if not party or not party.children:
        return ""
    parts = []
    for c in party.children:
        who = f"{c.age} 岁" if c.age is not None else "孩子"
        parts.append(who + ("占床" if c.bed else "不占床" if c.bed is False else "占床未定"))
    return "、".join(parts)


def summary(need: Need) -> str:
    """One line for the state bar: 德法意瑞 · 12月中下旬 · 2大2小 · ≤2万/人."""
    parts = []
    for field in ("destinations", "window", "party", "budget"):
        value = need.get(field)
        if value:
            parts.append(show(field, value))
    return " · ".join(parts) or "需求还很少"


# ---------------------------------------------------------------- consequences


def consequences(fields) -> list[str]:
    """The program-owned chain of effects for a set of changed fields."""
    fields = set(fields)
    out = []
    if fields & SEARCH_FIELDS:
        out.append("research")
    if fields & RANK_FIELDS:
        out.append("rerank")
    if fields & PRICE_FIELDS:
        out.append("reprice")
    return out


def spoken(need: Need) -> str:
    """The need as the advisor would say it back to the customer."""
    parts = []
    window = need.get("window")
    if window:
        parts.append((window.label or f"{window.start.month}月{window.start.day}日前后") + "出发")
    party = need.get("party")
    if party and party.adults is None and party.total_count:
        parts.append(f"共{party.total_count}人，人员构成待确认")
    if party and party.adults is not None:
        text = f"{party.adults}大"
        if party.children:
            text += f"{len(party.children)}小"
        if party.seniors:
            text += f" + {len(party.seniors)}位长辈"
        parts.append(text)
    budget = need.get("budget")
    if budget and budget.currency == "CNY":
        amount = float(budget.per_person)
        parts.append(
            f"每人{amount / 10000:g}万以内" if amount >= 10000 else f"每人{int(amount)}元以内"
        )
    if "slow_pace" in (need.get("preferences") or []):
        parts.append("别太累")
    return "、".join(parts)
