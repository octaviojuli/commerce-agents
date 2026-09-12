# ACME 旅行社 (tour)

The tour example runs the shopping agent for a travel agency's store advisor (顾问), who
sells 散拼团 to the customer sitting in front of them: the advisor states the need in
Chinese, the agent searches 线路 (routes), opens a 线路's dated 团期 (departures) priced for
that party, and writes a 预留 order in the agency's ERP while the customer decides. The
advisor is the user, not the traveler, and the advisor is also the ERP's salesperson: they
sign in with their own ERP account, so the reads span every department that account is
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
| `TOUR_ERP_MOBILE` | the deployment's own ERP account, a mainland mobile number; it boots the catalog snapshot and resolves the 同行 customer, and answers no advisor's session |
| `TOUR_ERP_PASSWORD` | its password; ten failed logins lock an account for fifteen minutes |
| `TOUR_ERP_CUSTOMER_CODE` | the 同行 customer's 客户编码 (the ERP's `csCode`), resolved to one customer at boot |
| `TOUR_ERP_STORE_NAME` | the 门店 a 同行 order is written through, which the ERP requires |
| `TOUR_ERP_ALLOW_PAST` | `1` lists departures that already left, for a beta with no future ones |
| `TOUR_BRAND_NAME` | the agency's own name, `ACME 旅行社` unset |
| `TOUR_ASSISTANT_NAME` | what the advisor calls the assistant, `选团助手` unset |
| `TOUR_STATE_DIR` | where the sessions, the memory and the parsed 行程附件 are written, `data/.state/` unset |
| `TOUR_MEMORY_RETENTION_DAYS` | how many days a remembered fact stays readable; unset, no age limit |

Unset `TOUR_ERP_BASE_URL` and `api/main.py` builds `MockErpClient` over `data/`, which is
what the demo runs on. Set it and `api/main.py` builds `HttpErpClient` on those credentials,
and the example is then talking to a live ERP: the third smoke turn below writes a real 预留
order on it, for the customer `TOUR_ERP_CUSTOMER_CODE` names. There is no call that takes an
order back, so the advisor deletes it in the ERP's backstage. Every credential and every token
stays with the host and never reaches the model.

## Login

Each advisor signs in with their own ERP mobile and password. The pair goes once to the ERP's
own `POST /login` and nothing here keeps it: what stays is the token the ERP answered with, in
`api/advisors.py`'s `AdvisorRegistry`, keyed by the ERP employee behind the login
(`erp-{userId}`). A password is not stored, not logged and never reaches the model; a login
writes one line — `advisor login ok user=erp-6`, or `advisor login failed status=401`.

    POST /api/login    {mobile, password} → {session_id, advisor{user_id, name, department, departments}}
    GET  /api/advisor   whether that advisor still holds a token, and who the ERP says they are
    POST /api/logout    drop the token; the conversation stays
    POST /api/sessions/new  another conversation for the same advisor → {session_id}; 401 once the token is gone

A refusal is the ERP's own: 401 with its Chinese message for a wrong password, 429 for the
throttle it owns — ten failures per mobile and IP lock the account for fifteen minutes, so
nothing here counts attempts — and 500 for an ERP that cannot be reached.

A token lives the eight hours the ERP gives it, which it does not renew, and lives in this
process alone, so a restart ends every login. `GET /api/advisor` answers `logged_in: false`
from then on, which is what puts the workbench back on its sign-in screen. A session with no
live login behind it reads nothing: `search_products`, `get_product_details` and `add_to_cart`
come back with `请先登录 ERP 账号`, and a token that ran out mid-conversation with
`登录已过期，请重新登录`, both relayed into the conversation the way every other ERP rule is;
a route that runs into either answers 401 with the same words rather than reporting an outage.
The session itself survives all of it — it is on disk under the same advisor — so signing in
again picks up the history.

Sessions and memory are keyed by that ERP employee, so two advisors of one deployment share
neither: their conversations, the facts remembered about them, and the ERP account their reads
and their orders go out on are their own, and an order is signed with the name and the mobile
they signed in with. What they do share is the process's own catalog reads — the boot snapshot
and the 线路 and 团期 records cached beside it — which are the agency's own records and carry
no token.

The deployment's own account answers no session. It has two jobs left, both the deployment's:
the boot listing snapshot the workbench's home page reads, and resolving
`TOUR_ERP_CUSTOMER_CODE` to the 同行 customer. So in live mode the demo's own
`POST /api/session`, which binds a session to whatever principal the caller names, answers 403
`请通过 /api/login 登录` (a middleware in `api/main.py`); on the fixtures it stays, because the
smoke script and the suite start their sessions with it. The fixtures hold no passwords at all:
`FixtureAdvisorRegistry` signs in any advisor `data/users.json` names, whatever password the
workbench sent, so the sign-in flow is the same on both.

## Live mode

