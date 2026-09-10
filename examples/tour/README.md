# ACME 旅行社 (tour)

The tour example runs the shopping agent for a travel agency's store advisor (顾问), who
sells 散拼团 to the customer sitting in front of them: the advisor states the need in
Chinese, the agent searches 线路 (routes), opens a 线路's dated 团期 (departures) priced for
that party, and writes a 预留 order in the agency's ERP while the customer decides. The
advisor is the user, not the traveler, and the advisor is also the ERP's salesperson: the
deployment logs in with their account, so the reads span every department that account is
authorised for and a quote or an order is written in the 团期's own. A 团期 has two prices —
the 市场价 it lists, which the customer's share page shows, and the 同业价 the ERP quotes this
customer — and what the advisor is quoted, and what an order is booked at, is the 同业价.
There is no merchant portal, and nothing is paid for. An order is the only write the ERP
has: it offers no cancel, release, or amend call, so a written order stands until the
advisor changes it in the ERP's own backstage.

## Run

```bash
python scripts/run_demo.py tour                 # API :8004 + advisor workbench :3004
python scripts/run_demo.py tour --api-only      # the API alone
```

The workbench is `storefront-web/`, a Next app in the `examples/` npm workspace; it calls
the API cross-origin at `http://localhost:8004`, and its chrome is Chinese laid over
`web-shared`'s English `DEFAULT_COPY`. There is no merchant portal, so `--merchant` and
`--all` have nothing to start.

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

The same file carries the ERP block, which decides what is behind the seam:

| Variable | What it names |
|---|---|
| `TOUR_ERP_BASE_URL` | the ERP's `/aicli` root, HTTPS in production; setting it is what puts the example in live mode |
| `TOUR_ERP_MOBILE` | the advisor's ERP login, a mainland mobile number; also the contact written on every order |
| `TOUR_ERP_PASSWORD` | its password; ten failed logins lock the account for fifteen minutes |
| `TOUR_ERP_CUSTOMER_CODE` | the 同行 customer's 客户编码 (the ERP's `csCode`), resolved to one customer at boot |
| `TOUR_ERP_STORE_NAME` | the 门店 a 同行 order is written through, which the ERP requires |
| `TOUR_ERP_ALLOW_PAST` | `1` lists departures that already left, for a beta with no future ones |
| `TOUR_BRAND_NAME` | the agency's own name, `ACME 旅行社` unset |
| `TOUR_ASSISTANT_NAME` | what the advisor calls the assistant, `选团助手` unset |

Unset `TOUR_ERP_BASE_URL` and `api/main.py` builds `MockErpClient` over `data/`, which is
what the demo runs on. Set it and `api/main.py` builds `HttpErpClient` and logs in with
those credentials, and the example is then talking to a live ERP: the third smoke turn
below writes a real 预留 order on it, for the customer `TOUR_ERP_CUSTOMER_CODE` names. There
is no call that takes an order back, so the advisor deletes it in the ERP's backstage. The
login, the password and the token they buy stay with the host and never reach the model.

## Live mode

`live = isinstance(erp, HttpErpClient)` in `api/main.py` is one line and everything the
fixtures stand in for hangs off it, because a fixture that leaks into a real deployment is a
number or a rule the advisor cannot tell from the agency's own.

| What | On the fixtures | Live |
|---|---|---|
| the advisor | the profile in `data/users.json` — name, 门店, 客群, quoting habits | the ERP login alone: `userName` as the display name and the department the login landed in as the location; no habits at all, and the profile is not read |
| the 同行 customer | `customer_id=4101` from `data/customers.json` | `TOUR_ERP_CUSTOMER_CODE` resolved through `customer/list` at boot to exactly one `CustomerRecord`; an unset, unknown, shared or unreadable code logs an error and leaves the deployment with no customer |
| a 团期's 同业价 | quoted for that customer | quoted for it; with no customer, `quote_source=list` and the record carries the 市场价 alone |
| a 占位 order | written through the 门店 the profile names, signed with the profile's 联系人 | written through `TOUR_ERP_STORE_NAME` and signed with the ERP login's own `userName`; unset, `add_to_cart` refuses with `未配置下单门店（TOUR_ERP_STORE_NAME）`, with no customer with `未配置下单客户（TOUR_ERP_CUSTOMER_CODE）`, and with no salesperson named rather than signing with the fixture's |
| memory | `data/memory-seed.json` seeds the advisor's habits | `data/memory-seed-empty.json`: nothing is seeded, and the advisor's own facts are what the conversation extracts |
| 政策 | `search_policies` over `data/policies.json` | `enable_policies=False`, so the tool is not registered on any path; the agency's rules are in its own knowledge base, which is not connected, and the search notes tell the model to say so and never state a rule from memory |
| the store's name | `data/routes.json`'s `store_name` | `TOUR_BRAND_NAME` |

