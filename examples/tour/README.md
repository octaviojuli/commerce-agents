# ACME 旅行社 (tour)

The tour example runs the shopping agent for a travel agency's store advisor (顾问), who
sells 散拼团 to the customer sitting in front of them: the advisor states the need in
Chinese, the agent searches routes (线路), opens a route's dated departures (团期) priced
for that party, and holds seats (占位) in the agency's ERP while the customer decides.
The advisor is the user, not the traveler. There is no merchant portal, and nothing is
paid for: a hold is the only write, and it expires on its own.

## Run

```bash
python scripts/run_demo.py tour                 # API :8004 + advisor workbench :3004
python scripts/run_demo.py tour --api-only      # the API alone
```

The workbench is `storefront-web/`, a Next app in the `examples/` npm workspace; it calls
the API cross-origin at `http://localhost:8004`; its chrome reads `storefront-web/lib/copy.ts`,
the Chinese laid over `web-shared`'s English `DEFAULT_COPY`. There is no merchant portal, so
`--merchant` and `--all` have nothing to start.

Or start the API yourself:

```bash
uvicorn tour.api.main:app --app-dir examples --reload --port 8004
```

Chat needs a model. Copy `.env.example` in this directory to `.env` and fill in one block:
an Anthropic key alone runs the repo's default Claude models; DeepSeek or Kimi run through
their Anthropic-compatible endpoints by setting `ANTHROPIC_BASE_URL`, the provider's key as
`ANTHROPIC_API_KEY`, and the provider's model ids as `TOUR_MODEL` and `TOUR_MEMORY_MODEL`
(`api/agent_config.py` reads the two). Reading the catalog needs no key. A third-party
endpoint ignores prompt caching, so `cache_read_input_tokens` stays at zero there.

## Try

`scripts/smoke_chat.py --vertical tour` runs the same three turns. The advisor types what
the customer said, in Chinese:

1. 10月中旬有四位客人计划去新疆伊犁，8–10天，两个大人两个小孩，孩子5岁和9岁，不要购物店。
2. 那条 10 天的喀拉峻深度线路，10 月 15 号前后有什么团？
3. 就 10/14 那个团，帮我把 4 个位置锁上。

The first turn is one `search_products` over 10-11 to 10-20: a shortlist of 纯玩 routes with
their 起价 — 5,780 元 for the 8 日小团, 7,880 元 for the 10 日深度 line, 9,680 元 for the
五钻轻奢 one — and the 酒店标准, 天数, and 车型 that separate them. The second turn names
the 10 日深度 line, the only ten-day route in the catalog, and is `get_product_details` on
its id: its 团期 over the searched window and a week either side, each with 余位, 成团状态,
and a 2大2小 total. The third turn is `add_to_cart` on the 10/14 团期, the one in that
window that still seats four, and the reply carries the hold counting down from
thirty minutes. A 团期 that can no longer seat the party is refused instead, and the
refusal names the sibling departures that can take it.

Single prompts worth trying after those turns:

- 把 10/14 和 10/21 两个团做成清单发给客人 — `present_shortlist`: a card with both 团期,
  their 2大2小 totals, and a 客人链接 the advisor copies; the link's token never reaches
  the model.
- 这个团的退改政策怎么说 — `search_policies` over `data/policies.json`, rendered as a
  policy card the advisor reads out rather than paraphrases.
- 帮我把占位改成 3 个人 — `update_cart_item`: the hold is released and re-taken for the
  smaller party; a party that no longer fits leaves the original hold standing.

## What is specific to this example

- `api/erp_client.py`: the `ErpClient` Protocol — six calls: `search_routes`,
  `list_departures`, `get_departure`, `create_hold`, `release_hold`, `list_holds` — and the
  records they exchange (`RouteQuery`, `RouteRecord`, `DepartureRecord`, `HoldRecord`) as
  plain dataclasses, so a real ERP maps onto them without importing this example.
  `ErpSoldOut` carries the sibling departure ids; every `ErpError` message is
  advisor-facing Chinese.