`live = isinstance(erp, HttpErpClient)` in `api/main.py` is one line and everything the
fixtures stand in for hangs off it, because a fixture that leaks into a real deployment is a
number or a rule the advisor cannot tell from the agency's own.

| What | On the fixtures | Live |
|---|---|---|
| the advisor | the profile in `data/users.json` — name, 门店, 客群, quoting habits — signed in with any password | their own ERP login alone: `userName` as the display name and the department that login landed in as the location; no habits at all, and the profile is not read |
| the 同行 customer | `customer_id=4101` from `data/customers.json` | `TOUR_ERP_CUSTOMER_CODE` resolved through `customer/list` at boot to exactly one `CustomerRecord`; an unset, unknown, shared or unreadable code logs an error and leaves the deployment with no customer |
| a 团期's 同业价 | quoted for that customer | quoted for it; with no customer, `quote_source=list` and the record carries the 市场价 alone |
| a 占位 order | written through the 门店 the profile names, signed with the profile's 联系人 | written through `TOUR_ERP_STORE_NAME` and signed with the name and the mobile the advisor signed in with; unset, `add_to_cart` refuses with `未配置下单门店（TOUR_ERP_STORE_NAME）`, with no customer with `未配置下单客户（TOUR_ERP_CUSTOMER_CODE）`, and with no salesperson named rather than signing with the fixture's |
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

`scripts/smoke_chat.py --vertical tour` runs the same five turns. The advisor types what
the customer said, in Chinese:

1. 10月中旬有四位客人计划去新疆伊犁，8–10天，两个大人两个小孩，孩子5岁和9岁，不要购物店。
2. 那条 10 天的喀拉峻深度线路，10 月 15 号前后有什么团？
3. 就 10/14 那个团，帮我把 4 个位置锁上。
4. 就这条线给客人做个定制方案，先把逐日行程摆出来。
5. 第 5 天多住一晚，其余不动。

The first turn is one `search_products` over 10-11 to 10-20: the three 纯玩 线路 whose
documents meet it, with their 起价 — 5,580 元 for the 8 日小团, 7,580 元 for the 10 日深度 line,
9,480 元 for the 五钻轻奢 one — the 天数, 住宿标准, 购物店 and 逐日路线 that separate them, all
read off the agency's own 线路文档, and the 团期 each sells in those dates beside them, because
the advisor gave the customer's window. The second turn names the 10 日深度 line, the only
ten-day route in the catalog, and is `present_departures` on `RT-1022`: its 团期 in the
searched window, each with its weekday, whether it is 可报名, 已成团 or 满员 for this party,
and the 同业价 per adult. The third turn is `add_to_cart` on `DP-3017`, the 10/14 团期, which
still seats four; the ERP writes a 预留 order and the reply carries the cart line counting
down from thirty minutes. Asking for more heads than the 团期 has seats is not refused: the
ERP writes a 候补 order instead, and its cart line says （候补） and does not count down.

The fourth turn is `present_itinerary` on the same 线路 with `DP-3017` as the baseline 团期: v1
of a new plan, the route's ten 第N天 specs restated a sentence or two a day, the 10/14 团期's own
figures as the reference price, and 待计调确认 in the note of any day the customer's ask goes
past what the line carries. The fifth turn asks for a night on day 5, which is v2 of that plan:
the tool's answer to the fourth turn names the plan id and the version, which is how the model
knows what to pass back, and the reply names the version the store assigned. A version re-sends
every day, so `agent_config.py` gives a turn `max_tokens=8192` rather than the repo's 2048: a
provider that reasons before it answers spends the same budget on the reasoning.

Single prompts worth trying after those turns:

- 这条线的逐日行程发我看看 — `present_route_days`: the whole 行程 of the 线路 named, day by
  day, with the flights, the 费用包含 and the 购物店 under it.
- 把 10/14 和 10/21 两个团做成清单发给客人 — `present_shortlist`: a card with both 团期,
  their 2大2小 totals, and a 客人链接 the advisor copies; the link's token never reaches
  the model.
- 这个团的退改政策怎么说 — `search_policies` over `data/policies.json`, rendered as a
  policy card the advisor reads out rather than paraphrases.
- 帮我把占位改成 3 个人 — `update_cart_item`, which this backend does not offer: the order
  is already written in the ERP, and the reply says so and sends the advisor to the ERP's
  backstage. 取消这个占位 answers the same way.

## What is specific to this example