A live catalog is also a beta catalog, so three of its shapes are guarded rather than
believed: a `minGroupSize` of 0 states no 最低成团人数, so a 团期 nobody has signed up for is
待成团 and not 已成团; a 团期 the ERP carries no `routeId` on belongs to no 线路 that can be
named, priced or booked, so it is dropped from a window read and id 0 is not looked up; and a
0 fare is 未发布 and not free, so a party with a child and no 儿童价 is `quote_source=partial`
with no total and a card that says `儿童价未发布，合计待定`.

## Try

`scripts/smoke_chat.py --vertical tour` runs the same three turns. The advisor types what
the customer said, in Chinese:

1. 10月中旬有四位客人计划去新疆伊犁，8–10天，两个大人两个小孩，孩子5岁和9岁，不要购物店。
2. 那条 10 天的喀拉峻深度线路，10 月 15 号前后有什么团？
3. 就 10/14 那个团，帮我把 4 个位置锁上。

The first turn is one `search_products` over 10-11 to 10-20: a shortlist of 纯玩 线路 with
their 起价 — 5,780 元 for the 8 日小团, 7,880 元 for the 10 日深度 line, 9,680 元 for the
五钻轻奢 one — and the 天数, 住宿标准 and 车型 that separate them, all read off the 标签 and
亮点 the ERP's editors wrote. The second turn names the 10 日深度 line, the only ten-day
route in the catalog, and is `get_product_details` on `RT-1022`: its 团期 over the searched
window and a week either side, each with 余位, 成团状态 and a 2大2小 total. The third turn
is `add_to_cart` on `DP-3017`, the 10/14 团期, which still seats four; the ERP writes a 预留
order and the reply carries the cart line counting down from thirty minutes. Asking for
more heads than the 团期 has seats is not refused: the ERP writes a 候补 order instead, and
its cart line says （候补） and does not count down.

Single prompts worth trying after those turns:

- 把 10/14 和 10/21 两个团做成清单发给客人 — `present_shortlist`: a card with both 团期,
  their 2大2小 totals, and a 客人链接 the advisor copies; the link's token never reaches
  the model.
- 这个团的退改政策怎么说 — `search_policies` over `data/policies.json`, rendered as a
  policy card the advisor reads out rather than paraphrases.
- 帮我把占位改成 3 个人 — `update_cart_item`, which this backend does not offer: the order
  is already written in the ERP, and the reply says so and sends the advisor to the ERP's
  backstage. 取消这个占位 answers the same way.

## What is specific to this example

- `api/erp_client.py`: the `ErpClient` Protocol — eight calls: `search_routes`,
  `list_departures`, `get_departure`, `search_customers`, `quote`, `create_order`,
  `list_orders`, `get_order` — with `WindowReader` beside it, the one further call a client
  offers by having it: `list_window` reads a whole date window's 团期 across every 线路 and
  department, which is how a search weighs many candidates against the window when
  `period/list` takes no route id. And the records they exchange (`RouteQuery`, `RouteRecord`,
  `DepartureRecord`, `PriceInfo`, `CustomerRecord`, `Quote`, `OrderRequest`, `OrderResult`,
  `OrderRecord`) as frozen dataclasses of plain types, so a real ERP maps onto them without
  importing this example. `ORDER_STATUS` is the ERP's own six: 预留, 占位, 确认, 取消,
  审批中, 候补. Every `ErpError` message is advisor-facing Chinese, because the executor
  relays it into the conversation unchanged.
