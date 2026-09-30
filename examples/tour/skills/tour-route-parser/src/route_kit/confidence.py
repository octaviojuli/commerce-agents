"""A route-level triage score and the reasons a person should look at a route first.

Built only from what the parser already knows about itself (days, evidence coverage, missing
sections, issues). It separates broken results from usable ones; it does not rank usable
results by accuracy, and it is not shown to customers.
"""

from __future__ import annotations

from .models import RouteContent

SEVERE = {"DAY_EMPTY_RESULT", "DAY_MODEL_FAILED", "ROUTE_MODEL_FAILED", "DAY_MODEL_INVALID"}
REMOVED = {"NAME_NOT_IN_SOURCE", "NUMBER_NOT_IN_SOURCE", "TEXT_NOT_IN_SOURCE", "TYPE_UNSUPPORTED"}
SOFT = {"ATTRIBUTE_UNSUPPORTED", "TYPE_CHECK", "EXTENSION_UNSUPPORTED"}
WEIGHTS = {
    "days": 0.30,
    "evidence": 0.25,
    "fees": 0.15,
    "issues": 0.15,
    "risk": 0.10,
    "image": 0.05,
}
REVIEW_BELOW = 70


def included_meals(content: RouteContent) -> tuple[int, int]:
    """(breakfasts, main meals) the day-by-day programme includes."""
    breakfast = sum(d.meals.breakfast.status == "included" for d in content.days)
    main = sum(
        m.status == "included" for d in content.days for m in (d.meals.lunch, d.meals.dinner)
    )
    return breakfast, main


def assess(content: RouteContent) -> tuple[float, list[str]]:
    q = content.quality
    found, extracted, expected = q.days_found, q.days_extracted, q.days_expected
    reasons: list[str] = []
    days = extracted / max(found, expected or 0, 1)
    if not found:
        reasons.append("没有识别出逐日行程")
    elif expected and found != expected:
        days *= 0.7
        reasons.append(f"附件 {found} 天，线路登记 {expected} 天")
    if found and extracted < found:
        reasons.append(f"{found - extracted} 天整理失败，只显示原文")
    units = max(q.units, 1)
    evidence = (q.direct_units + 0.8 * q.inferred_units + 0.5 * q.auto_attached) / units
    fees = {0: 0.3, 1: 0.6, 2: 1.0}[bool(content.inclusions) + bool(content.exclusions)]
    if not content.inclusions and not content.exclusions:
        reasons.append("费用包含、不含都为空")
    elif not content.inclusions:
        reasons.append("费用包含为空")
    elif not content.exclusions:
        reasons.append("费用不含为空")
    penalty = 0.0
    for issue in q.issues:
        code = issue["code"]
        if code in SEVERE:
            penalty += 0.15
        elif code in REMOVED and not issue["path"].endswith("countries"):
            penalty += 0.02
        elif code in SOFT:
            penalty += 0.01
    issues = max(0.0, 1 - penalty * 4 / max(found, 1) ** 0.5)
    risky = sum(1 for u in q.unmapped if u.get("risky"))
    if risky:
        reasons.append(f"{risky} 条涉及费用或条件的原文没有归入任何字段")
    counts = content.meal_counts
    if found and (counts.breakfast is not None or counts.main is not None):
        breakfast, main = included_meals(content)
        if counts.breakfast is not None and counts.breakfast != breakfast:
            reasons.append(f"费用包含写早餐 {counts.breakfast} 餐，逐日为 {breakfast} 餐")
        if counts.main is not None and counts.main != main:
            reasons.append(f"费用包含写正餐 {counts.main} 餐，逐日为 {main} 餐")
    image = 1 - 0.3 * q.image_units / units
    parts = {
        "days": min(days, 1.0),
        "evidence": min(evidence, 1.0),
        "fees": fees,
        "issues": issues,
        "risk": max(0.0, 1 - 0.1 * risky),
        "image": image,
    }
    score = round(100 * sum(WEIGHTS[k] * parts[k] for k in WEIGHTS), 1)
    if score < REVIEW_BELOW and not reasons:
        reasons.append("证据覆盖率低或问题项较多")
    return score, reasons
