"""Customer draft sentence validation against this turn's current, reviewed facts."""

import re

INTERNAL = re.compile(r"(?<![A-Za-z0-9])(?:W[PDO]-[A-Za-z0-9-]+|[A-Z]{1,5}\d{1,5}[-－_])")
PRIVATE = re.compile(
    r"同[行业]价|结算价|结算总价|毛利|利润率?|(?:余位|剩余名额|库存)[：:\s]*[零一二三四五六七八九十\d]+|\{\s*\""
)
PROMISE = re.compile(r"(?<!不)(?<!无法)(?<!不能)(?:保证|一定|肯定|确保|包退)")
FACTUAL = re.compile(
    r"\d|[￥¥]|元|出发|包含|不含|赠送|安排|入住|全程|酒店|餐食|购物|自费|退改|退款|推荐.*线路|这条.*适合|孩子不累"
)
QUALIFIER = re.compile(
    r"(?:不能|无法|不予|不含|不保证|需[要由]?|须|仅限|自费|以.{0,20}为准|待确认|最终确认)[^，。；！？\n]{0,60}"
)


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


def supported(claim, fact):
    """Permit exact cited clauses, preserving every restrictive condition in a fact."""
    source, target = normalized(fact["text"]), normalized(claim)
    if not target or target not in source:
        return False
    return all(normalized(q) in target for q in QUALIFIER.findall(fact["text"]))


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
            violations.append("unproven")
    accepted = []
    question_count = 0
    for sentence in re.findall(r"[^。！？\n]+[。！？]?", data.get("to_customer", "")):
        evidence = [c for c in claims if c["text"] in sentence]
        covered = sentence
        for claim in evidence:
            covered = covered.replace(claim["text"], "")
        # These phrases express uncertainty, not an additional route fact.
        covered = re.sub(r"需要确认(?:出发地|出发时间|团期|是否适合孩子)", "", covered)
        reason = ""
        if sentence.endswith(("？", "?")):
            question_count += 1
            if max_questions is not None and question_count > max_questions:
                violations.append("excess_questions")
                continue
        if (
            INTERNAL.search(sentence)
            or PRIVATE.search(sentence)
            or any(term and term in sentence for term in forbidden)
        ):
            reason = "private"
        elif PROMISE.search(sentence) and not any(
            PROMISE.search(by_id[c["fact_id"]]["text"]) for c in evidence
        ):
            reason = "promise"
        elif any(by_id[c["fact_id"]].get("conflict") for c in evidence) and not all(
            term in sentence for term in ("备选", "需要确认")
        ):
            reason = "conflict"
        elif FACTUAL.search(covered) and not sentence.endswith(("？", "?")):
            reason = "unproven"
        if reason:
            violations.append(reason)
        else:
            accepted.append(sentence.strip())
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
    requirements = [f for f in by_id.values() if f["kind"] == "requirement"]
    if (
        preserve_requirements
        and requirements
        and not any(c["kind"] == "requirement" for c in claims)
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
    if re.search(r"已(?:下单|占位|付款|预订|确认|采纳|成交)", advisor):
        advisor = conclusion
    return {
        "to_advisor": advisor,
        "to_customer": customer,
        "claims": claims,
        "chips": chips[:3],
        "customer_may_ask": questions,
        "simplified": bool(violations) or fallback_used,
        "violations": violations,
    }
