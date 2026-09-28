# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ACME Travel deployment's two agent configs."""

from __future__ import annotations

import os

from demo_common import host_approval_default
from merchant_agent import MerchantAgentConfig
from shopping_agent import ShoppingAgentConfig

_SHOPPING_DEFAULTS = ShoppingAgentConfig()
_MERCHANT_DEFAULTS = MerchantAgentConfig()

# Room for a day-by-day itinerary or a rate preview with the thinking that precedes it.
_MAX_TOKENS = 8192


def _shopping_models() -> tuple[str, str]:
    """The storefront's turn model and memory-extraction model, from ``TRAVEL_MODEL`` and
    ``TRAVEL_MEMORY_MODEL``. A deployment that points the Anthropic SDK at another provider's
    Anthropic-compatible endpoint (``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_API_KEY``) names that
    provider's model ids here; unset, the repo defaults stand. A provider that serves one
    model runs extraction on it too."""
    model = os.environ.get("TRAVEL_MODEL") or _SHOPPING_DEFAULTS.model
    memory_model = os.environ.get("TRAVEL_MEMORY_MODEL") or (
        model if os.environ.get("TRAVEL_MODEL") else _SHOPPING_DEFAULTS.memory_model
    )
    return model, memory_model


def _merchant_models() -> tuple[str, str]:
    """The portal's turn model and memory-extraction model, from ``TRAVEL_MERCHANT_MODEL``
    (falling back to ``TRAVEL_MODEL``) and ``TRAVEL_MEMORY_MODEL``."""
    named = os.environ.get("TRAVEL_MERCHANT_MODEL") or os.environ.get("TRAVEL_MODEL")
    model = named or _MERCHANT_DEFAULTS.model
    memory_model = os.environ.get("TRAVEL_MEMORY_MODEL") or (
        model if named else _MERCHANT_DEFAULTS.memory_model
    )
    return model, memory_model


# Supplier vocabulary added to the metrics-grounding lexicon.
_METRICS_TERMS = (
    "occupancy",
    "pacing",
    "pace",
    "room nights",
    "nightly rate",
    "adr",
    "bookings",
    "cancellations",
)


def build_shopping_config() -> ShoppingAgentConfig:
    model, memory_model = _shopping_models()
    return ShoppingAgentConfig(
        model=model,
        memory_model=memory_model,
        max_tokens=_MAX_TOKENS,
        brand_name="ACME Travel",
        assistant_name="ACME 助手",
        # The storefront is in Chinese, so the language rule rides on the identity line, where
        # every turn reads it first: a traveler writing Chinese gets Chinese back on every
        # surface, while the catalog's own names stay as written so the cards match the data.
        brand_voice=(
            "well-traveled, candid, allergic to tourist traps, and always in the traveler's "
            "own language: a traveler writing Chinese gets Simplified Chinese in every "
            "sentence and in every field of every presentation card (title, labels, notes, "
            "reasons, pros, cons, best_for, dimensions), with only product names, city names "
            "and ids left as the catalog writes them"
        ),
        domain_search_notes=(
            "Stays and experiences are date-bound: when the traveler has named dates, "
            "pass the check-in date as an ISO filters.attributes['travel_date'] on "
            "every search — results and prices are quotes for those dates, not "
            "catalog constants."
        ),
    )


def build_merchant_config(store_name: str) -> MerchantAgentConfig:
    model, memory_model = _merchant_models()
    return MerchantAgentConfig(
        model=model,
        memory_model=memory_model,
        max_tokens=_MAX_TOKENS,
        brand_name=store_name,
        assistant_name="供应商助手",
        # The portal is in Chinese, so the language rule rides on the identity line, where
        # every turn reads it first; listing names, ids and field names stay as the records
        # write them so the cards match the data.
        brand_voice=(
            "plain and specific, numbers first, and always in the operator's own language: an "
            "operator writing Chinese gets Simplified Chinese in every sentence and in every "
            "field of every presentation card (title, headline, summary, why_it_matters, notes), "
            "with only listing names, ids and field names left as the records write them"
        ),
        require_host_approval=host_approval_default(),
        approval_surface="the Approve button on the change preview card",
        metrics_intent_terms=_MERCHANT_DEFAULTS.metrics_intent_terms + _METRICS_TERMS,
        # Stays price under nightly_rate, so the price-delta caps follow that field and a
        # free-form listing update cannot change it.
        price_bearing_fields=_MERCHANT_DEFAULTS.price_bearing_fields + ("nightly_rate",),
        listing_update_blocked_fields=_MERCHANT_DEFAULTS.listing_update_blocked_fields
        + ("nightly_rate",),
    )
