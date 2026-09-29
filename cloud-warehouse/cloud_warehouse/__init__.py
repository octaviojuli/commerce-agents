"""Travel warehouse business services, independent of the two agent runtimes.

``persistence`` supplies scoped transactions, ``integrations`` supplier reads,
``database_pool`` owns fair connection handoff and engine/pool lifetime,
``concurrency`` moves complete synchronous operations off the request event loop,
``catalog`` catalog publication and queries, ``auth`` platform sessions, and ``api``
the HTTP surface. ``http_observation`` emits content-free HTTP completion logs and request IDs.
``http_metrics`` exports optional operations-only Prometheus HTTP counters and latency histograms.
``changes`` owns persistent approvals, ``inventory`` managed stock,
``imports`` Excel validation/publication, and ``admin``/``cli`` operational entry points.
``advisor`` and ``merchant`` implement the original role interfaces; ``conversations``
stores organization/user-scoped turns, and ``pricing``/``quotes`` own buyer prices.
``platform_sales`` supplies operator-enabled shared catalog access and independent
advisor onboarding; private workspaces retain record ownership without an agency requirement.
``travel_search`` splits destination terms and expands a bounded shorthand vocabulary.
``legacy_references`` resolves old catalog IDs with an explicit source and live buyer access.
``legacy_cases`` imports frozen owner-mapped historical cases through offline administration;
runtime reads check ownership and the original grant version, without replaying model state.
``price_books`` owns approved price windows, buyer grades and whole-schedule selection.
``offers`` owns approved Excel sellable plans sharing a departure inventory pool.
``product_display`` owns source-separated API display copy and independent display versions.
``documents`` owns source/review/publication and parsing leases, ``route_doc`` the shared
content contract, and ``assets`` private immutable file storage.
Document extraction jobs retain external cursors across bounded worker steps;
immutable parses retain the extraction and comparison without publishing it.
``route_editor`` owns immutable drafts and existing approvals, ``route_content``
normalizes daily blocks and customer projections, and ``route_tags`` supplies approved tags.
``object_gc`` supplies operator-only, journaled collection of old unreferenced local objects.
``jobs`` supplies persistent synchronization schedules and fenced worker leases.
``outbox`` supplies durable per-consumer receipts, retries and worker observations;
``search`` supplies version-checked product projections with live-data fallback.
``operations`` reports scoped runtime evidence and sanitized approved-command failures.
``sources`` supplies Excel source onboarding and versioned controls.
``sales`` supplies approved local departure controls and shared sales eligibility.
``quote_shares`` supplies revocable owner-issued customer projections of quote snapshots.
``route_consistency`` checks field conflicts; ``route_applicability`` selects approved departure versions.
``route_extraction`` validates optional source-cited model fields; ``reparse_diff`` compares append-only parser upgrades.
``order_contracts`` defines inactive phase-two data contracts, without transaction execution.
``distribution`` supplies scoped grant listing and audited supplier administrator controls.
``invitations`` supplies buyer requests and supplier approvals for new distribution grants.
"""
