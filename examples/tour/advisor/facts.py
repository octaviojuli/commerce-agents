"""A published itinerary as citable facts, a quick-look grid and the things to warn about.

The warehouse publishes two shapes (``route-kit/1`` and the older day document). Both are
read into one view. A fact is a whole unit of the source, never a clipped sentence: a
condition, an exception or an age limit stays attached to what it limits.
"""

import hashlib
import re

MEAL_NAMES = {"breakfast": "早", "lunch": "午", "dinner": "晚"}


def fact_id(scope, text):
    return hashlib.sha256(f"{scope}|{text}".encode()).hexdigest()[:16]


def _text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if "text" in value and isinstance(value["text"], str):
            return value["text"].strip()
        return "；".join(filter(None, (_text(v) for v in value.values())))
    if isinstance(value, list):
        return "；".join(filter(None, (_text(v) for v in value)))
    return str(value)


def _money(value):
    if not isinstance(value, dict) or value.get("amount") in (None, ""):
        return ""
    unit = {"person": "/人", "room": "/间", "night": "/晚"}.get(value.get("unit") or "", "")
    currency = value.get("currency") or ""
    symbol = "¥" if currency in ("CNY", "RMB", "") else currency + " "
    return f"{symbol}{value['amount']}{unit}"


OUTLET = re.compile(r"购物村|奥特莱斯|奥莱|免税店|outlet", re.I)


