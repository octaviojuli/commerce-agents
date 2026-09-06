# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import asyncio
from datetime import date

import pytest

from demo_common.storefront_fixtures import load_json
from demo_common.tests.fixtures import *  # noqa: F403
from tour.api import main as main_module
from tour.api.mock_erp import MockErpClient
from tour.api.tour_backend import DATA_DIR, TourBackend

# Departure dates move forward by the whole weeks between routes.json's dates_anchored_to
# and the backend's clock; a backend pinned to that day keeps the authored dates and ids.
PINNED_TODAY = date.fromisoformat(load_json(DATA_DIR, "routes.json")["dates_anchored_to"])


@pytest.fixture(scope="session")
def main():
    # TestClient is not used as a context manager in these tests, so the app's lifespan
    # never runs; the listing snapshot the catalog routes read is loaded here instead.
    asyncio.run(main_module.backend.load_listings())
    return main_module


@pytest.fixture(scope="session")
def make_storefront():
    return lambda: TourBackend(MockErpClient(today=PINNED_TODAY), today=PINNED_TODAY)


@pytest.fixture(scope="session")
def extra_public_routes() -> set[str]:
    return set()


@pytest.fixture(scope="session")
def cart_product() -> str:
    return "DP-1021-20261017"


@pytest.fixture(scope="session")
def relevance_probe() -> tuple[str, str, str, set[str]]:
    """Returns (query, non-relevance sort, the route that must lead, faint matches to cut).
    The faint ones carry 摄影 or 深度游 as fit tags but go somewhere else; a tour search is
    a destination search first, and the sort never reaches past that."""
    return ("伊犁草原深度摄影", "rating", "RT-1022", {"RT-1031", "RT-1041", "RT-1051"})