- `api/erp_client.py`: the `ErpClient` Protocol — nine calls: `search_routes`,
  `list_departures`, `get_itinerary`, `get_departure`, `search_customers`, `quote`,
  `create_order`, `list_orders`, `get_order` — with `WindowReader` beside it, the one further
  call a client offers by having it: `list_window` reads a whole date window's 团期 across every 线路 and
  department, which is how a search weighs many candidates against the window when
  `period/list` takes no route id. And the records they exchange (`RouteQuery`, `RouteRecord`,
  `Itinerary`, `ItineraryDay`, `DepartureRecord`, `PriceInfo`, `CustomerRecord`, `Quote`,
  `OrderRequest`, `OrderResult`, `OrderRecord`) as frozen dataclasses of plain types, so a real ERP maps onto them without
  importing this example. `ORDER_STATUS` is the ERP's own six: 预留, 占位, 确认, 取消,
  审批中, 候补. Every `ErpError` message is advisor-facing Chinese, because the executor
  relays it into the conversation unchanged.
- `api/http_erp.py`: `HttpErpClient`, the same Protocol over the agency's own HTTP API
  (`docs/erp-contract.md`). `from_login` builds one on a mobile and a password alone — the ERP
  takes no department and puts the account in its first authorised one — and caches that bearer
  for its eight hours; `with_token` builds one on a token a login already bought and no
  password, which is the client an advisor's session reads through, and past that token's own
  expiry it raises `登录已过期，请重新登录` rather than logging in again behind the advisor. The
  reads run on whichever token the client holds and span every department; `quote` and
  `create_order` are department-bound, so the client buys a `switch-company` token for the 团期's
  department once and keeps it, and a 401 on either buys exactly one fresh login or switch
  before giving up. `list_orders` and `get_order` run on the login token, so an order written in another
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
- `api/route_doc.py`: `RouteDoc`, one 线路 as a structured document — the static half of the
  catalog (`docs/route-doc.md`): identity, `summary`, the attachment's `cover`, the flights,
  the `days` (places, KM figures, 交通, the night, the 【景点】 with 含门票/外观/购物/自费/赠送,
  the three meals, the hotel, the programme whole), the 包含/不含 lists, 购物 and 自费, the
  policy sentences, and `source` and `quality` saying which attachment it was read from and
  what a reviewer should check. `data/route-schema.json` is its JSON Schema.
- `api/pdf_source.py`: a .pdf 行程附件 as the lines a .docx gives, through `pdftotext`
  (poppler); a scanned .pdf is refused as `ImageOnlyPdf`.
- `api/route_parser.py`: the .docx or .pdf 行程附件 read into a `RouteDoc` by rule, on top of
  `itinerary_source`'s days; `score` is the completeness the batch ranks the catalog by.
