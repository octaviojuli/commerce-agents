# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ACME 旅行社 deployment's shopping config. The example has no merchant portal, so
there is no merchant config beside it. ``live`` is the switch the host throws when the ERP
behind the seam is the agency's own: the agency's rules are not in this repo, so the policy
tool goes with it."""

from __future__ import annotations

import os

from shopping_agent import ShoppingAgentConfig

_DEFAULTS = ShoppingAgentConfig()

# What the deployment calls itself and its assistant. The example ships ACME's names; an
# agency running it puts its own in the environment, and the workbench and every order the
# backend writes read the same two values.
DEFAULT_BRAND_NAME = "ACME 旅行社"
DEFAULT_ASSISTANT_NAME = "选团助手"


def brand_name() -> str:
    """``TOUR_BRAND_NAME``, the agency's own name. The backend's ``store_name`` is this in
    live mode, where the fixture's is not the agency's."""
    return os.environ.get("TOUR_BRAND_NAME", "").strip() or DEFAULT_BRAND_NAME


def assistant_name() -> str:
    """``TOUR_ASSISTANT_NAME``, what the advisor calls the assistant."""
    return os.environ.get("TOUR_ASSISTANT_NAME", "").strip() or DEFAULT_ASSISTANT_NAME


def _models() -> tuple[str, str]:
    """The turn model and the memory-extraction model, from ``TOUR_MODEL`` and
    ``TOUR_MEMORY_MODEL``. The agency picks the provider by pointing the Anthropic SDK at an
    Anthropic-compatible endpoint (``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_API_KEY``), and the
    model ids that endpoint serves are not Claude's, so both come from the environment;
    unset, the repo defaults stand. A provider that serves one model runs extraction on it too."""
    model = os.environ.get("TOUR_MODEL") or _DEFAULTS.model
    memory_model = os.environ.get("TOUR_MEMORY_MODEL") or (
        model if os.environ.get("TOUR_MODEL") else _DEFAULTS.memory_model
    )
    return model, memory_model


def _retention_days() -> int | None:
    """``TOUR_MEMORY_RETENTION_DAYS``, how long an extracted fact stays readable. Unset,
    memory has no age limit; the agent's ``MemoryRuntime`` is what applies the window. A
    value that is not a positive whole number of days fails the boot rather than being
    read as no limit at all."""
    raw = os.environ.get("TOUR_MEMORY_RETENTION_DAYS", "").strip()
    return int(raw) if raw else None


# 旅行社 vocabulary added to the policy-grounding lexicon: the rules an advisor is asked
# to quote rather than paraphrase.
_POLICY_TERMS = (
    "退团",
    "退款",
    "改期",
    "取消",
    "儿童价",
    "单房差",
    "成团",
    "不成团",
    "定金",
    "政策",
)

# What an advisor calls the booking side of the conversation.
_ORDER_TERMS = ("报名单", "订单", "占位", "锁位", "过期")

# The rule for a deployment whose 政策 are not wired up: the agency keeps them in its own
# knowledge base, this deployment reads none, and a rule the model states from memory is the
# one mistake an advisor cannot catch. ``enable_policies=False`` takes the tool away; this
# says what to answer with instead.
_NO_POLICIES_NOTE = (
    "This deployment answers no 政策 question. 退改, 儿童价, 成团, 定金 and 发票 rules live in "
    "the agency's own knowledge base, which is not connected here, and there is no tool that "
    "reads them: say plainly that the rule has to come from the 门店 or the ERP, and never "
    "state one from memory or infer it from a 团期's fields. "
)


def build_shopping_config(*, live: bool = False) -> ShoppingAgentConfig:
    """``live`` says the ERP behind the backend is the agency's own. It switches the policy
    tool off, because the agency's rules are in a knowledge base this deployment does not
    read; everything else is the same on both."""
    model, memory_model = _models()
    return ShoppingAgentConfig(
        model=model,
        memory_model=memory_model,
        memory_retention_days=_retention_days(),
        brand_name=brand_name(),
        assistant_name=assistant_name(),
        brand_voice="像一位资深旅游顾问：直接、懂行、主动说明取舍",
        domain_search_notes=(
            (_NO_POLICIES_NOTE if live else "")
            + "Advisors describe a customer's need in Chinese. Every search must carry "
            "filters.attributes: destination; depart_from and depart_to as ISO dates "
            "(上旬=1–10, 中旬=11–20, 下旬=21–end of month; a bare month is the whole month; "
            "国庆=10-01..10-07); adults (default 2); children and child_ages (e.g. '5|9') when named; "
            "days_min/days_max when a length is given; no_shopping=yes when the customer wants "
            "纯玩; hotel_level (四钻, 五钻) when a standard is named; departure_city when the "
            "customer says which city they fly from; family=yes when children travel, which "
            "sorts the 亲子 lines first rather than dropping the others; region when the advisor "
            "names a 线路系 (德法意瑞, 法意瑞, 西欧多国, 英爱, 西葡, 北欧, 东欧巴尔干, 意大利一地 …) "
            "or picks one off an overview; price_max as a whole number of yuan when a ceiling on "
            "the 起价 is given. Search returns routes "
            "(lines) and matches destination against route names, itinerary tags, 亮点, the "
            "selling department and the departure city, so a destination the catalog writes "
            "down nowhere finds nothing. The 购物, hotel, 出发城市 and 包含 attributes on a route "
            "come from tags the ERP auto-extracted from the itinerary attachment and can be "
            "wrong or missing (shopping=unknown, hotel_grade=unknown say so), so present them "
            "as what the tags claim, and tell the advisor to confirm 购物 and the hotel standard "
            "against the 行程附件 before promising either to the customer. "
            "A route's departures come only from "
            "get_product_details on that route (RT-…), and one departure's own record from "
            "get_product_details on its id (DP-…). Present the routes a search returned with "
            "present_products and let the advisor pick one before opening any route's "
            "departures: get_product_details on a searched route the advisor has not been "
            "shown is held. A route's departures are cards the same way — present them with "
            "present_products on their DP- ids; present_shortlist is the list the advisor "
            "sends the customer, so call it only for the departures the advisor asks to send. "
            "A departure carries two prices: adult_price and "
            "party_quote_total are the 同业价, the advisor's settlement price and what an order is "
            "booked at, while market_adult_price is the 市场价, which is what the customer is "
            "shown. When presenting a departure, state both: name the 同业价 as what the advisor "
            "books at and the 市场价 as what the customer sees, and never quote only one of them. "
            "quote_source=list means only the "
            "市场价 is known, so say the 同业价 is still to be confirmed; quote_source=partial "
            "means a fare the party needs is 未发布 — a 0 in this ERP is not free — so state the "
            "fares that are published, say the 合计 is 待定, and tell the advisor to get the "
            "missing fare from the 团期's department. Report a 线路's length as the record's "
            "days states it and never count it off depart_date and return_date, which the "
            "ERP's own records disagree with. Book with the "
            "departure id, never the route id. add_to_cart quantity is the whole party (adults + "
            "children) and writes a 占位 order that this workbench keeps for 30 minutes — tell the "
            "advisor 30 minutes, never the ERP's reserve_hours. The cart cannot remove or resize a "
            "占位; the advisor does that in the ERP's own backstage — there is no App, and the "
            "word must not appear in an answer, because the advisor works in the ERP and the "
            "customer only ever sees a 分享清单. State the expanded date window back to the "
            "advisor once. Every result carries catalog_matches, the number of 线路 that met "
            "the request in the window; when it exceeds the results shown, say how many there "
            "are. When the search result carries a 目录概览 block, the request was too broad to "
            "shortlist: follow that block — state the total, give the groups, ask one narrowing "
            "question with the group values as chips, and present cards only after the advisor "
            "narrows. A record carrying line_type (包团, 会销, 定制) is not on "
            "general sale and appears only because the advisor named it or pasted its id; say "
            "what it is and do not offer it to another customer. "
            "A custom plan (定制方案) is built with present_itinerary on a route the advisor "
            "has been shown: v1 restates the route's 第N天 specs day by day and says the "
            "itinerary follows the 行程附件; a change re-presents the whole plan with plan_id "
            "and only the asked days changed, and the reply names the version the server "
            "assigned ('已出 v3，改了第 5、6 天'). A plan carries no price of its own: the "
            "reference price is the baseline 团期's, the 定制 difference is the 计调's to "
            "quote, and a plan is never added to the cart — add_to_cart is for a 团期 alone. "
            "Anything the customer wants that the route does not carry goes into that day's "
            "note as 待计调确认."
        ),
        # Nothing ships: the customer joins the group at its 集合地点, which the route's
        # specs carry, so the fulfillment tool is not registered at all.
        enable_fulfillment=False,
        # The agency's 政策 are in a knowledge base this deployment does not read, so against
        # a live ERP there is no policy tool at all; the fixtures carry data/policies.json.
        enable_policies=not live,
        # One line is one 团期 and its quantity is the whole party; a conversation writes at
        # most three 占位 orders, because every one of them is a real order in the ERP.
        max_quantity_per_item=20,
        max_cart_lines=3,
        # A turn that redraws a 定制方案 sends every day again, and a provider that reasons
        # before it answers spends the same budget on the reasoning: a ten-day revision on
        # the fixtures ran to 4,065 output tokens, and a 线路 runs to twenty days. The repo
        # default of 2048 ends such a turn before the tool call is written.
        max_tokens=8192,
        policy_intent_terms=_DEFAULTS.policy_intent_terms + _POLICY_TERMS,
        order_intent_terms=_DEFAULTS.order_intent_terms + _ORDER_TERMS,
        # The two id shapes this catalog has, replacing the defaults: nothing else in the
        # conversation reads as a product id.
        product_id_patterns=(r"\bRT-\d+\b", r"\bDP-\d+\b"),
    )
