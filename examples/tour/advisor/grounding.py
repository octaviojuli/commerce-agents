"""Check a customer draft sentence by sentence against this turn's facts.

A factual clause stays only when one fact supports it: its numbers come from that fact,
it adds no negation or limit the fact does not have, and when it restates a clause of the
fact it keeps that clause's limits and quantities. A sentence that fails is removed whole.
"""

import re

INTERNAL = re.compile(r"(?<![A-Za-z0-9])(?:W[PDO]-[A-Za-z0-9-]+|[A-Z]{1,5}\d{1,5}[-－_])")
PRIVATE = re.compile(
    r"同[行业]价|结算价|毛利|利润|(?:余位|剩余名额|库存)[：:\s]*[\d一二三四五六七八九十]+|\{\s*\""
)
# Saying a customer's personal numbers were taken down: they are masked and never kept.
# A request to send documents separately ("之后单独发我，方便登记") is what a reply should say.
KEPT_PERSONAL = re.compile(
    r"(?:手机|电话|护照|证件|身份证)[号码信息]*.{0,8}"
    r"(?:(?:已经|已|都|也)(?:帮您|给您)?(?:登记|留|记|保存|存)|登记好|留好|记下了|记好了|保存好|存好)"
)
# Guarantees, and holds or bookings: the advisor app neither holds seats nor books in phase one.
PROMISE = re.compile(
    r"(?<!不)(?<!无法)(?<!不能)(?:保证|一定|肯定|确保|包退|绝对|百分百"
    r"|锁定|锁位|占位|留位|保留名额|帮您订|给您订|订好了|已预订)"
    r"|(?:马上|稍后|随后|确认后|核实后).{0,6}(?:回复|回您|回你)|已(?:经)?联系.{0,6}供应商"
)
# Clauses that state something checkable. Wishes, questions and connectives do not.
FACTUAL = re.compile(
    r"\d|[￥¥]|(?<!旦)元(?!旦)|包含|不含|含[早午晚三]?餐|赠送|免费|入住|[1-5一二三四五]星|购物|自费|另付|退改|退款|"
    r"手续费|签证|保险|占床|自理|强制|自愿|天气|气温|雨季|旱季|季节|已(?:经)?(?:安排|订好|预订|预留|确认)"
    r"|(?:价格|价钱|费用|团费).{0,6}(?:差|贵|便宜|高|低|涨|降)|(?:含|包)在.{0,4}(?:团费|费用|价格|价钱)"
)
# "资料没写明，我去跟供应商确认": saying what is not known is not a claim.
# Restating the customer's question ("您问的有没有购物店") is not a claim either.
VERIFY = re.compile(
    r"没写明|未写明|没有写|没写|不确定|待(?:核实|确认)|(?:还?没|未)(?:完全)?(?:确认|核实)"
    r"|正在.{0,8}(?:核对|确认|核实)|(?:去|再|帮您|会|跟供应商|向供应商)(?:确认|核实|问)|问清|一并.{0,4}问"
    r"|您问的|有没有|是否|要不要|能不能"
)
# "自费项目、含餐和签证这三项": the list a reply names while saying it is still being checked.
TOPIC_SPAN = re.compile(r"[^，。；：:]*?(?:这|那)[两三四五六几\d]+(?:项|点|件事|个问题)")
# "自费项目、含餐和签证这三项": naming what was asked, not saying what it is.
TOPIC_LIST = re.compile(r"(?:这|那)?[两三四五六几\d]+(?:项|点|件事|个问题)$|(?:这些|这几个)?问题$")
RECAP_PREFIX = re.compile(
    r"^(?:您|你|客人|客户)(?:希望|想要?|要求|计划|打算|的(?:需求|要求|偏好)(?:是|为)?)"
    r"|^按(?:您|你|客人|客户)的(?:需求|要求|偏好)"
)
RECAP_END = re.compile(r"(?:这些|以上)(?:需求|要求|偏好)?我都?记(?:下|好)了[。\s]*$")
SERVICE_CLAIM = re.compile(
    r"^(?:这条|该|本)?(?:线(?:路)?|行程|团|全程|签证|费用)|已(?:经)?(?:含|包|安排)"
)