- `api/http_erp.py`: `HttpErpClient`, the same Protocol over the agency's own HTTP API
  (`docs/erp-contract.md`). It logs in with the mobile and the password alone — the ERP takes
  no department and puts the account in its first authorised one — and caches that bearer for
  its eight hours. The reads run on it and span every department; `quote` and `create_order`
  are department-bound, so the client buys a `switch-company` token for the 团期's department
  once and keeps it, and a 401 on either buys exactly one fresh login or switch before giving
  up. `list_orders` and `get_order` run on the login token, so an order written in another
  department is not listed — a known gap. Field mapping and error mapping and nothing else:
  no seat arithmetic, no ranking, no status derivation. The ERP's status is the exception
  (`400 → ErpRefused`, `401/403 → ErpAuth`, `404 → ErpNotFound`, `429 → ErpThrottled`, `5xx`,
  a transport failure or an unreadable body `→ ErpUnavailable`) and its Chinese `message` is
  kept as written. A 404 with no JSON body at all is still a missing record: the beta answers
  a bare nginx page for a departure the catalog has no price row for, and for a write made in
  the wrong department, and that reads as not-found rather than as an outage.
- `api/mock_erp.py`: `MockErpClient`, that Protocol over `data/`. 线路 are ranked by how
  much of the query's Chinese text (character 2-grams, plus a small synonym set) the name,
  the tags and the features hold, then by price. The orders it writes live in memory: the
  ERP's own checks first (at least one head, a 2–50 character Chinese contact name, an
  eleven-digit mobile), then the seats — a party larger than what is left becomes a 候补
  order that takes no seats, anything else a 预留 that takes them. A quote or an order that
  names any department but the 团期's own is refused with 部门不匹配, which is the guard the
  real ERP enforces with a bare 404. Nothing expires here, because the ERP's own 预留 runs on
  the departure's `reserveHours`.
- `api/tags.py`: `normalize`, the ERP's free-text 行程标签 as the attributes an advisor filters
  on — `destinations`, `shopping` (`none` / `some` / `unknown`), `hotel_grade`, `family`,
  `departure_cities`, `direct_flight`, `inclusions`, `budget`. A 线路 carries some fifty tags
  the ERP auto-extracted from its itinerary attachment (斯里兰卡, 上海出发, 网评5钻酒店,
  纯玩无购物, 含签证, and forty attraction names), written in whatever words the extractor
  found and carrying its mistakes, so the facets are evidence and not a specification: an
  attribute no tag names is `unknown`, which is not a no. The vocabulary is not in the module.
  `data/tag-rules.json` holds it — for each attribute a list of `{value, any}` rules matched
  by substring in file order, plus the `negative` substrings that take a tag out of that
  attribute (印度教寺庙 is not a trip to 印度) — so product staff extend the synonyms without a
  code change. `destinations` and `departure_cities` keep every value their tags name, in tag
  order; every other attribute keeps one, the earliest rule in the file that any tag matched,
  which is what makes 无购物 beat 购物 on a line whose extractor listed a market as an
  attraction. `api/tests/test_tags.py` holds the rule families, and its coverage test runs
  them over a live `route/list` snapshot named by `TOUR_TAG_SNAPSHOT`.
