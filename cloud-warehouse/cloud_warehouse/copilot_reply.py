"""Customer draft sentence validation against this turn's current, reviewed facts."""

import re

INTERNAL = re.compile(r"(?<![A-Za-z0-9])(?:W[PDO]-[A-Za-z0-9-]+|[A-Z]{1,5}\d{1,5}[-－_])")
PRIVATE = re.compile(
    r"同[行业]价|结算价|结算总价|毛利|利润率?|(?:余位|剩余名额|库存)[：:\s]*[零一二三四五六七八九十\d]+|\{\s*\""
)
PROMISE = re.compile(r"(?<!不)(?<!无法)(?<!不能)(?:保证|一定|肯定|确保|包退)")
# Only concrete, checkable statements need a fact: numbers, money, inclusions,
# hotels, shopping, refunds and completed arrangements. Connecting phrases,
# restated wishes and questions do not.
FACTUAL = re.compile(
    r"\d|[￥¥]|元|包含|不含|含[早午晚]?餐|赠送|免费|入住|[1-5一二三四五]星|购物|自费|退改|退款"
    r"|签证|保险|已(?:经)?(?:安排|确认|订好|预订|预留)"
)
# Limits a claim may not drop when it restates the clause that carries them.
LIMIT = re.compile(
    r"不含|不包含|不占|不保证|不能|无法|自理|自费|另付|需[要由]?|须|仅限|为准|待确认"
)
# The customer's priorities already addressed in the reply's own words.
CONCERN_WORDS = re.compile(r"慢|累|赶|轻松|孩子|小朋友|亲子|宝宝")
LIST_MARKER = re.compile(r"^\s*(?:\d{1,2}|[一二三四五六七八九十])\s*[)）.、:：]\s*")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# A customer question about a requirement already on file.
KNOWN_QUESTIONS = {
    "party": r"几位|几个人|多少人|几口人|几个大人|几个小孩|几个孩子|人数",
    "child_ages": r"(?:孩子|小孩|儿童|宝宝).{0,6}(?:几岁|多大|年龄)|几岁",
    "window": r"什么时候(?:出发|走|去|出行)|打算几月|哪个月",
    "days": r"玩几天|几天|多少天",
    "depart_city": r"哪里出发|从哪|出发城市|出发地",
    "destinations": r"想去哪|去哪|目的地|哪个国家",
    "budget": r"预算",
    "rooms": r"几间房|房型|占床",
}


def display_name(value):
    return re.sub(
        r"^\s*(?:W[PDO]-[a-fA-F0-9-]{8,}|[A-Z]{1,5}\d{1,5})\s*[-－_：:]*\s*", "", value or ""
    ).strip()