class Route:
    """Normalized published itinerary for one product."""

    def __init__(self, product_id, title, body, *, reviewed=True):
        self.product_id = product_id
        self.title = title
        self.body = body or {}
        self.kit = self.body.get("schema") == "route-kit/1"
        self.notice = self.body.get("publication_notice", "")
        self.reviewed = reviewed and "未人工审核" not in self.notice
        self.days = [self._day(d) for d in self.body.get("days", [])]

    # ------------------------------------------------------------ days

    def _day(self, d):
        items = []
        if self.kit:
            for n in d.get("items", []):
                detail = "；".join(
                    filter(
                        None,
                        [
                            n.get("description", ""),
                            n.get("duration_text", ""),
                            _text(n.get("alternative")),
                            _text(n.get("disclaimer")),
                            _text(n.get("extra_cost_note")),
                            n.get("price_text", ""),
                            n.get("booking_note", ""),
                        ],
                    )
                )
                transport = n.get("transport") or {}
                if transport and not detail:
                    detail = "；".join(
                        filter(
                            None, [transport.get("duration_text"), transport.get("distance_text")]
                        )
                    )
                items.append(
                    {
                        "node_id": n.get("node_id") or "",
                        "type": n.get("type") or "",
                        "name": n.get("name") or transport.get("to_place") or "",
                        "detail": detail,
                        "paid": n.get("inclusion") in ("excluded", "recommended_not_included")
                        or n.get("type") in ("optional", "recommend"),
                        "shopping": n.get("type") == "shopping",
                    }
                )
            stay = d.get("stay") or {}
            stay_text = " ".join(
                filter(
                    None,
                    [
                        "、".join(stay.get("names") or []),
                        stay.get("grade_text") or "",
                        stay.get("city") or "",
                    ],
                )
            )
            meals = {k: _text(v) for k, v in (d.get("meals") or {}).items()}
            notes = [_text(n) for n in d.get("notes", [])]
            text = d.get("summary", "")
        else:
            for i, b in enumerate(d.get("blocks", [])):
                items.append(
                    {
                        "node_id": f"d{d.get('day')}b{i}",
                        "type": b.get("type", ""),
                        "name": b.get("title", ""),
                        "detail": "；".join(b.get("paragraphs", [])),
                        "paid": b.get("ticket_status") == "excluded",
                        "shopping": b.get("type") == "shopping",
                    }
                )
            for s in d.get("sights", []):
                items.append(
                    {
                        "node_id": f"d{d.get('day')}s{s.get('name', '')}",
                        "type": s.get("kind", ""),
                        "name": s.get("name", ""),
                        "detail": s.get("duration", ""),
                        "paid": s.get("kind") == "自费",
                        "shopping": s.get("kind") == "购物",
                    }
                )
            hotel = d.get("hotel") or {}
            stay_text = " ".join(filter(None, [hotel.get("name", ""), hotel.get("grade", "")]))
            meals = {}
            for k, m in (d.get("meals") or {}).items():
                m = m or {}
                meals[k] = m.get("text") or (
                    "含" if m.get("included") else "自理" if m.get("included") is False else ""
                )
            notes = []
            text = d.get("text") or d.get("summary", "")
        return {
            "day": d.get("day"),
            "title": d.get("title", ""),
            "text": text,
            "items": items,
            "stay": stay_text,
            "meals": meals,
            "notes": [n for n in notes if n],
            "travel": d.get("travel_text") or d.get("travel_reference") or d.get("transport") or "",
        }

    # ------------------------------------------------------------ facts

    def outlets(self, facts=None):
        """Days that stop at an outlet village: not a listed shop, but the customer will ask."""
        seen, out = set(), []
        for f in facts if facts is not None else self.facts():
            if f["section"] == "行程" and OUTLET.search(f["text"]) and f["day"] not in seen:
                seen.add(f["day"])
                out.append(f)
        return out

    def facts(self):
        """Every citable unit. ``section`` says where it came from for the evidence tag."""
        out = []

        def add(text, section, day=None, node=""):
            text = (text or "").strip(" ；;")
            _, colon, body = text.partition("：")
            if len(text) < 2 or colon and len(body.strip(" ；;：")) < 2:
                # A label with nothing after it ("儿童政策：") is not a fact.
                return
            out.append(
                {
                    "fact_id": fact_id(self.product_id, text),
                    "text": text,
                    "section": section,
                    "day": day,
                    "node_id": node,
                    "product_id": self.product_id,
                    "reviewed": self.reviewed,
                }
            )

        b = self.body
        for x in b.get("cover_facts", []):
            add(_text(x), "行程概要")
        for x in b.get("selling_points", []):
            add(_text(x), "行程概要")
        for x in b.get("inclusions", []):
            add("费用包含：" + _text(x), "费用说明")
        for x in b.get("exclusions", []):
            add("费用不含：" + _text(x), "费用说明")
        for x in b.get("shopping", []):
            parts = [x.get("name", ""), x.get("duration_text") or x.get("duration") or ""]
            day = x.get("day")
            add(
                (f"第 {day} 天购物：" if day else "购物：") + "；".join(filter(None, parts)),
                "购物说明",
                day,
            )
        for x in b.get("optional_items", []) + b.get("optional", []):
            parts = [
                x.get("name", ""),
                x.get("price_text") or x.get("price") or _money(x.get("money")),
                x.get("note", ""),
            ]
            add("自费项目：" + "；".join(filter(None, parts)), "自费说明", x.get("day"))
        policies = b.get("policies") or {}
        labels = {
            "single_room": "单房差",
            "child": "儿童政策",
            "tips": "小费",
            "cancellation": "退改",
            "deposit": "定金",
            "visa": "签证",
        }
        for key, label in labels.items():
            value = policies.get(key)
            for item in value if isinstance(value, list) else [value]:
                add(f"{label}：{_text(item)}", label)
        if b.get("single_room_supplement"):
            money = _money(b["single_room_supplement"])
            if money:
                add(f"单房差：{money}", "单房差")
        req = b.get("traveler_requirements") or {}
        for key, label in (("child", "儿童"), ("senior", "老人")):
            rule = req.get(key) or {}
            text = "；".join(
                filter(
                    None,
                    [
                        (f"{rule['minimum_age']} 岁以上" if rule.get("minimum_age") else ""),
                        (f"{rule['maximum_age']} 岁以下" if rule.get("maximum_age") else ""),
                        rule.get("bed_policy", ""),
                        rule.get("conditions", ""),
                        rule.get("raw", ""),
                    ],
                )
            )
            add(f"{label}参团：{text}" if text else "", f"{label}政策")
        visa = req.get("visa") or {}
        visa_text = "；".join(
            filter(
                None,
                [
                    visa.get("submission_deadline", ""),
                    visa.get("passport_validity", ""),
                    visa.get("raw", ""),
                ],
            )
        )
        add(f"签证：{visa_text}" if visa_text else "", "签证须知")
        formation = b.get("formation") or {}
        if formation.get("minimum_travelers"):
            add(
                f"成团：{formation['minimum_travelers']} 人成团；"
                + "；".join(
                    filter(
                        None,
                        [
                            formation.get("failure_action", ""),
                            formation.get("booking_deadline", ""),
                        ],
                    )
                ),
                "成团规则",
            )
        for n in b.get("notices", []):
            for item in n.get("items", []):
                add(f"{n.get('title') or '注意事项'}：{_text(item)}", "注意事项")
        for d in self.days:
            day = d["day"]
            label = f"第 {day} 天"
            if d["title"]:
                add(f"{label}：{d['title']}", "行程", day)
            if d["text"]:
                add(f"{label}：{d['text']}", "行程", day)
            meals = "，".join(f"{MEAL_NAMES.get(k, k)}餐{v}" for k, v in d["meals"].items() if v)
            if meals:
                add(f"{label}餐食：{meals}", "餐食", day)
            if d["stay"]:
                add(f"{label}住宿：{d['stay']}", "住宿说明", day)
            for item in d["items"]:
                text = "；".join(filter(None, [item["name"], item["detail"]]))
                add(f"{label}：{text}", "行程", day, item["node_id"])
            for note in d["notes"]:
                add(f"{label}提示：{note}", "注意事项", day)
        if not b.get("shopping") and self.days:
            # Derived from the days: the document lists no shop, and where the outlets are.
            stops = self.outlets(out)
            add(
                "购物安排：行程未列购物店"
                + (
                    "；" + "、".join(f"第 {f['day']} 天" for f in stops) + "安排购物村自由活动"
                    if stops
                    else ""
                ),
                "行程概要",
            )
        seen, unique = set(), []
        for f in out:
            if f["fact_id"] not in seen:
                seen.add(f["fact_id"])
                unique.append(f)
        return unique

    # ------------------------------------------------------------ quick look

    def grid(self):
        """The eight questions advisors are asked most, answered only from the document."""
        facts = self.facts()

        def find(*patterns, section=None):
            for f in facts:
                if section and f["section"] not in section:
                    continue
                if any(re.search(p, f["text"]) for p in patterns):
                    return f
            return None

        grades = [d["stay"] for d in self.days if d["stay"]]
        meals = sum(
            1
            for d in self.days
            for v in d["meals"].values()
            if v and not re.search(r"自理|敬请自理|不含|自费", v)
        )
        shopping = [f for f in facts if f["section"] == "购物说明"]
        outlets = self.outlets(facts)
        optional = [f for f in facts if f["section"] == "自费说明"]
        child = find(r"儿童|小孩|占床", section=("儿童政策", "儿童政策"))
        single = find(r"单房差", section=("单房差",))
        visa = find(r"签证", section=("签证须知", "签证"))
        group = find(r"成团", section=("成团规则",))
        return [
            {"label": "酒店", "value": _grade(grades), "fact": None},
            {"label": "含餐", "value": f"{meals} 餐" if meals else "未写明", "fact": None},
            {
                "label": "购物",
                "value": f"{len(shopping)} 店"
                if shopping
                else f"未列购物店 · 含 {len(outlets)} 次购物村"
                if outlets
                else ("未列购物店" if self.days else "未写明"),
                "fact": shopping[0]["fact_id"]
                if shopping
                else outlets[0]["fact_id"]
                if outlets
                else None,
            },
            {
                "label": "自费",
                "value": f"{len(optional)} 项 · 自愿" if optional else "未列自费",
                "fact": optional[0]["fact_id"] if optional else None,
            },
            {"label": "儿童", "value": _after(child), "fact": child and child["fact_id"]},
            {"label": "单房差", "value": _after(single), "fact": single and single["fact_id"]},
            {"label": "签证", "value": _after(visa), "fact": visa and visa["fact_id"]},
            {"label": "成团", "value": _after(group), "fact": group and group["fact_id"]},
        ]

    def watch(self):
        """What to tell the customer before they commit: conditions, extra payments, changes."""
        out = []
        for f in self.facts():
            if (
                f["section"] in ("注意事项",)
                or re.search(
                    r"另付|现场支付|以.{0,20}为准|如遇|可能|改为外观|不保证|不能保证|闭馆|取消",
                    f["text"],
                )
            ) and (f["section"] != "费用说明" or "不含" in f["text"]):
                out.append(f)
        return out[:8]

    def outline(self):
        return [
            {
                "day": d["day"],
                "title": d["title"] or (d["items"][0]["name"] if d["items"] else ""),
                "detail": d["stay"] or d["travel"] or "",
                "items": d["items"],
                "meals": d["meals"],
                "stay": d["stay"],
            }
            for d in self.days
        ]


def _after(fact):
    if not fact:
        return "未写明"
    text = fact["text"].split("：", 1)[-1]
    return text[:26] + ("…" if len(text) > 26 else "")


def _grade(grades):
    if not grades:
        return "未写明"
    stars = re.findall(r"([2-5一二三四五])\s*(?:星|钻)", " ".join(grades))
    if stars:
        values = sorted(set(stars))
        return "、".join(values) + " 星"
    return grades[0][:20]