- `api/route_docs.py`: `RouteDocStore`, the documents the runtime reads — `route-docs/published/`
  (reviewed) over `route-docs/selected/` (the review round's draft) — and what the backend
  reads off one ahead of the tags: the 行程, the card's 参考航班 / 购物店 / 费用包含 / 单房差
  规格, the `doc` / `shopping_stops` / `meals_included` / `doc_hotel_grade` / `flights`
  attributes, and the 纯玩 and hotel-standard filters.
- `api/catalog.py`: `Catalog`, the reviewed documents as the catalog a search runs over.
  `RouteFacts` is one document as a search reads it — the countries, the 线路系, the length,
  the city it leaves from, the 酒店标准 and its 钻 grade, the 购物店 and 自费 counts, the 门票
  and 赠送 counts, the places its days pass through, the sights it names, the cover's first
  三 亮点, whether a reviewer signed it, and the feature words (观鲸, 一价全含, 国泰, 纯玩 …)
  that tell one version of a trip from another — with the 起价 and the cover photo of the ERP
  catalog row beside them. `match` is the whole filter; `chips` groups the matches by 目的地,
  天数, 出发城市, 出发月份, 酒店标准, 纯玩, 特色 and 起价, each value one the model sends back
  as a filter; `ambiguous` and `differences` say when a handful of matches are one trip sold
  several ways and what tells them apart.
- `api/itinerary_source.py`: where a 线路's day-by-day 行程 comes from. A details record carries
  行程来源 and one spec per day — the day's title, its programme cut to 200 characters, its
  住宿 and its 用餐 — for at most 20 days, and a 线路 whose days could not be read says 无 there
  instead of saying nothing. The days are the ERP's own where `get_itinerary` answers with any,
  and otherwise are parsed out of the .docx 行程附件 the catalog links: `parse_docx` walks the
  document in order, reads `第 N 天` headers — or `DAY-N` where the file has no Chinese header at
  all — and stops at the 费用/须知 sections after the last day. `TourBackend` reads one 线路's
  行程 once per process behind a per-route lock, and `cache_read` / `cache_write` keep the
  reading as one owner-only JSON file per 线路 under `TOUR_STATE_DIR`, sent back as an ETag so an
  unchanged attachment is not downloaded again; a fetch or a parse that fails is logged and
  leaves the record saying 无, because a details read must not fail over an unreadable file.
- `api/advisor_memory.py`: the memory's subject. The core extracts a customer's one live
  undertaking as `current_project`; an advisor's conversations are their customers' trips one
  after another, and a trip kept as the advisor's own would be read into the next customer's
  conversation. So the extraction runs under a prompt rendered for the advisor — what
  qualifies is a standing habit of their own work — and `advisor_write_filter` refuses a
  customer's trip (a party size, a date on a journey, a key the customer prompt would use) on
  every path to the store: the extraction pass, the model's memory tool, the memory editor.
- `api/advisors.py`: `AdvisorRegistry`, the logins this process holds — `AdvisorLogin` per ERP
  employee, carrying the `with_token` client their calls go out on, their name, their mobile,
  the department the login landed in and the moment the token dies. `login` forwards the mobile
  and the password to the ERP once through a client built for that one call, keeps the token
  and closes the client that held the password; `get` stops naming an advisor whose token has
  run out, and `logout` drops one. `FixtureAdvisorRegistry` beside it is the demo's: the
  advisors `data/users.json` names, all reading through the one `MockErpClient`, and any
  password, because a fixture has none to check against. The section above is what a login is
  and what it is not.
- `api/private_lines.py`: which 线路 are not on general sale. The catalog carries, beside what
  anyone may sell, the 包团 a customer chartered, the 会销 one salesperson runs and the 定制
  built for one party, and the ERP has no field saying which is which. `PrivateLineRules`
  reads the name for the words `data/private-lines.json` lists (包团, 会销, 定制, 包机, 团建,
  考察, 独立成团), and the ERP's `saleType` ahead of the name once the ERP carries it. A private
  line is not a search result and not in the boot listing snapshot; it opens by its id and by
  its full name, and its record then carries `line_type` so the model says what it is.
- `api/tags.py`: `normalize`, the ERP's free-text 行程标签 as attributes — `destinations`,
  `shopping` (`none` / `some` / `unknown`), `hotel_grade`, `family`, `departure_cities`,
  `direct_flight`, `inclusions`, `budget`, `region`. The search no longer reads them: what a
  线路 contains is its document's word. They are what a details record's 规格 and the boot
  listing snapshot are built from, for a 线路 the advisor opened or pasted. A 线路 carries some fifty tags
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
- `api/tour_backend.py`: `TourBackend`, the `StorefrontBackend`. `_erp_for` is what every read
  and the one write go out on: the client the session advisor's own login left in the registry,
  resolved per call, with `请先登录 ERP 账号` where there is no live login and the deployment's
  own account lent to nobody. A 线路 is a family and its
  团期 are that family's variants, so search stays a shortlist of lines and the seats and
  quotes come from one details call or from the 团期卡. The search itself is the documents
  (`api/catalog.py`) and not the ERP's catalog: the destination is matched against a line's
  countries, its 线路系, its name, the department that sells it, the places its days pass
  through and the sights it names, and the day count, 纯玩, the hotel standard, the departure
  city, the 线路系 and the 起价 ceiling filter on what the document states; 亲子 orders the
  shortlist instead of filtering it. A 线路 the agency has no document for is not searched at
  all, and a request the documents do not meet returns nothing rather than something near it.
  A card is the document's own fields — `route_code`, `days`, `nights`, `depart_city`,
  `countries`, `region`, `airline`, `hotel_standard`, `meal_standard`, `shopping_stops`,
  `optional_count`, `ticket_count`, `gift_count`, `places`, `highlights`, `doc` — with
  `attachment` where the catalog links one, and its labels are 纯玩 or 购物店N家, the hotel
  standard, the airline and 已复核 or 解析稿. The ERP is asked for the dynamic half alone: the
  window's 团期 are read once for the whole search. A stated window is a condition and not a
  decoration: every match is weighed against it, a line that runs in it is `match=exact` and
  carries the dates as `departures`
  (`2026-10-03:可报名|2026-10-05:已成团|2026-10-08:满员`, 截止 once the date has passed) with the
  window as `departures_window`, and a line that runs in none of them is `match=adjacent_date`
  with an empty `departures`, the `nearest_departure` a second read over the next 180 days
  found — one read for all such lines together — and a `mismatch` the advisor reads back
  (`10/01–10/07 无团期，最近团期 10/19`). Such a line is offered and not dropped, because the
  advisor may still sell that date, and the lines that do run in the dates rank ahead of it. The ERP catalog row behind a document is read once, for the
  起价 and the cover photo; a matched line the process has no row for is still offered, at
  起价未知. A quoted departure costs  because a card built from one carries no date, no seat count and no price. A quoted departure costs
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
- The two cards the documents alone answer are `api/route_days.py` and the 团期 half of
  `api/departures.py`. `present_route_days` is one 线路's 逐日行程, whole: the heading the
  document states, the flights of the first day that flies and of the last, every day with its
  places, its sights and their 门票, the three meals, the night's hotel, and the 费用包含,
  费用不含, 购物店, 自费项目, the policy sentences and the attachment's own file name under
  them. It is refused for an id this session has not shown and for a 线路 with no document.
  `present_departures` is the 团期卡: the ERP's own dates for one 线路 inside a window — the
  advisor's, else the one the conversation is working in, else the default — capped at twelve
  nearest its middle, each row its date, its weekday, 可报名 / 已成团 / 满员 / 截止 for this
  party, and the 同业价 per adult where the ERP quoted one. Every 团期 it shows enters the
  session's provenance and counts as presented, so a 占位 order and a 分享清单 may name it.
- `api/store.py`: `SqliteSessionStore`, the shared `SessionStore` with its six storage
  methods over one SQLite file, and the two reads the history routes answer from —
  `summaries`, one line per conversation of one advisor, and `transcript`, its messages as
  stored. `display_messages` beside them is the view a person is shown, which is what drops
  the tool exchange and the app-event notes. The section above is what the file holds and how
  a restart reads it. The same file also holds the 定制方案 the advisor builds on a published
  线路 — one row per plan and one row per version, each version under its own share token —
  because a plan outlives the conversation it was built in.
- `api/plans.py`: what a plan and one version of it are made of, and the reading of a version
  against the one before it. `diff_days` aligns two versions on what each day says rather
  than on where it sits, so a day inserted in the middle is one added day and not a
  renumbering of every day after it; `summarize` is that diff in the one Chinese line the
  advisor reads above a version, and `handoff_text` is the plain text they copy to the
  agency's 计调, which is who prices a custom plan.
- `api/agent_config.py`: the shopping config, and the one reader of `TOUR_BRAND_NAME`,
  `TOUR_ASSISTANT_NAME` and `TOUR_MEMORY_RETENTION_DAYS`. `enable_fulfillment=False`, because the
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
- `api/attachments.py`: the 行程附件 as a download the advisor asked for. The catalog links
  each 线路's itinerary document on a public object store under a hashed file name and carries
  the agency's own name beside it. `present_attachments` is the presentation extension the
  model calls only when the advisor asks for a line's 行程单: it names the ids, the server joins
  each to the file's name from the record the session saw, drops the ids it never saw or that
  link no document, and refuses a card with nothing on it. A tap on the card asks
  `GET /api/attachments/{product_id}` (a 线路 or one of its 团期) on the advisor's session, and
  this host reads the file and answers it under that name, with a 30 MB ceiling. The route
  record's `attachment` attribute tells the model a document exists; no card offers it unasked.
- `api/focus.py`: `present_focus`, the 聚焦卡. A search that matched more lines than a
  shortlist, one that matched a handful of versions of one trip, or one that matched nothing
  at all leaves an overview on the backend; the model writes one narrowing question and names
  the dimension (目的地, 天数, 出发城市, 出发月份, 酒店标准, 纯玩, 特色, 起价), and the card's
  chips are that overview's groups with their counts. A tap holds a chip rather than sending it —
  several in a group and across groups — and the bar under the rows sends the whole pick as one
  `只看 <维度>：<值>、<值>；<维度>：<值>` in the advisor's own words, or 不限条件 for the lines as
  they stand. Up to three results may stand on the card as a
  foothold while the match is twelve or fewer. The card is refused when the last search fits a
  shortlist. Up to twelve matches the cards and the chips go together and nothing is held;
  above twelve `TourToolExecutor` holds `present_products` (`FOCUS_FIRST_GATE`) the way it
  holds a route opened before its card, and the card then carries the question alone. An
  overview over versions of one trip holds nothing, because those lines are the cards the
  question is asked over.
- `api/shortlist.py`: `present_shortlist`, the presentation extension for the customer's list. The model names
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
- `api/itinerary.py`: `present_itinerary`, the 定制方案 the advisor builds on one published
  线路. The model writes the days and names the baseline; the server does the rest. The first
  call creates the plan and is v1, the baseline restated; every later call sends the whole
  plan again with `plan_id` and only the asked days changed, and the server numbers the
  version, reads it against the version it was written against (`base_version`, the latest
  unless it names one), and marks each day 新增, 修改 or 删除 off that reading. The call is
  refused unless the baseline is a 线路 the advisor has been shown — a `present_products` card,
  or a 包团 or 定制 line opened by id, which was never a search result — and a plan is one
  conversation's: 3 plans per conversation, 30 versions per plan, and a `plan_id` from another
  conversation is refused. `departure_id` names the 团期 the reference price is read off, and
  one that is not this 线路's is dropped and named to the model rather than refusing the plan;
  a plan has no price of its own, because the 定制 difference is the 计调's to quote.
  `erp_route_id` links the 线路 the agency built in the ERP and adds no version. Each version
  carries its own share token, so a customer sent v2 keeps reading v2, and the card leaves the
  advisor with two texts: the customer's link and `plans.py`'s `handoff_text` for the 计调.

      GET  /api/plans/{plan_id}                    the caller's own plan and every version of it
      GET  /api/plans/{plan_id}/versions/{n}       one stored version as the card it was sent as
      GET  /api/share/plan/{token}                 the customer's read of the version their link names, 市场价 only
      POST /api/share/plan/{token}/respond         {choice: ok|question, text?} → an app event on the advisor's conversation

- `api/main.py`: the host. It builds the ERP client from the environment, and `live =
  isinstance(erp, HttpErpClient)` is the switch the table above hangs off: it hands the backend
  the 客户编码, the 门店, the brand and the advisor's mobile, picks the empty memory seed, and
  builds the config with `live=live`. It creates `TOUR_STATE_DIR`, builds the two stores in it
  — the sessions and the memory — hands the same directory to the backend as the 行程附件
  cache, and holds the two history routes over the session one. It builds the registry the advisors sign in to, holds `POST /api/login`,
  `POST /api/logout` and `GET /api/advisor` over it, and in live mode installs
  `install_login_guard`, the middleware that answers the demo's own `POST /api/session` with a
  403. It also puts the conversation's 预留 on every cart payload with what is left of each
  thirty-minute window. A 候补 order is not a hold and carries no countdown.

`storefront-web/` is the advisor's workbench, Chinese throughout: 线路 cards read what the
reviewed document states about a line (`days`, `nights`, `depart_city`, `countries`, `airline`,
`hotel_standard`, `meal_standard`, `shopping_stops`, `optional_count`, `ticket_count`,
`gift_count`, `places`, `doc`, `match`, `mismatch`) under the agency's own poster (`image_url`,
with a wash in its place where a line carries none) and, on a dated search, the 团期 it sells in
the window (`departures`, `departures_window`) — or, where the window holds none, that window and
the nearest date beside it (`match` `adjacent_date`, `nearest_departure`), 团期 cards read a variant's (`depart_date`, `seats_left`,
`seats_total`, `group_status`, `party_quote_total`, `quote_party`) and name both of its prices
per head, the 同业价 an order is booked at (`adult_price`, `child_price`) above the 市场价 the
customer is shown (`market_adult_price`, `market_child_price`), with the party's total on the
同业价 and `quote_source` saying which of the two it was made at (or `partial`, where a fare
the party needs is 未发布 and there is no total), the bag counts each 预留
down and flips to 已过期 at zero, and `present_focus`, `present_attachments`, `present_route_days`,
`present_departures`, `present_shortlist`, `present_itinerary`, `present_guide` and `checkout`
each have a card. The `route_days` card draws one 线路 as its 逐日行程: every day of the
document open at once, the 去程 and 回程 as flight strips, and the 费用包含, 费用不含, 购物店,
自费项目 and 须知 folded under them. The `departures` card draws the 团期 one 线路 sells inside a
window, each row its date, what it is open for and its 同业价. The `itinerary` card draws one version of a
定制方案 — the days with what this version did to each of them, the baseline's own figures, the
customer's link and the 计调's copy — and `app/p/[token]/page.tsx` is the customer's own page
for the version their link names, which reads `GET /api/share/plan/{token}` and answers on it.
Its `showcase` page renders every card: the 线路 and 团期 records of a snapshot of one advisor
search, which `api/tests/test_showcase.py` holds to the live records, and the document cards
from fixtures written for them.
The page before it is the login (`components/LoginView.tsx`, `lib/auth.ts`): the advisor's own
ERP 手机号 and 密码, which `POST /api/login` signs in and answers with the session it started.
The browser remembers that session id and nothing else, so a reload asks `GET /api/advisor`
whether it is still an advisor's before it resumes anything; a `logged_in: false`, or a 401 from
any other request, drops the id and puts the login screen back up with 登录已过期 on it. 退出登录
in the app bar is `POST /api/logout`. The app bar carries the advisor's name and the 门店 the
ERP gave them, which is where the greeting's eyebrow used to say it.
Its 历史会话 drawer (`components/SessionPanel.tsx`, `lib/sessions.ts`) lists the advisor's
earlier conversations from `GET /api/sessions`, reopens one by sending that session's id
with the stored transcript replayed above the live one, and remembers the last session per
browser, reopening it when the advisor's own list still carries it. 新会话 asks
`POST /api/sessions/new` for another conversation for the advisor already signed in; a 401
from it is a login that is over, and puts the sign-in screen back up.