# "按3位成人来安排": a head count by kind, which the reply may say only when it is known.
HEADS = r"[\d零一二两三四五六七八九十]+"
PARTY_COUNT = re.compile(
    rf"({HEADS})\s*(?:位|个|名)?\s*(成人|大人|儿童|小孩|孩子|小朋友|老人|长辈)|({HEADS})大({HEADS})小"
)
PARTY_KINDS = {
    "成人": "adults",
    "大人": "adults",
    "儿童": "children",
    "小孩": "children",
    "孩子": "children",
    "小朋友": "children",
    "老人": "seniors",
    "长辈": "seniors",
}


def party_counts(text: str) -> set:
    """Each head count by kind the text states, as ("adults", 3): "三名成人", "二大一小".

    "其中一个孩子" and "每一位成人" name part of the party, not its count.
    """
    text = text or ""
    found = set()
    for m in PARTY_COUNT.finditer(text):
        if re.search(r"(?:其中|每)$", text[: m.start()]):
            continue
        if m[1]:
            found.add((PARTY_KINDS[m[2]], heads(m[1])))
        else:
            found |= {("adults", heads(m[3])), ("children", heads(m[4]))}
    return found


def heads(value: str) -> int:
    if value.isdigit():
        return int(value)
    if "十" in value:
        tens, _, ones = value.partition("十")
        return CN_DIGITS.get(tens, 1) * 10 + CN_DIGITS.get(ones, 0)
    return CN_DIGITS.get(value, 0)


def known_counts(party) -> set:
    """The head counts a saved party settles: "三个客人" settles none of them."""
    if party is None:
        return set()
    found = set()
    if party.adults is not None:
        found |= {("adults", party.adults), ("seniors", len(party.seniors))}
    elif party.seniors:
        found.add(("seniors", len(party.seniors)))
    if party.children is not None:
        found.add(("children", len(party.children)))
    return found


def matches_said(clause: str, stated: str) -> bool:
    """Whether words match customer input; this is never supplier evidence."""
    if not stated:
        return False
    words = [m.group(0) for m in FACTUAL.finditer(clause) if not m.group(0).isdigit()]
    own = bigrams(clause)
    return (
        bool(own)
        # A recap reuses the customer's own wording; "行程里没有购物店" is not "不想进购物店".
        and len(own & bigrams(stated)) >= 0.6 * len(own)
        and all(w in stated for w in words)
        and numbers(clause) <= numbers(stated)
        and set(polar(clause)) <= set(polar(stated))
    )


def restates(clause: str, stated: str, *, recap=False) -> bool:
    """A matching requirement must still be attributed to the customer."""
    prefix = RECAP_PREFIX.match(clause)
    if not prefix and (not recap or SERVICE_CLAIM.search(clause)):
        return False
    return matches_said(clause[prefix.end() :] if prefix else clause, stated)


# Negations and limits, longest first so "无须" is never read as "须".
POLAR = (
    "无须",
    "无需",
    "不需要",
    "不需",
    "不用",
    "不必",
    "不包含",
    "不包括",
    "不含",
    "不占",
    "不能",
    "无法",
    "不保证",
    "不可",
    "不退",
    "免费",
    "自理",
    "自费",
    "另付",
    "现付",
    "须",
    "需要",
    "需",
    "仅限",
    "仅",
    "限",
    "为准",
    "提前",
)
LIMITS = {
    "须",
    "需要",
    "需",
    "自理",
    "自费",
    "另付",
    "现付",
    "不含",
    "不包含",
    "不包括",
    "仅",
    "仅限",
    "限",
    "为准",
    "提前",
    "不能",
    "无法",
    "不保证",
    "不可",
    "不退",
    "不占",
    "免费",
    "无须",
    "无需",
    "不需要",
    "不需",
    "不用",
    "不必",
}
SCOPE = {"仅限", "仅", "限"}
SYNONYMS = (
    ("包含", "含"),
    ("已含", "含"),
    ("在内", ""),
    ("大人", "成人"),
    ("小朋友", "儿童"),
    ("小孩", "儿童"),
    ("孩子", "儿童"),
    ("位", "个"),
    ("需要", "需"),
)
CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
KNOWN_QUESTIONS = {
    "party": r"几位|几个人|多少人|几口人|几个大人|几个儿童|几个孩子|人数",
    "child_ages": r"(?:孩子|小孩|儿童|宝宝).{0,6}(?:几岁|多大|年龄)",
    "window": r"什么时候(?:出发|走|去|出行)|打算几月|哪个月出发",
    "days": r"玩几天|几天行程|多少天",
    "depart_city": r"哪里出发|从哪.{0,3}出发|出发城市",
    "destinations": r"想去哪|去哪里玩|目的地是",
    "budget": r"预算(?:多少|大概|是多少)",
    "child_beds": r"占床|不占床",
    "rooms": r"几间房|住几间|房间怎么(?:安排|住)|单住",
}
REQUEST = re.compile(r"告诉我|说一下|说下|发我|确认一下|方便.{0,6}(?:说|告诉|提供)")