def plain(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "；".join(filter(None, (plain(item) for item in value)))
    if isinstance(value, dict):
        labels = {
            "name": "",
            "title": "",
            "text": "",
            "description": "",
            "paragraphs": "",
            "amount": "金额",
            "currency": "币种",
            "duration": "时长",
            "conditions": "条件",
            "fee": "费用",
            "included": "包含",
            "label": "",
            "percentage": "比例",
            "days_before": "出发前天数",
        }
        return "；".join(
            f"{labels[k]}{('：' if labels[k] else '')}{plain(v)}"
            for k, v in value.items()
            if k in labels and v not in (None, "", [], {})
        )
    return ""


def normalized(value):
    return re.sub(r"[\s，。；：、！？,.!?;:（）()【】\[\]“”\"'·]", "", value)


def numbers(value):
    found = set()
    for item in NUMBER.findall(value):
        item = item.replace(",", "")
        found.add(item.split(".")[0] if re.fullmatch(r"\d+\.0+", item) else item)
    return found


SYNONYMS = (
    ("包含", "含"),
    ("已含", "含"),
    ("在内", ""),
    ("都", ""),
    ("均", ""),
    ("费用", ""),
    ("大人", "成人"),
    ("小朋友", "儿童"),
    ("小孩", "儿童"),
    ("孩子", "儿童"),
    ("位", "个"),
)


def bigrams(value):
    value = canonical(value)
    return {value[i : i + 2] for i in range(len(value) - 1)}


def canonical(value):
    value = normalized(value)
    for word, replacement in SYNONYMS:
        value = value.replace(word, replacement)
    return value


def core(claim, fact_text):
    """Each factual phrase of a claim, with its neighbours, appears in the fact."""
    claim, fact_text = canonical(claim), canonical(fact_text)
    spans = list(FACTUAL.finditer(claim))
    if not spans:
        return False
    for match in spans:
        start, end = match.span()
        windows = [
            claim[i:j]
            for i in range(max(0, start - 2), start + 1)
            for j in range(end, min(len(claim), end + 2) + 1)
            if j - i >= 3
        ]
        if not any(w in fact_text for w in windows):
            return False
    return True


def supported(claim, fact):
    """A claim may reword a fact but cannot add numbers or drop the fact's limits."""
    target = normalized(claim)
    if not target:
        return False
    if target not in normalized(fact["text"]):
        words = bigrams(claim)
        if (not words or len(words & bigrams(fact["text"])) < 0.5 * len(words)) and not core(
            claim, fact["text"]
        ):
            return False
    if not numbers(claim) <= numbers(fact["text"]):
        return False
    words = bigrams(claim)
    for clause in re.split(r"[，,；;。！？\n]", fact["text"]):
        limits = LIMIT.findall(clause)
        shared = words & bigrams(clause)
        # Restating a limited clause must keep one of its limits.
        if limits and shared and not any(x in claim for x in limits):
            return False
    return True


def asks_known(sentence, known):
    return next(
        (
            name
            for name, pattern in KNOWN_QUESTIONS.items()
            if name in known and re.search(pattern, sentence)
        ),
        None,
    )


def validate(
    draft,
    facts,
    allowed,
    *,
    fallback,
    conclusion,
    forbidden=(),
    metrics=None,
    max_questions=None,
    preserve_requirements=False,
    known=(),
):
    if draft is None:
        return {
            "to_advisor": conclusion,
            "to_customer": fallback[:400],
            "claims": [],
            "chips": [],
            "customer_may_ask": [],
            "simplified": True,
            "violations": [],
            "validation": {"sentences": 0, "dropped": 0},
        }
    data = draft.model_dump(mode="json") if hasattr(draft, "model_dump") else draft
    by_id = {f["fact_id"]: f for f in facts if f.get("reviewed", True)}
    claims = []
    violations = []
    for claim in data.get("claims", []):
        fact = by_id.get(claim.get("fact_id"))
        if (
            fact
            and claim.get("text") in data.get("to_customer", "")
            and supported(claim["text"], fact)
        ):
            claims.append({**claim, "kind": fact["kind"], "scope": fact.get("scope", {})})
        else:
            # A bad citation alone removes nothing; the sentence is checked below.
            violations.append("claim_rejected")
    accepted, removed = [], []
    question_count = 0
    sentences = re.findall(r"[^。！？\n]+[。！？]?", data.get("to_customer", ""))
    # A number stated anywhere in this turn's facts, e.g. an age, a day count or a price.
    stated = set().union(*(numbers(f["text"]) for f in by_id.values())) if by_id else set()
    # Route names of candidates that conflict with the need, e.g. another departure city.
    conflicting = [
        f["text"].split("，")[0]
        for f in by_id.values()
        if f.get("conflict") and len(f["text"].split("，")[0]) >= 4
    ]
    for sentence in sentences:
        evidence = [c for c in claims if c["text"] in sentence]
        covered = sentence
        for claim in evidence:
            covered = covered.replace(claim["text"], "")
        # These phrases express uncertainty, not an additional route fact.
        covered = re.sub(r"需要确认(?:出发地|出发时间|团期|是否适合孩子)", "", covered)
        reason = ""
        question = sentence.rstrip().endswith(("？", "?"))
        if question:
            question_count += 1
            if max_questions is not None and question_count > max_questions:
                violations.append("excess_questions")
                removed.append(sentence.strip())
                continue
            if asks_known(sentence, known):
                # The requirement is on file; asking again reads as not listening.
                violations.append("asks_known")
                removed.append(sentence.strip())
                continue
        found = []
        if not question:
            for clause in re.split(r"[，,；;：:]", LIST_MARKER.sub("", covered)):
                clause = LIST_MARKER.sub("", clause).strip(" 。！？")
                if not FACTUAL.search(clause):
                    continue
                # The customer's own words back numbers only, never an inclusion or a price.
                fact = next(
                    (
                        f
                        for f in by_id.values()
                        if f.get("scope", {}).get("field") != "said" and supported(clause, f)
                    ),
                    None,
                )
                if fact is None and not FACTUAL.search(re.sub(r"\d", "", clause)):
                    # Only numbers make this clause factual; each must be stated in a fact.
                    wanted = numbers(clause)
                    if wanted <= stated:
                        fact = max(by_id.values(), key=lambda f: len(wanted & numbers(f["text"])))
                if fact is None:
                    reason = "unproven"
                    break
                found.append(
                    {
                        "text": clause,
                        "fact_id": fact["fact_id"],
                        "kind": fact["kind"],
                        "scope": fact.get("scope", {}),
                    }
                )
        cited = evidence + found
        if (
            INTERNAL.search(sentence)
            or PRIVATE.search(sentence)
            or any(term and term in sentence for term in forbidden)
        ):
            reason = "private"
        elif PROMISE.search(sentence) and not any(
            PROMISE.search(by_id[c["fact_id"]]["text"]) for c in cited
        ):
            reason = "promise"
        elif (
            any(by_id[c["fact_id"]].get("conflict") for c in cited)
            or any(name in sentence for name in conflicting)
        ) and not all(term in sentence for term in ("备选", "需要确认")):
            reason = "conflict"
        if reason:
            violations.append(reason)
            removed.append(sentence.strip())
        else:
            accepted.append(sentence.strip())
            claims.extend(found)
    customer = "\n".join(accepted).strip()
    if len(customer) > 400:
        violations.append("length")
        customer = ""
    fallback_used = len(customer) < 8
    if fallback_used:
        customer = fallback[:400]
    claims = [c for c in claims if c["text"] in customer]
    # Validation must not erase the customer's priorities while removing an
    # unsupported promise. Restore only the exact current requirement evidence.
    requirements = [
        f
        for f in by_id.values()
        if f["kind"] == "requirement" and f.get("scope", {}).get("field") in (None, "concern")
    ]
    if (
        preserve_requirements
        and requirements
        and not any(c["kind"] == "requirement" for c in claims)
        and not CONCERN_WORDS.search(customer)
    ):
        retained = requirements[:2]
        prefix = "。".join(f["text"] for f in retained) + "。"
        customer = prefix + "\n" + customer
        if len(customer) > 400:
            customer = prefix + "\n" + fallback[: 398 - len(prefix)]
        claims = [c for c in claims if c["text"] in customer] + [
            {
                "text": f["text"],
                "fact_id": f["fact_id"],
                "kind": f["kind"],
                "scope": f.get("scope", {}),
            }
            for f in retained
        ]
        violations.append("lost_requirement")
    questions = [
        q
        for q in data.get("customer_may_ask", [])
        if 1 <= len(q) <= 80
        and not INTERNAL.search(q)
        and not PRIVATE.search(q)
        and not any(t and t in q for t in forbidden)
    ][:3]
    chips = []
    for chip in data.get("chips", []):
        action = chip.get("action", "")
        if action in allowed or action.startswith("ask:") and action[4:] in questions:
            if not INTERNAL.search(chip.get("label", "")) and not PRIVATE.search(
                chip.get("label", "")
            ):
                chips.append(chip)
        else:
            violations.append("invalid_action")
    if metrics:
        for reason in violations:
            metrics.observe_reply(reason)
    advisor = data.get("to_advisor", "").strip()[:80] or conclusion
    if re.search(r"已(?:下单|占位|付款|预订|确认|采纳|成交)", advisor) or not numbers(
        advisor
    ) <= stated | numbers(conclusion):
        # The advisor note may not carry a number no fact states.
        advisor = conclusion
    return {
        "to_advisor": advisor,
        "to_customer": customer,
        "claims": claims,
        "chips": chips[:3],
        "customer_may_ask": questions,
        "simplified": fallback_used
        or len(accepted) < len(sentences)
        or "lost_requirement" in violations,
        "violations": violations,
        "validation": {
            "sentences": len(sentences),
            "dropped": len(sentences) - len(accepted),
            "removed": removed[:6],
        },
    }
