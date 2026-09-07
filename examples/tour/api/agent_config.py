# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ACME 旅行社 deployment's shopping config. The example has no merchant portal, so
there is no merchant config beside it."""

from __future__ import annotations

import os

from shopping_agent import ShoppingAgentConfig

_DEFAULTS = ShoppingAgentConfig()


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


def build_shopping_config() -> ShoppingAgentConfig:
    model, memory_model = _models()
    return ShoppingAgentConfig(
        model=model,
        memory_model=memory_model,
        brand_name="ACME 旅行社",
        assistant_name="选团助手",
        brand_voice="像一位资深旅游顾问：直接、懂行、主动说明取舍",
        domain_search_notes=(
            "Advisors describe a customer's need in Chinese. Every search must carry "
            "filters.attributes: destination; depart_from and depart_to as ISO dates "
            "(上旬=1–10, 中旬=11–20, 下旬=21–end of month; a bare month is the whole month; "
            "国庆=10-01..10-07); adults (default 2); children and child_ages (e.g. '5|9') when named; "
            "days_min/days_max when a length is given; no_shopping=yes when the customer wants "
            "纯玩; hotel_level (四钻, 五钻) when a standard is named. Search returns routes (lines) "
            "and matches destination against route names and tags, so a destination the catalog "
            "does not name in either finds nothing. A route's departures and their list prices come "
            "only from get_product_details on that route (RT-…); this customer's own price for one "
            "departure comes from get_product_details on that departure id (DP-…). Book with the "
            "departure id, never the route id. add_to_cart quantity is the whole party (adults + "
            "children) and writes a 占位 order; the cart cannot remove or resize one — the advisor "
            "does that in the ERP. State the expanded date window back to the advisor once."
        ),
        # Nothing ships: the customer joins the group at its 集合地点, which the route's
        # specs carry, so the fulfillment tool is not registered at all.
        enable_fulfillment=False,
        # One line is one 团期 and its quantity is the whole party; a conversation writes at
        # most three 占位 orders, because every one of them is a real order in the ERP.
        max_quantity_per_item=20,
        max_cart_lines=3,
        policy_intent_terms=_DEFAULTS.policy_intent_terms + _POLICY_TERMS,
        order_intent_terms=_DEFAULTS.order_intent_terms + _ORDER_TERMS,
        # The two id shapes this catalog has, replacing the defaults: nothing else in the
        # conversation reads as a product id.
        product_id_patterns=(r"\bRT-\d+\b", r"\bDP-\d+\b"),
    )
