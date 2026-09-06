# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The shared contract, minus what this example does not have. The tests are imported by
name instead of star-imported, because ``demo_common.tests.contract`` also covers the
merchant portal, the storefront's showcase fixtures, and a cart of quantities, and the
tour example has none of those.

Left out, and why:

- ``test_public_route_sets_are_closed_and_each_role_has_its_own_token_store``,
  ``test_memory_routes_carry_the_key_in_the_body``,
  ``test_ids_containing_slashes_reach_the_id_routes``,
  ``test_merchant_session_binds_the_server_held_identity``,
  ``test_health_names_the_store_and_the_model``,
  ``test_merchant_memory_lists_deletes_one_fact_and_purges``,
  ``test_listings_total_counts_the_universe_and_survives_paging``,
  ``test_overview_and_alerts_carry_the_portal_keys``,
  ``test_preview_card_buttons_report_a_hold_apart_from_an_apply``,
  ``test_portal_config_follows_the_host_approval_switch``,
  ``test_snapshot_periods_end_before_boot_and_compare_to_the_prior_block``,
  ``test_campaigns_and_issues_land_in_the_same_week_as_the_snapshot``,
  ``test_listing_ids_resolve_case_insensitively_and_browse_queries_return_the_universe``,
  ``test_pricing_context_repeats_the_deployments_movement_caps``,
  ``test_every_staging_path_is_the_agents_proposal_and_applying_is_the_operators_act``,
  ``test_unknown_ids_are_refused_on_every_staging_path``,
  ``test_campaign_previews_carry_budget_audience_and_copy``,
  ``test_two_staged_restocks_both_count_when_applied``: all need the merchant portal, which
  this example does not have.
- ``test_showcase_products_are_catalog_records_plus_the_backends_stamps``: reads the
  catalog through ``load_catalog(main.DATA_DIR)``, and this example has no ``catalog.json``;
  ``storefront-web/lib/showcase-fixtures.ts`` is checked against the live records by hand.
- ``test_presentation_extensions_advertise_their_payload_models``: the deployment registers
  no presentation extension.
- ``test_orders_route_lists_the_callers_own_orders_newest_first`` and
  ``test_orders_are_newest_first_per_user_and_resolve_case_insensitively``: both require a
  non-empty order history, and the demo starts with no 报名单 — every booking in it is a
  seat hold taken during the session (``data/orders.json``).
- ``test_cart_lines_belong_to_the_session``: it adds the same product twice and expects the
  quantities to sum. A tour cart line is one 占位 on one 团期, and the ERP refuses a second
  hold on a departure this conversation already holds; ``test_tour_backend.py`` covers the
  session isolation, the party change, and the release in the terms this cart has.
"""

from demo_common.tests.contract import (  # noqa: F401
    seeded_shopper,
    test_a_non_relevance_sort_keeps_only_relevant_matches,
    test_delete_and_edit_touch_exactly_one_fact_of_the_callers_own,
    test_every_scoped_route_refuses_a_missing_or_unknown_token,
    test_json_body_without_a_content_type_is_not_parsed,
    test_no_route_reads_identity_from_the_request,
    test_non_loopback_host_names_are_rejected_before_any_route,
    test_purge_deletes_everything_and_clear_memory_reseeds,
    test_seed_file_matches_what_a_fresh_shopper_sees,
    test_storefront_session_binds_the_profile_and_reset_reissues_it,
    test_unknown_users_get_a_guest_profile,
)
