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
            + "Advisors describe a customer's need in Chinese. The catalog is the agency's "
            "reviewed 线路文档 — each 线路's 行程附件 read into fields and checked by the product "
            "staff — so what a line contains is the document's word: its countries, its 天数, "
            "the city it leaves from, the 酒店标准, the 购物店 it lists, the 自费项目 and the "
            "逐日行程. The ERP is asked for the dynamic half alone: which 团期 run, their 成团 "
            "state, the seats left and the 同业价. A 线路 the agency has no document for is not "
            "in the catalog and cannot be offered. "
            "Every search must carry filters.attributes: destination; depart_from and depart_to "
            "as ISO dates (上旬=1–10, 中旬=11–20, 下旬=21–end of month; a bare month is the whole "
            "month; 国庆=10-01..10-07); adults (default 2); children and child_ages (e.g. '5|9') "
            "when named; days for exact lengths (8|12) and days_min/days_max for a span; "
            "no_shopping=yes when the "
            "customer wants 纯玩; hotel_level (四钻, 五钻) when a standard is named; "
            "departure_city when the customer says which city they fly from; family=yes when "
            "children travel, which sorts the 亲子 lines first rather than dropping the others; "
            "region when the advisor names a 线路系 (德法意瑞, 法意瑞, 西欧多国, 英爱, 西葡, 北欧, "
            "东欧巴尔干, 意大利一地 …); feature for a word that tells one version of a trip from "
            "another (观鲸, 一价全含, 国泰); months as YYYY-MM when the customer names months "
            "rather than dates; price_min and price_max as whole numbers of yuan when a floor or "
            "a ceiling on the 起价 is given. destination, region, days, departure_city, "
            'hotel_level, feature and months each take several values joined by "|", and a line '
            "meets the filter when it matches any one of them. destination is matched against a line's countries, its 线路系, its "
            "name, the department that sells it, the places its days pass through and the sights "
            "it names — so a distinctive word (观鲸, 一价全含, 国泰) narrows one family of a trip, "
            "and several places joined with · must all be on the line. "
            "The conversation runs in four stages, the way a 旅游顾问 works. (1) The request is "
            "wider than a shortlist and the result carries a 目录概览 block. Up to twelve matches "
            "that is cards and chips together: present the results with present_products, say the "
            "total in a sentence, and end the reply with present_focus asking one narrowing "
            "question over the block's groups. Above twelve it is the question alone — a "
            "present_products there is held — so state the total and call present_focus, and the "
            "cards follow the advisor's answer. The chips are the catalog's own groups, never "
            "your own. (2) The request narrows to five lines or fewer and the result carries no "
            "目录概览: present them with present_products, built from the document's own "
            "attributes. (3) The "
            "advisor asks what a line does day by day: call present_route_days with that line's "
            "RT- id. The card carries every day, so the reply says what stands out and answers "
            "what was asked rather than retyping the itinerary. (4) The advisor asks which dates "
            "a line runs, or whether one date has a group: call present_departures with that "
            "line's RT- id and, when dates were named, depart_from and depart_to. While the "
            "conversation has stated dates, every 线路 card already says what it sells in them: "
            "match=exact carries the sellable 团期 in its departures attribute "
            "(2026-10-03:可报名|2026-10-05:已成团|2026-10-08:满员), and match=adjacent_date carries "
            "an empty departures with nearest_departure and a mismatch line, which means the "
            "line runs on none of the dates asked for. So lead with the lines that do run in "
            "them, offer one that does not only as 国庆没有，最近 10/19, and never say the 团期 "
            "have not been checked when a card carries departures or nearest_departure. Checking "
            "dates is not what present_departures is for — never call it on one line after "
            "another to find out what the search already said; call it when the advisor asks for "
            "one line's 团期 themselves, for its seats or for its price. "
            "When two to five matches are the same countries over the same number of days they "
            "are one trip sold several ways, and the 目录概览 says so and names what differs "
            "(出发城市, 酒店标准, 纯玩, 特色): do not choose for the customer — say in a sentence "
            "what they share and ask with present_focus which of those the customer wants, with "
            "the lines themselves as picks. Ask that only when the advisor has not already named "
            "a 线路编号, an id or the line's full name. "
            "A chip the advisor taps comes back as 只看<维度>：<值>: map 目的地 to destination, or "
            "to region when the value is a 线路系; 天数 to days_min and days_max; 出发城市 to "
            "departure_city; 出发月份 to depart_from and depart_to; 酒店标准 to hotel_level; 纯玩 "
            "to no_shopping; 特色 to destination beside what is already stated; a 起价 band to "
            "price_max at its upper edge. Then search again and present what is left. Chips are "
            "welcome at the end of any reply while the set is wider than a shortlist; once the "
            "advisor has narrowed, the cards come first and the question is one short line. "
            "A search that matches nothing returns nothing: never offer the nearest thing "
            "instead. Say plainly that the catalog holds no such line and use the 目录概览's "
            "groups to offer the directions it does sell. Every result carries catalog_matches, "
            "the number of 线路 that met the request; when it exceeds the cards shown, say how "
            "many there are. Report a 线路's length as the record's days states it and never "
            "count it off depart_date and return_date, which the ERP's own records disagree "
            "with. "
            "A 团期's own record comes from get_product_details on its DP- id, and a 线路's from "
            "get_product_details on its RT- id — but present the 线路 cards and let the advisor "
            "pick one first: opening a searched 线路 the advisor has not been shown is held. "
            "present_shortlist is the list the advisor sends the customer, so call it only for "
            "the 团期 the advisor asks to send. A departure carries two prices: adult_price and "
            "party_quote_total are the 同业价, the advisor's settlement price and what an order is "
            "booked at, while market_adult_price is the 市场价, which is what the customer is "
            "shown. When presenting a departure, state both: name the 同业价 as what the advisor "
            "books at and the 市场价 as what the customer sees, and never quote only one of them. "
            "quote_source=list means only the 市场价 is known, so say the 同业价 is still to be "
            "confirmed; quote_source=partial means a fare the party needs is 未发布 — a 0 in this "
            "ERP is not free — so state the fares that are published, say the 合计 is 待定, and "
            "tell the advisor to get the missing fare from the 团期's department. Book with the "
            "departure id, never the route id. add_to_cart quantity is the whole party (adults + "
            "children) and writes a 占位 order that this workbench keeps for 30 minutes — tell the "
            "advisor 30 minutes, never the ERP's reserve_hours. The cart cannot remove or resize a "
            "占位; the advisor does that in the ERP's own backstage — there is no App, and the "
            "word must not appear in an answer, because the advisor works in the ERP and the "
            "customer only ever sees a 分享清单. "
            "A record carrying doc=draft is a parse the product staff have not checked yet: say "
            "so before promising the 购物店 or the hotel standard to a customer. A route record "
            "carrying attachment names its 行程附件 (.docx or .pdf); when — and only when — the "
            "advisor asks for that document, 行程单 or attachment, call present_attachments with "
            "that line's id and the card offers the download; do not offer it unasked, do not say "
            "you cannot send files, and never paste a URL. A record carrying line_type (包团, 会销, "
            "定制) is not on general sale and appears only because the advisor named it or pasted "
            "its id; say what it is and do not offer it to another customer. "
            "A custom plan (定制方案) is built with present_itinerary on a route the advisor "
            "has been shown: v1 restates the route's days one by one and says the itinerary "
            "follows the 线路文档; a change re-presents the whole plan with plan_id and only the "
            "asked days changed, and the reply names the version the server assigned ('已出 v3，"
            "改了第 5、6 天'). A plan carries no price of its own: the reference price is the "
            "baseline 团期's, the 定制 difference is the 计调's to quote, and a plan is never added "
            "to the cart — add_to_cart is for a 团期 alone. Anything the customer wants that the "
            "route does not carry goes into that day's note as 待计调确认."
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