- `api/http_erp.py`: `HttpErpClient`, the same Protocol over a real 旅行社 ERP's HTTP API.
  It maps fields and errors and nothing else: the six calls become JSON requests, the ERP's
  `code` becomes the matching `ErpError` and its Chinese `message` is kept as written, a
  timeout, a refused connection, a 5xx, or a body that is not JSON becomes `ErpUnavailable`,
  and a route missing a required field is dropped with a warning instead of failing the
  search. Holds carry an `Idempotency-Key` over the session, departure, and party, so a
  retry cannot take the seats twice. `api/main.py` builds it when `TOUR_ERP_BASE_URL` is
  set, sending `TOUR_ERP_TOKEN` as the bearer, and `MockErpClient` when it is not; hold
  expiry notices are the mock's alone, so `deliver_hold_events` does nothing against HTTP.
- `api/mock_erp.py`: `MockErpClient`, that Protocol over `data/`. Routes are ranked by how
  much of the query's Chinese text (character 2-grams, plus a small synonym set) each field
  holds, then by price. Holds live in memory, expire after the departure's
  `hold_ttl_minutes`, and are swept at the top of every call; the sweep returns the seats
  and leaves a `HoldNotification` per expiry. One conversation holds at most three
  departures and cannot hold the same one twice. A sold-out hold names the same route's
  departures within ±14 days that still seat the party, nearest date first.
- `api/tour_backend.py`: `TourBackend`, the `StorefrontBackend`. A route is a family and its
  departures are that family's variants, so search stays a shortlist of lines and seats and
  quotes come from one details call. The window and party the advisor last searched are kept
  per session: details price every departure for that party and state it back as
  `quote_party`, and `add_to_cart` splits the quantity into 成人 and 儿童 the same way. A
  cart line is a live hold; `update_cart_item` releases and re-takes it, and puts the
  original back when the larger party no longer fits. `add_to_cart` refuses a route id,
  because a price and a seat only exist on a date.
- `api/tour_backend.py` also holds `TourToolExecutor`: `ErpSoldOut` becomes `Unavailable`,
  which the base executor relays as ids only, and the three ERP rules the advisor can act
  on (报名截止, 占位上限, 占位已过期) are relayed as their own Chinese text, so the model
  says what stands in the way rather than reporting an outage. `ErpUnavailable` stays an
  outage.
- When nothing exact is quotable, the search widens in three steps and each relaxed route
  says in Chinese what it misses: the date window by a week (`match=adjacent_date`,
  `无 10/3–10/8 团期，最近为 10/2`), then the day count by two either way, then the hotel
  and 纯玩 preferences (`match=similar_route`, `酒店为四钻，要求五钻`).
- `api/agent_config.py`: the shopping config. `enable_fulfillment=False`, because the
  customer joins the group at its 集合地点; `product_id_patterns` replaced with the two id
  shapes this catalog has; the cart capped at three lines of up to twenty people, matching
  the ERP's hold allowance; 旅行社 terms added to the policy and order lexicons; and
  `domain_search_notes` stating how a Chinese date phrase becomes a window and that a
  booking takes a departure id.
- `api/shortlist.py`: `present_shortlist`, the one presentation extension. The model names
  one to five 团期 it has seen and writes the title; the server joins each to its 线路, names
  in Chinese the ids it dropped for want of provenance, refuses the call when nothing is
  left, and mints the customer's link through `TourBackend.create_share_link`, which holds
  the token and the shortlist's ids in memory so the link's contents never pass through the
  model. `POST /api/share/{token}/choose` is where the customer's page answers: it takes no
  session, because the customer is not a user here, and the 团期 they chose becomes an app
  event on the advisor's conversation, or on their other live sessions when that one has
  ended. `TOUR_SHARE_BASE_URL` is the origin the link points at, `http://localhost:3004` by
  default; the page itself is not part of this example.
- `api/main.py`: the host. Every cart payload carries the conversation's 占位 with what is
  left of each TTL, and the ERP's expiry notices are drained before a turn and handed to the
  agent as app events on it.

`storefront-web/` is the advisor's workbench, derived from travel's storefront and Chinese
throughout:

- `components/generative/RouteCarousel.tsx` renders routes with the trade-offs an advisor
  reads out (天数, 住宿标准, 车型, 购物店, 起价, 余位) and a relaxed route's `mismatch` note;
  the same component renders 团期 cards with 出发日期, 余位/总位, 成团状态, 报名截止, and the
  party's total.