def chinese_numbers(value: str) -> str:
    """Write Chinese numerals and 万 amounts as digits: 两天 → 2天, 1.5万 → 15000."""

    def wan(m):
        number = float(m.group(1)) * 10000
        return str(int(number)) if number.is_integer() else str(number)

    value = re.sub(r"(\d+(?:\.\d+)?)\s*万", wan, value)

    def cn(m):
        text = m.group(0)
        total, current = 0, 0
        for ch in text:
            if ch in CN_DIGITS:
                current = CN_DIGITS[ch]
            elif ch == "十":
                total += (current or 1) * 10
                current = 0
            elif ch == "百":
                total += (current or 1) * 100
                current = 0
            elif ch == "千":
                total += (current or 1) * 1000
                current = 0
        return str(total + current)

    return re.sub(
        r"[零一二两三四五六七八九十百千]+(?=[天晚岁人位间个次元小时分钟点号月日周星钻顿餐项店])",
        cn,
        value,
    )


def normalized(value: str) -> str:
    value = chinese_numbers(value)
    value = re.sub(r"[\s，。；：、！？,.!?;:（）()【】\[\]“”\"'·～~—\-]", "", value)
    for word, replacement in SYNONYMS:
        value = value.replace(word, replacement)
    return value


def numbers(value: str) -> set:
    found = set()
    for item in re.findall(r"\d[\d,]*(?:\.\d+)?", chinese_numbers(value)):
        item = item.replace(",", "")
        found.add(re.sub(r"\.0+$", "", item))
    return found


def bigrams(value: str) -> set:
    value = normalized(value)
    return {value[i : i + 2] for i in range(len(value) - 1)}


def polar(value: str) -> list:
    """Negation and limit words in reading order, longest match first."""
    out, i = [], 0
    while i < len(value):
        for token in POLAR:
            if value.startswith(token, i):
                out.append(token)
                i += len(token)
                break
        else:
            i += 1
    return out


def clauses(value: str) -> list:
    return [c for c in re.split(r"[，,；;。！？!?\n]", value) if c.strip()]


def core(claim: str, text: str) -> bool:
    """Each factual phrase of the claim appears in the fact together with its neighbours."""
    claim_n, text_n = normalized(claim), normalized(text)
    spans = [m for m in FACTUAL.finditer(claim_n) if not m.group(0).isdigit()]
    if not spans:
        return True
    for match in spans:
        start, end = match.span()
        windows = [
            claim_n[i:j]
            for i in range(max(0, start - 2), start + 1)
            for j in range(end, min(len(claim_n), end + 2) + 1)
            if j - i >= 3
        ]
        if not any(w in text_n for w in windows):
            return False
    return True


def supported(claim: str, fact: dict) -> bool:
    text = fact["text"]
    words = bigrams(claim)
    if not words:
        return False
    target, source = normalized(claim), normalized(text)
    if (
        target not in source
        and len(words & bigrams(text)) < 0.5 * len(words)
        and not core(claim, text)
    ):
        return False
    if target not in source and not core(claim, text):
        return False
    if not numbers(claim) <= numbers(text):
        return False
    # A scope limit ("仅限6岁以下儿童") governs the whole unit, not only its own clause.
    scope = [t for t in polar(text) if t in SCOPE]
    if scope and not any(t in polar(claim) for t in scope):
        return False
    related = [c for c in clauses(text) if words & bigrams(c)]
    fact_polar = set(polar("".join(related) or text))
    for token in polar(claim):
        # A claim may not add a negation or a limit the fact does not state.
        if token not in fact_polar and not (token == "需" and "需要" in fact_polar):
            return False
    for clause in related:
        shared = words & bigrams(clause)
        if not shared:
            continue
        if numbers(claim) and not numbers(claim) & numbers(clause) and numbers(clause):
            # A sibling clause about another number ("8岁的占床" beside "5岁不占床").
            continue
        need = [t for t in polar(clause) if t in LIMITS]
        have = set(polar(claim))
        if any(t not in have and not (t == "需要" and "需" in have) for t in need):
            return False
        if numbers(clause) - numbers(claim):
            return False
    return True