- `api/tour_backend.py`: `TourBackend`, the `StorefrontBackend`. A 线路 is a family and its
  团期 are that family's variants, so search stays a shortlist of lines and the seats and
  quotes come from one details call. The advisor's destination is matched against 线路 names
  and against the attributes `api/tags.py` normalises a line's tags into, because the ERP
  catalog has no destination field; the day count, 纯玩, the hotel standard and the departure
  city are filtered on this side against those attributes for the same reason, and 亲子 orders
  the shortlist instead of filtering it. The facets are computed once per 线路 and carried on
  every record: a card's labels are the hotel standard, 纯玩, 亲子, the departure city and the
  first inclusions rather than the first four raw tags, its specs carry 酒店 / 购物 / 出发城市 /
  包含 where the tags say them, and a relaxed record's note says what the tags state
  (`标签标注四钻，要求五钻`) or that they state nothing (`未标注五钻`). The ERP's own search
  reads the name alone, and its editors do not write every destination into one — 欧洲 names
  no route and 欧洲部 sells them all — so a named query that returns fewer than two routes is
  followed by one broad read of the window, whose routes are kept when the destination is in
  a tag, a 亮点, the selling department or the departure city, and those count as exact
  matches; the read is cached per window, so the four relaxation steps below share it, as they
  share the named query and each route's departure list. A route the ERP's loose date filter
  returns with no 团期 inside the window at all is dropped from the pass, whichever pass it is,
  because a card built from one carries no date, no seat count and no price. A quoted departure costs
  two ERP calls — its detail for the 市场价 and the seat counts, `order/price` in its own
  department for the 同业价 — so a listing prices at most six of them and a route's details at
  most twelve, nearest the middle of the window first and four at a time. A variant is priced
  and totalled at the 同业价 and carries the 市场价 beside it as `market_adult_price` and
  `market_child_price`, for the customer's share page; a family's price is the cheapest 同业价
  among the departures the window priced, the cheapest 市场价 when none was quoted, and the
  线路's own 起价 when neither is known. A variant's `group_status` is derived from the ERP's
  two counts — `confirm_count` against `min_group_size` — and reads 候补 once the seats are
  gone and someone is already waiting; `quote_source` says whether the numbers on it are the
  同业价 (`customer`), the 市场价 while the 同业价 is unknown (`list`), or neither (`none`). The
  window and party the advisor last searched are kept per session: details price every
  departure for that party and state it back as `quote_party`, and `add_to_cart` splits the
  quantity into 成人 and 儿童 the same way and writes the order in the departure's own
  department, reading the departure first when this process has not seen it. `add_to_cart`
  refuses a 线路 id, because a price and a seat only exist on a date.
- A cart line is a 预留 order this conversation wrote, and its thirty-minute countdown is
  the backend's own clock, not the ERP's. `update_cart_item` and `remove_from_cart` raise
  `NotOffered` with the Chinese reason: the ERP has no cancel or amend call, so the advisor
  does both in its backstage. `get_orders` and `get_order` read the salesperson's orders back
  from the ERP, and the ERP's own status word rides along with the departure date, because
  the shared enum has no 预留.
- `api/tour_backend.py` also holds `TourToolExecutor`: `ErpRefused`, `ErpNotFound`,
  `ErpThrottled` and `ErpAuth` are relayed as their own Chinese text, so the model says what
  stands in the way instead of reporting an outage. `ErpUnavailable` is an outage and falls
  through to the base default. It also gates the order of the flow the way `gates.py` gates a
  cart write: `get_product_details` on a 线路 this session searched but has not shown as a
  card is held with `route_first`, so the advisor picks off the 线路 cards and a route's 团期
  open only after that; a 团期 id and a route id the advisor pasted are not gated, and the
  record of what was presented lives on the backend, because the executor is rebuilt each turn.
- When fewer than two 线路 are quotable, the search widens in four steps and each relaxed
  route says in Chinese what it misses: the date window by a week (`match=adjacent_date`,
  `无 10/3–10/8 团期，最近为 10/2`), then the day count by two either way, then the hotel
  standard and 纯玩 (`match=similar_route`, `未标注五钻`), then the advisor's dates for the whole
  default window, which answers a stated week the destination has no 团期 in with the nearest
  one it does (`match=adjacent_date`, `无 10/1–10/7 团期，最近为 11/2`). Each step keeps the one
  before it, and the note is measured against everything the advisor stated. The last step
  spends one route query and the departure lists behind it: its broad pass is what the steps
  above already read.
- `api/agent_config.py`: the shopping config, and the one reader of `TOUR_BRAND_NAME` and
  `TOUR_ASSISTANT_NAME`. `enable_fulfillment=False`, because the
  customer joins the group at its 集合地点; `enable_policies=not live`, because the agency's
  rules are in a knowledge base this deployment does not read; `product_id_patterns` replaced
  with the two id
  shapes this catalog has, `RT-\d+` and `DP-\d+`; the cart capped at three lines of up to
  twenty people, because every line is a real order in the ERP; 旅行社 terms added to the
  policy and order lexicons; and `domain_search_notes` stating how a Chinese date phrase
  becomes a window, that a booking takes a 团期 id, that the advisor is quoted the 同业价
  while the 市场价 belongs to the customer's share page, that a 线路's length is the record's
  `days` and never counted off the dates, that the advisor works in the ERP's own backstage
  and there is no App to send them to, and — in live mode — that a 政策 question is answered
  by saying the rule has to come from the 门店 or the ERP.