The filter keys the model writes into `filters.attributes` on every search:

| Key | Value |
|---|---|
| `destination` | the customer's destination as text, matched against the document's countries, its 线路系, the line's name, the department that sells it, the places its days pass through and the sights it names; places joined with · must all be on the line |
| `depart_from`, `depart_to` | ISO dates; the window every match is weighed against, and the one this and every later quote is made for. A line with no 团期 in it is `match=adjacent_date` and names its `nearest_departure` |
| `days` | exact lengths (`8\|12`), against the document's own 天数 |
| `days_min`, `days_max` | a span of whole days, against the same |
| `adults`, `children` | the party every quote is made for, and the split `add_to_cart` writes onto the order |
| `child_ages` | pipe-separated, `5\|9`; kept on the session's `SearchContext` and filters nothing, because the ERP states no minimum age |
| `no_shopping` | `yes` keeps the 线路 whose document lists no 购物店 at all |
| `hotel_level` | 四钻, 五钻; matched against the grade the document's 酒店标准 states, in every spelling the attachments use (五钻, 5钻, 五星, 5星) |
| `feature` | a word that tells one version of a trip from another (观鲸, 一价全含, 国泰), read off the line's name and its sights |
| `months` | `2026-10\|2026-11`; the months the customer named, which become the window — a line is a match for the dates when it has a 团期 in any of them |
| `departure_city` | the city the group leaves from, as the document's `summary` states it |
| `family` | `yes` sorts the 线路 whose document claims 亲子 to the front of the shortlist and drops nothing, because a family will take a line that never wrote the word down |
| `region` | a 线路系 (德法意瑞, 西欧多国, 英爱, 西葡, 北欧, 东欧巴尔干, 意大利一地 …), matched either way round against the document's own `region`; a line filed nowhere is not admitted |
| `price_min`, `price_max` | a floor and a ceiling on the ERP's 起价 in yuan, from the 起价 bands the advisor tapped; a 起价 it has not published (0) is under neither |