def _support(clause, facts):
    return next((f for f in facts if supported(clause, f)), None)


def asks_known(sentence: str, known: set):
    return next(
        (
            f
            for f, pattern in KNOWN_QUESTIONS.items()
            if f in known and re.search(pattern, sentence)
        ),
        None,
    )


def check(
    text: str,
    facts: list,
    *,
    said: list = (),
    known=(),
    forbidden=(),
    conflicts=(),
    max_questions=None,
    counts=None,
):
    """Return the kept draft, the claims found for each kept factual clause, and what was cut.

    ``said`` is the customer's own words: they may back a restated number, never a fact.
    ``conflicts`` are route names that do not fit the need; they may only appear as an
    alternative that still needs confirming. ``counts`` are the head counts the saved party
    settles (``known_counts``); a reply states no other unless the customer or a fact says it.
    """
    # A route with no reviewed document is cited from what it has; each claim says which.
    checked = {f.get("product_id") for f in facts if f.get("reviewed", True)}
    reviewed = [f for f in facts if f.get("reviewed", True) or f.get("product_id") not in checked]
    customer = [
        f
        for f in reviewed
        if f.get("section") == "客人原话"
        or str(f.get("fact_id", "")).startswith(("need:preferences:", "need:themes:"))
    ]
    reviewed = [f for f in reviewed if f not in customer]
    said = [*said, *(f["text"] for f in customer)]
    said_numbers = set().union(*(numbers(s) for s in said)) if said else set()
    # What the customer stated, without their questions: "含早餐吗" backs nothing.
    stated = "".join(
        part for s in said for part in re.split(r"[^。！？\n]*[？?吗][。！？\n]?", s) if part
    )
    known = set(known)
    if counts is not None:
        counts = set(counts).union(
            *(party_counts(s) for s in said), *(party_counts(f["text"]) for f in reviewed)
        )
    kept, claims, removed, reasons, decisions = [], [], [], [], []
    questions = 0
    for sentence in re.findall(r"[^。！？\n]+[。！？]?", text or ""):
        stripped = sentence.strip()
        if not stripped:
            continue
        question = stripped.endswith(("？", "?"))
        reason = ""
        found = []
        if question:
            questions += 1
            if max_questions is not None and questions > max_questions:
                reason = "too_many_questions"
        if not reason and (question or REQUEST.search(stripped)) and asks_known(stripped, known):
            reason = "asks_known"
        if not reason and (
            INTERNAL.search(stripped)
            or PRIVATE.search(stripped)
            or any(t and t in stripped for t in forbidden)
        ):
            reason = "private"
        if not reason and not question:
            body = re.sub(r"^\s*(?:\d{1,2}|[①②③④⑤⑥⑦⑧⑨])\s*[)）.、]?\s*", "", stripped)
            if VERIFY.search(body):
                # "自费、含餐和签证这三项，资料没写明": the named list is what is open, not a claim;
                # every other clause in the sentence is still checked.
                body = TOPIC_SPAN.sub("所问事项", body)
            parts = [c.strip() for c in clauses(body)]
            k = 0
            while k < len(parts):
                clause = parts[k]
                k += 1
                if not FACTUAL.search(clause):
                    continue
                if (
                    VERIFY.search(clause)
                    or TOPIC_LIST.search(clause)
                    or restates(clause, stated, recap=bool(RECAP_END.search(stripped)))
                ) and not PROMISE.search(clause):
                    allowed = set().union(*(numbers(f["text"]) for f in reviewed)) | said_numbers
                    if numbers(clause) <= allowed:
                        found.append({"text": clause, "fact_id": None})
                        continue
                fact = _support(clause, reviewed)
                if fact is None and k < len(parts):
                    # "如果孩子不占床，每位11800元": a condition and its number span two clauses.
                    joined = clause + "，" + parts[k]
                    fact = _support(joined, reviewed)
                    if fact is not None:
                        found.append(
                            {
                                "text": joined,
                                "fact_id": fact["fact_id"],
                                "section": fact.get("section", ""),
                                "reviewed": fact.get("reviewed", True),
                            }
                        )
                        k += 1
                        continue
                if fact is None and not FACTUAL.search(re.sub(r"\d", "", chinese_numbers(clause))):
                    allowed = (
                        set().union(*(numbers(f["text"]) for f in reviewed)) | said_numbers
                        if reviewed
                        else said_numbers
                    )
                    if numbers(clause) <= allowed:
                        found.append({"text": clause, "fact_id": None})
                        continue
                if fact is None:
                    # Customer wishes and hearsay cannot become supplier facts, even if a
                    # later judge mistakes the overlapping words for supporting evidence.
                    reason = "customer_claim" if matches_said(clause, stated) else "unproven"
                    break
                found.append(
                    {
                        "text": clause,
                        "fact_id": fact["fact_id"],
                        "section": fact.get("section", ""),
                        "reviewed": fact.get("reviewed", True),
                    }
                )
        # Hard rules are recorded whatever failed first, so no later reading can lift them.
        hard = reason if reason in HARD else ""
        if PROMISE.search(stripped):
            backed = [f for f in reviewed if any(c["fact_id"] == f["fact_id"] for c in found)]
            if not any(PROMISE.search(f["text"]) for f in backed):
                hard = hard or "promise"
        if any(name in stripped for name in conflicts) and not (
            "备选" in stripped and "确认" in stripped
        ):
            hard = hard or "conflict"
        if (
            counts is not None
            and not question
            # Each clause: "酒店需要再核实" does not make "按3位成人来安排" known.
            and any(
                not party_counts(c) <= counts for c in clauses(stripped) if not VERIFY.search(c)
            )
        ):
            hard = hard or "party"
        if (
            INTERNAL.search(stripped)
            or PRIVATE.search(stripped)
            or KEPT_PERSONAL.search(stripped)
            or any(t and t in stripped for t in forbidden)
        ):
            hard = "private"
        reason = reason if reason in HARD or not hard else hard
        decisions.append(
            {
                "text": stripped,
                "reason": reason,
                "hard": hard,
                "claims": [c for c in found if c["fact_id"]],
            }
        )
        if reason:
            removed.append(stripped)
            reasons.append(reason)
        else:
            kept.append(stripped)
            claims.extend(c for c in found if c["fact_id"])
    return {
        "text": "\n".join(kept),
        "claims": claims,
        "removed": removed,
        "reasons": reasons,
        "sentences": len(kept) + len(removed),
        "decisions": decisions,
    }