- `api/shortlist.py`: `present_shortlist`, the one presentation extension. The model names
  one to five 团期 it has seen and writes the title; the server joins each to its 线路, names
  in Chinese the ids it dropped for want of provenance, refuses the call when nothing is
  left, and mints the customer's link through `TourBackend.create_share_link`, which holds
  the token and the shortlist's ids in memory so the link's contents never pass through the
  model. The card is the step after the advisor's pick, so it is refused while any 团期 on it
  has not been shown as a `present_products` card of its own, read off the same record the
  route-first gate keeps. `POST /api/share/{token}/choose` is where the customer's page
  answers: it takes no session, because the customer is not a user here, and the 团期 they
  chose becomes an app event on the advisor's conversation, or on their other live sessions
  when that one has ended. `TOUR_SHARE_BASE_URL` is the origin the link points at,
  `http://localhost:3004` by default; the page itself is not part of this example.
- `api/main.py`: the host. It builds the ERP client from the environment, and `live =
  isinstance(erp, HttpErpClient)` is the switch the table above hangs off: it hands the backend
  the 客户编码, the 门店, the brand and the advisor's mobile, picks the empty memory seed, and
  builds the config with `live=live`. It also puts the conversation's 预留 on every
  cart payload with what is left of each thirty-minute window. A 候补 order is not a hold and
  carries no countdown.

`storefront-web/` is the advisor's workbench, Chinese throughout: 线路 cards read the
trade-offs an advisor reads out off a family's attributes (`days`, `depart_city`, `tags`,
`features`, `match`, `mismatch`), 团期 cards read a variant's (`depart_date`, `seats_left`,
`seats_total`, `group_status`, `party_quote_total`, `quote_party`) and name both of its prices
per head, the 同业价 an order is booked at (`adult_price`, `child_price`) above the 市场价 the
customer is shown (`market_adult_price`, `market_child_price`), with the party's total on the
同业价 and `quote_source` saying which of the two it was made at (or `partial`, where a fare
the party needs is 未发布 and there is no total), the bag counts each 预留
down and flips to 已过期 at zero, and `present_shortlist`, `present_guide` and `checkout`
each have a card. Its `showcase` page renders every card from a snapshot of one advisor
search, which `api/tests/test_showcase.py` holds to the live records.

The filter keys the model writes into `filters.attributes` on every search:

| Key | Value |
|---|---|
| `destination` | the customer's destination as text; the ERP matches it against 线路 names, and this side matches it against the normalised destinations, the raw tags, the 亮点, the selling department and the departure city, because the catalog has no destination field |
| `depart_from`, `depart_to` | ISO dates; the ERP's own date filter, and the window this and every later quote is made for |
| `days_min`, `days_max` | whole days; filtered here, since the ERP has no day filter |
| `adults`, `children` | the party every quote is made for, and the split `add_to_cart` writes onto the order |
| `child_ages` | pipe-separated, `5\|9`; kept on the session's `SearchContext` and filters nothing, because the ERP states no minimum age |
| `no_shopping` | `yes` keeps the 线路 whose tags say 纯玩 (`shopping=none`) and drops the ones that say 购物; a 线路 whose tags say neither falls back to the words in its name |
| `hotel_level` | 四钻, 五钻; matched against the normalised `hotel_grade`, in every spelling the ERP's editors use (五钻, 5钻, 五星, 5星, 4+5钻) |
| `departure_city` | the city the group leaves from, against the tags (上海出发, 昆明直飞) and the ERP's own `departCityName`; it is not relaxed, because a customer cannot fly from a city the line does not leave |
| `family` | `yes` sorts the 线路 whose tags claim 亲子 to the front of the shortlist and drops nothing, because a family will take a line that never wrote the word down |

## Data

