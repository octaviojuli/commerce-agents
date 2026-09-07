# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``storefront-web/lib/showcase-fixtures.ts`` against the read it snapshots. The shared
contract's showcase test holds a fixture to ``catalog.json``, which this example does not
have: a route's 起价 and a 团期's 报价 are made for one window and one party, so a literal is
only right against the request that produced it. That request is the one the file's header
states, replayed here on the pinned backend, so a re-anchored fixture or a moved price
fails rather than leaving the showcase quoting a number the ERP no longer returns."""

from demo_common.tests.fixtures import showcase_products
from shopping_agent import SearchFilters

# The search the showcase is a snapshot of, as the file's header comment states it.
QUERY = "伊犁"
SEARCH_ATTRIBUTES = {
    "destination": "伊犁",
    "depart_from": "2026-10-11",
    "depart_to": "2026-10-20",
    "days_min": "8",
    "days_max": "10",
    "adults": "2",
    "children": "2",
    "child_ages": "5|9",
    "no_shopping": "yes",
}
# The route whose 团期 the showcase opens; its variants are the other three literals.
DETAILED_ROUTE = "RT-1022"


async def test_showcase_literals_are_the_records_that_search_and_details_returned(
    main, backend, session
):
    routes = await backend.search_products(
        session, QUERY, SearchFilters(attributes=dict(SEARCH_ATTRIBUTES))
    )
    details = await backend.get_product_details(session, DETAILED_ROUTE)
    assert details is not None, DETAILED_ROUTE
    # The same serialisation the API emits, so key order and omitted defaults line up.
    records = {
        product.product_id: product.model_dump(mode="json", exclude_unset=True)
        for product in [*routes, *details.variants]
    }
    fixtures = showcase_products(main.DATA_DIR.parent)
    assert len(fixtures) == 6, len(fixtures)
    for fixture in fixtures:
        product_id = fixture["product_id"]
        assert product_id in records, (product_id, sorted(records))
        record = records[product_id]
        differing = sorted(
            key for key in fixture.keys() | record.keys() if fixture.get(key) != record.get(key)
        )
        assert fixture == record, (product_id, differing)