# Reasons the program keeps whatever the judge says: they are rules, not readings.
HARD = {
    "private",
    "promise",
    "asks_known",
    "too_many_questions",
    "conflict",
    "customer_claim",
    "party",
}
# The judge overturns the rules only when sure enough: "safe" at TRUST, "unsafe" at DOUBT.
# Measured on live replies, an "unsafe" below DOUBT is a coin toss (a correct route list at 0.02).
TRUST, DOUBT = 0.5, 0.3


def judged(result: dict, verdicts: list, facts: list, said=()) -> dict:
    """The check's result with the judge deciding what the rules could only guess at.

    The judge never keeps a sentence the rules cut for a hard reason, nor one whose numbers
    appear in no fact and nothing the customer said.
    """
    allowed = set().union(*(numbers(f["text"]) for f in facts)) if facts else set()
    allowed |= set().union(*(numbers(s) for s in said)) if said else set()
    kept, claims, removed, reasons, decisions = [], [], [], [], []
    padded = (list(verdicts) + [None] * len(result["decisions"]))[: len(result["decisions"])]
    for d, v in zip(result["decisions"], padded, strict=True):
        reason, how = d["reason"], "rules"
        if not d.get("hard") and v:
            choice, confidence = v
            spoken = numbers(TOPIC_SPAN.sub("", d["text"]))
            if choice == "unsafe" and confidence >= DOUBT:
                reason, how = "judged_unsafe", "judge"
            elif confidence >= TRUST and spoken <= allowed:
                reason, how = "", "judge"
        decisions.append({**d, "reason": reason, "by": how})
        if reason:
            removed.append(d["text"])
            reasons.append(reason)
        else:
            kept.append(d["text"])
            claims.extend(d["claims"])
    return {
        **result,
        "text": "\n".join(kept),
        "claims": claims,
        "removed": removed,
        "reasons": reasons,
        "decisions": decisions,
    }