Matches are ranked, not left in the catalog's order: the reviewed documents first, then a
line of exactly the length asked for, then the lower published 起价, then the name. Every
result carries `catalog_matches`, the number of 线路 that met the request. Above five matches
the executor appends a 目录概览 to the tool result — the total and the matches grouped by
目的地 (one value per line: the 线路系 it is filed under, else its countries joined), 天数,
出发城市, 出发月份 (counted off the 团期 the search read, bare for the year the advisor is
working in), 酒店标准, 纯玩, 特色 (观鲸, 一价全含, 国泰 — the words that tell one version of a
trip from another) and 起价, each value one the model sends back as a filter. With dates stated
the head says how many of the matches run in them. Between six
and twelve matches that is cards and chips together: the cards are the answer and the model
ends the reply with a `present_focus` question over those groups. Above twelve it is the
question alone, and a shortlist over that overview is held until it has been asked.
The advisor may tap as many chips as they like: the workbench sends one message
(`只看 目的地：德法意瑞、法意瑞；天数：12 天；出发月份：10月、11月`, or
`不限条件，直接看这些线路`), and the model sends each group as one filter with its values joined
by `|` — alternatives inside a group, conditions between them. `destination`, `region`, `days`,
`departure_city`, `hotel_level`, `feature` and `months` all read that way.