- `components/HoldBar.tsx` counts down every 占位 the cart payload carries and flips to
  已过期 at zero; `components/HoldPanel.tsx` is the bag, with a party stepper in place of
  the shared quantity controls.
- `components/generative/ShortlistCard.tsx`, `PolicyCard.tsx`, and `QuoteSheet.tsx` render
  `present_shortlist`, `present_guide`, and `checkout`; `ComparisonSpread`, `PlanChecklist`,
  and `BookingStatusCard` are travel's, re-labelled.
- `lib/copy.ts` is the Chinese chrome laid over `web-shared`'s `DEFAULT_COPY`, passed to
  `StoreShell`. `app/showcase` renders every card from `lib/showcase-fixtures.ts`, a
  snapshot of one advisor search that `api/tests/test_showcase.py` holds to the live
  records.

The filter keys the model writes into `filters.attributes` on every search:

| Key | Value |
|---|---|
| `destination` | 伊犁, 喀纳斯, 南疆, 青海; matched against a route's destination and its region, either way round |
| `depart_from`, `depart_to` | ISO dates; the window this and every later quote is made for |
| `days_min`, `days_max` | whole days |
| `adults`, `children`, `child_ages` | the party; ages are pipe-separated, `5\|9`, and a route whose 儿童最小年龄 is above the youngest child is dropped |
| `departure_city` | the customer's 出发城市; carried on the query, not filtered on by the mock |
| `no_shopping` | `yes` keeps only routes with zero 购物店 |
| `hotel_level` | 三钻, 四钻, 五钻, 特色民宿 |

`SearchFilters.max_price` is a ceiling on the route's 起价 and the ERP applies it;
`min_price` is the advisor asking for the better tier and the backend applies it after,
so relaxation cannot put a cheaper route back.

## Data

- `data/routes.json`: eight routes over four destinations (four 伊犁, two 喀纳斯, one 南疆,
  one 青海), each with its 行程规格 (days, 住宿标准, 车型, 成团人数, 含餐), its 适合标签,
  its 购物店 and 自费项目 counts, 儿童最小年龄, and a daily-drive and intensity figure.
- `data/departures.json`: sixty-six departures, six to twelve per route, each with seats
  total and left, 成团状态 (`confirmed`, `pending`, `closed`), 报名截止, adult and child
  prices, 单房差, and `hold_ttl_minutes` (thirty throughout).
- `data/policies.json`: 退改政策, 儿童价规则, 成团规则, 定金规则 — what `search_policies`
  answers from.
- `data/users.json`: two advisor profiles — the 门店 they work in, the 客群 they see, and
  how they quote and hold. `data/memory-seed.json` carries the same habits as memory.
- `data/orders.json`: empty. The demo starts with nothing booked; the session's holds are
  what the advisor accumulates.

Departure dates are authored as if `dates_anchored_to` were today and shift forward by
whole weeks at load, so a demo booted any week has departures ahead of it; each id is
rebuilt from the shifted date. A route id is `RT-dddd`; a departure id is
`DP-<the route's digits>-YYYYMMDD`.

`scripts/check.py` validates the other verticals' `catalog.json`-based fixtures and does
not cover these files; `api/tests/test_fixtures.py` holds them to their ids, dates, and
enumerated fields.

## The ERP contract

`ErpClient` in `api/erp_client.py` is the seam: the six calls and the records they
exchange. `MockErpClient` in `api/mock_erp.py` answers them from `data/`, and
`HttpErpClient` in `api/http_erp.py` answers them over HTTP. What the
calls owe — the ranking, the window, the sold-out siblings, the hold rules and their
expiry — is stated as tests in `api/tests/test_mock_erp.py`, so a second client is held to
the same rules.

Sessions and identity are the shared host code in [`../demo_common/`](../demo_common/): a
session id stands for one advisor.

## Diagrams

[`docs/architecture.html`](docs/architecture.html) is a single page with the system
architecture, the two-layer route-then-departure sequence, the hold / sold-out /
share-back sequence, the ERP-record to `Product` mapping, the hold state machine, and the
`ErpClient`-to-HTTP contract table. Open it in a browser; it has no build step.
