"""Server-owned advisor stages; model suggestions cannot grant action authority."""

import re
from datetime import UTC, datetime

from .trip_brief import readiness


def derive(brief):
    ready = readiness(brief)
    stale = bool(brief.quote_id and (brief.quote_brief_version or 0) < brief.quote_fields_version)
    if brief.quote_id:
        try:
            valid_until = datetime.fromisoformat(
                (brief.quote or {}).get("quote_valid_until") or brief.quote["fresh_until"]
            )
            stale = (
                stale
                or valid_until <= datetime.now(UTC)
                or bool(brief.quote.get("quote_expired", brief.quote.get("snapshot_stale")))
            )
        except (KeyError, ValueError, TypeError):
            stale = True
    if not ready["search"]["ready"]:
        stage = "need"
    elif brief.share_token and not stale:
        stage = "shared"
    elif brief.quote_id and not stale:
        stage = "quote"
    elif brief.route_id:
        stage = "departure"
    else:
        stage = "select"
    actions = ["update_trip_brief"]
    if ready["search"]["ready"]:
        actions += ["search_routes"]
    # perform() verifies that the named route was read by this conversation.
    actions += ["departures"]
    if brief.route_id:
        actions += ["offers"]
    if brief.route_id and ready["quote"]["ready"]:
        actions += ["quote"]
    if stage in ("quote", "shared") and brief.quote and brief.quote.get("complete"):
        actions += ["share_quote"]
    if stage == "shared":
        actions += ["customer_confirmed", "offline_hold_recorded"]
    return {"stage": stage, "allowed_actions": actions, "quote_stale": stale}


def public_suggestions(brief, values, seen):
    """Keep bounded text chips; clicking one never bypasses the action services."""
    allowed = set(derive(brief)["allowed_actions"])
    gates = (
        (r"询价|报价|比价|多少钱|价格", "quote"),
        (r"发给客人|发给客户|分享|对客链接", "share_quote"),
        (r"查.*团期|看.*团期|发团日期", "departures"),
        (r"检索|搜.*线路|找.*线路|推荐.*线路", "search_routes"),
        (r"客人.*确认", "customer_confirmed"),
        (r"线下.*(?:备注|记录)", "offline_hold_recorded"),
    )
    result = []
    for item in values:
        if not isinstance(item, str) or not 1 <= len(item.strip()) <= 160:
            continue
        item = item.strip()
        if not set(re.findall(r"(?<![A-Za-z0-9])W[PDO]-[A-Za-z0-9-]+", item)) <= set(seen):
            continue
        if re.search(r"下单|支付|收款|自动占位|立即占位|锁位", item):
            continue
        if any(re.search(pattern, item) and action not in allowed for pattern, action in gates):
            continue
        if item not in result:
            result.append(item)
    return result[:6]