Two to five matches that are the same countries over the same number of days are one trip
sold several ways: the overview stands for those too, names the dimensions that differ first,
and the model asks which the customer wants rather than choosing. A search that matched
nothing leaves the whole catalog's own groups instead, so the model can offer a direction it
does sell.

## Data

- `data/routes.json`: eight 线路 over four destinations, in the ERP's own row shape — integer
  `routeId`, `routeCode`, `routeName`, `days`, `departCityName`, `companyId` (2 for the 新疆
  routes and 5 for the 青海 one, as the real account's departments run), `fromPrice`, the
  `tags` and `features` free text the editors wrote, the `itineraryTags` the ERP extracted
  from the itinerary attachment and the `periodPriceTags` budget band. There is no
  destination, hotel, vehicle, shopping or child-age *field*, because the ERP has none; those
  are tags, and `api/tags.py` is what reads them.
- `data/route-docs/published/`: the fixture catalog's own 线路文档, one per 线路 in
  `routes.json` — the documents the search runs over when the deployment has none of its own
  under `TOUR_STATE_DIR`. An agency's are written there by `scripts/parse_attachments.py` and
  moved into `route-docs/published/` once the product staff have checked them.
- `data/route-schema.json`: the JSON Schema of `RouteDoc`, written by
  `scripts/parse_attachments.py --schema` and held to the model by `api/tests/test_route_doc.py`.
- `data/private-lines.json`: the words a 线路 not on general sale carries in its name, and
  the `saleType` values that mean public once the ERP sends the field; `api/private_lines.py`
  reads it.
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
- `data/itineraries.json`: the baseline 行程 of the three 线路 the demo scripts and the showcase
  open — RT-1021, RT-1022, RT-1024 — one entry per day of the route with its 住宿 and its 用餐,
  in the shape `route/itinerary` is asked for. A 线路 the file writes none for has none, as most
  of a live catalog does.
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
`MockErpClient` in `api/mock_erp.py` answers the nine calls from `data/` and
`HttpErpClient` in `api/http_erp.py` answers them over HTTP; what both owe — the ranking,
the window, the 候补 rule, the validation, the error classes — is stated as tests in
`api/tests/test_mock_erp.py` and `api/tests/test_http_erp.py`, so a third client is held to
the same rules.

Sessions and identity are the shared host code in [`../demo_common/`](../demo_common/): a
session id stands for one advisor.

## Sessions and memory on disk

An advisor keeps the workbench open all day and the API restarts under them, so this is the
one example whose sessions are not in the process. `TOUR_STATE_DIR` — `data/.state/` unset,
gitignored, created at boot — holds all three:

| File | Written by | What is in it |
|---|---|---|
| `sessions.sqlite` | `api/store.py`'s `SqliteSessionStore` | one row per conversation (the advisor it belongs to, the session state, the version a write is checked against) and one row per message |
| `memory-store.json` | the core's `JsonFileMemoryStore` | the facts the post-turn extraction pass keeps about each advisor |
| `itineraries/{routeId}.json` | `api/itinerary_source.py`'s `cache_write` | one 线路's days as parsed out of its 行程附件, with the URL and the ETag they were read at |
| `route-docs/{routeId}.json`, `index.json`, `REPORT.md`, `selected/` | `scripts/parse_attachments.py` | one `RouteDoc` per public .docx or .pdf line, the ranking, and the first review round's pick; the agency's product data, never committed |
| `route-docs/published/{routeId}.json` | the agency's product staff | the reviewed documents (`quality.reviewed_by` set), read at boot ahead of `selected/` |

`SqliteSessionStore` is a subclass of `demo_common.sessions.SessionStore` with the six
storage methods over one SQLite file, which is what that class's docstring describes a
deployment doing; nothing about a record, a route or the request header changes, and the
other examples still keep their sessions in the process. `write_state` is the compare-and-set
the base class relies on, run inside a transaction that takes the write lock first, so two
processes on one file cannot overwrite each other. The file stays in WAL mode, so a restarted
API, a second worker or a script reads the sessions the last process wrote.

Resuming a conversation needs no route: the client sends its id in `X-Session-Id` as it does
for a live one, and the store loads it. Two reads serve the history list:

    GET /api/sessions                        the caller's own conversations, newest first, at most 50
    GET /api/sessions/{id}/messages          one of them, as a person reads it

Both answer for the caller alone — the caller is the session id in the header and nothing
else — and a conversation belonging to another advisor is a 404. The messages route is a
display view: the advisor's turns and the assistant's replies as strings, with the tool
exchange and the app-event notes the host writes for the model left out, so the count on a
list row is the number of lines the transcript route returns.

`scripts/review_sessions.py --state-dir <dir> [--since YYYY-MM-DD] [--out report.md]` reads both
files and prints one Markdown report for the agency's staff: what the advisors searched for and
with which conditions, the searches that came back empty or only after the backend relaxed them,
the 定制方案 they built and the days those wrote 待计调确认 into, the calls a gate or the ERP
refused, the facts the memory file holds, the destination words
`data/tag-rules.json` has no rule for, and the answers whose wording is worth a second look. The
方案 section is the one read off the plan tables rather than the conversations, because a plan's
id is the server's and a call carries it only once the advisor revises the plan. It
reports only — nothing is written, and the words it lists are candidates to review, because a
change to the vocabulary or the prompt is made by hand afterwards. The sections and the parsing
are `api/review.py`, which is where the tests read them.

`scripts/parse_attachments.py [--select N] [--limit N] [--include-private] [--out DIR]` reads the
catalog's public .docx and .pdf 行程附件 into one `RouteDoc` per 线路 under the state directory's
`route-docs/`, with `index.json` and `REPORT.md` ranking them by completeness and, with
`--select`, the N most complete copied into `selected/` for the agency's product staff to
review (`docs/route-doc.md`); `--schema` writes `data/route-schema.json` and stops.

`python scripts/run_demo.py tour --fresh-memory` deletes `memory-store.json` and leaves the
conversations beside it alone. The fixture seed in `data/memory-seed.json` is loaded at every
boot, so on the fixtures a seeded fact the advisor retracted is back after a restart; live
mode seeds nothing and only the advisor's own extracted facts are in the file.

The memory subject and a session's owner are both the storefront's `user_id`, and that is the
ERP employee behind the advisor's own login (`erp-{userId}`, or their `data/users.json` id on
the fixtures). So the isolation is the login's: one advisor's conversations and remembered
facts are theirs, another advisor of the same deployment sees neither, and no route reads an
identity from a request — the session id in the header is the only thing that names anybody.

## Diagrams

[`docs/architecture.html`](docs/architecture.html) is a single page with the system
architecture, the two-layer route-then-departure sequence, the order / 候补 / share-back
sequence, the ERP-record to `Product` mapping, the order state machine, and the
`ErpClient`-to-HTTP contract table. Open it in a browser; it has no build step.