- `data/routes.json`: eight 线路 over four destinations, in the ERP's own row shape — integer
  `routeId`, `routeCode`, `routeName`, `days`, `departCityName`, `companyId` (2 for the 新疆
  routes and 5 for the 青海 one, as the real account's departments run), `fromPrice`, the
  `tags` and `features` free text the editors wrote, the `itineraryTags` the ERP extracted
  from the itinerary attachment and the `periodPriceTags` budget band. There is no
  destination, hotel, vehicle, shopping or child-age *field*, because the ERP has none; those
  are tags, and `api/tags.py` is what reads them.
- `data/tag-rules.json`: the controlled vocabulary those tags are normalised with, one list
  of rules per attribute. The destinations are the agency's own catalog — Europe country by
  country off the attractions the extraction writes down (埃菲尔铁塔, 罗马, 琉森, 新天鹅堡,
  圣家堂), then 土耳其, 摩洛哥, 埃及, 迪拜, 斯里兰卡, 马尔代夫, 印度, 日本, 澳新, 邮轮 and the
  four 新疆 and 青海 values the fixtures use. A rule matches by substring, so a place name
  inside another place's name goes to whichever rule is listed first (马拉喀什 above 喀什,
  布加勒斯特 above 加勒, 都柏林 above 柏林), and a tag naming a dish or a square is a negative.
  The agency's product staff extend it without a code change.
- `data/departures.json`: sixty-six 团期, six to twelve per 线路, in the ERP's period row
  shape — `periodId`, `periodCode`, `planGuests`, `availableSeats`, `minGroupSize`,
  `confirmCount`, `reserveHours`, the route's own `companyId`, and the `priceInfo` block
  (`adultPrice`, `childPrice`, `elderPrice`, `singleRoomDiff`) the ERP carries only on a
  departure's detail, which is the 市场价.
- `data/customers.json`: three 同行 customers in the ERP's customer row shape; the demo books
  for the first, and a live deployment for the one `TOUR_ERP_CUSTOMER_CODE` resolves to.
- `data/policies.json`: 退改政策, 儿童价规则, 成团规则, 定金规则 — what `search_policies`
  answers from.
- `data/users.json`: two advisor profiles — the ERP `mobile` the deployment falls back to as
  an order's contact, the 门店 they work in, the 客群 they see, and how they quote and hold.
  `data/memory-seed.json` carries the same habits as memory, and
  `data/memory-seed-empty.json` is what live mode seeds instead: nothing.
- `data/orders.json`: empty, and no longer read. Orders live in the ERP now, and
  `get_orders` reads them back from it.

Departure dates are authored as if `dates_anchored_to` were today and shift forward by
whole weeks at load, so a demo booted any week has 团期 ahead of it; `periodId` never moves
and each `periodCode` is rebuilt from the shifted date. A 线路 id is `RT-<routeId>`; a 团期
id is `DP-<periodId>`.

`scripts/check.py` validates the other verticals' `catalog.json`-based fixtures and does
not cover these files; `api/tests/test_fixtures.py` holds them to their ids, dates, and
enumerated fields.

## The ERP contract

`docs/erp-contract.md` is the agency's API as observed on its beta environment: the login,
the paging, the error envelope, the seven reads, the one write, and the decisions Phase 4
took on top of them. `ErpClient` in `api/erp_client.py` is the seam it maps onto.
`MockErpClient` in `api/mock_erp.py` answers the eight calls from `data/` and
`HttpErpClient` in `api/http_erp.py` answers them over HTTP; what both owe — the ranking,
the window, the 候补 rule, the validation, the error classes — is stated as tests in
`api/tests/test_mock_erp.py` and `api/tests/test_http_erp.py`, so a third client is held to
the same rules.

Sessions and identity are the shared host code in [`../demo_common/`](../demo_common/): a
session id stands for one advisor.

## Diagrams

[`docs/architecture.html`](docs/architecture.html) is a single page with the system
architecture, the two-layer route-then-departure sequence, the order / 候补 / share-back
sequence, the ERP-record to `Product` mapping, the order state machine, and the
`ErpClient`-to-HTTP contract table. Open it in a browser; it has no build step.
