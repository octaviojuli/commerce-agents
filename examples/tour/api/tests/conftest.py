# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
import tempfile
from datetime import date

import pytest

from demo_common.storefront_fixtures import load_json
from demo_common.tests.fixtures import *  # noqa: F403
from tour.api.mock_erp import MockErpClient
from tour.api.tour_backend import DATA_DIR, TourBackend

# Departure dates move forward by the whole weeks between routes.json's dates_anchored_to
# and the backend's clock; a backend pinned to that day keeps the authored dates and ids.
PINNED_TODAY = date.fromisoformat(load_json(DATA_DIR, "routes.json")["dates_anchored_to"])
# The 同行 customer and the advisor mobile every fixture order is written for.
CUSTOMER_ID = 4101
CONTACT_MOBILE = "13900000001"

# The suite runs on data/, never on an agency's own ERP: examples/tour/.env may name a live
# one, and ``load_demo_env`` leaves a variable already in the environment alone. The sessions
# and the memory a test writes go to a temporary state directory for the same reason: the
# module builds both stores at import time, and a run must not touch a demo's own.
os.environ["TOUR_ERP_BASE_URL"] = ""
os.environ["TOUR_STATE_DIR"] = tempfile.mkdtemp(prefix="tour-state-")


@pytest.fixture(scope="session")
def main():
    # Imported here, after the ERP variable above is cleared, because the module builds its
    # client at import time. TestClient is not used as a context manager in these tests, so
    # the app's lifespan never runs; the listing snapshot the catalog routes read is loaded
    # here instead.
    from tour.api import main as main_module

    asyncio.run(main_module.backend.load_listings())
    return main_module


@pytest.fixture(scope="session")
def make_storefront():
    return lambda: TourBackend(
        MockErpClient(today=PINNED_TODAY),
        today=PINNED_TODAY,
        customer_id=CUSTOMER_ID,
        contact_mobile=CONTACT_MOBILE,
    )


@pytest.fixture(scope="session")
def extra_public_routes() -> set[str]:
    # The customer's own two pages — choosing a 团期 on a shared shortlist, and reading and
    # answering one version of a 定制方案; they hold no session here.
    return {
        "/api/share/{token}/choose",
        "/api/share/plan/{token}",
        "/api/share/plan/{token}/respond",
    }


@pytest.fixture(scope="session")
def cart_product() -> str:
    # 伊犁北疆环线's 10/17 团期: four seats left, so a 2 大 2 小 party is a 预留 and not a 候补.
    return "DP-3008"


@pytest.fixture(scope="session")
def relevance_probe() -> tuple[str, str, str, set[str]]:
    """Returns (query, non-relevance sort, the route that must lead, faint matches to cut).
    The search reads the 线路文档, so the probe is a place one document names and the others
    do not: 白哈巴 is on the 喀纳斯 秋色 line alone, and no sort may pull 伊犁 or 南疆 into it."""
    return (
        "白哈巴",
        "rating",
        "RT-1031",
        {"RT-1021", "RT-1022", "RT-1032", "RT-1041", "RT-1051"},
    )
