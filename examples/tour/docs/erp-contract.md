# The real ERP behind the tour vertical (Phase 4)

The agency's B2B ERP exposes an "AI CLI" API. This page is the contract `HttpErpClient`
maps onto `ErpClient`, as observed on the beta environment on 2026-09-07, and the
decisions that shape Phase 4. Fictional example values throughout.

## Access

- Base URL `{backend}/aicli`; beta `http://beta-b2b-backend.hl1j.com/aicli`; production
  over HTTPS. JSON both ways; `Content-Type: application/json` on POST.
- `POST /login` `{mobile, password}` → `{token, tokenType: "Bearer", expiresIn: 28800,
  expiresAt, userInfo{userId, userName, companyId, companyName}, companies[]}`. **The login
  takes no department**: any `companyId` sent is ignored, and the server puts the account in
  its first authorised department (lowest id) and answers with that one in `userInfo` and
  with all of them in `companies`. The token lives 8 hours, is not renewed, and is bound to
  that one department. Ten failed logins per mobile and IP lock the account for 15 minutes.
- `POST /switch-company` `{companyId}` with the current bearer → a new token bound to that
  department, plus the full `companies` list. The old token stays valid until its own expiry,
  so a client can hold one token per department at once.
- **Reads span every authorised department; writes are bound to one.** `route/list`,
  `period/list` and `period/detail` answer across all of them whatever token they carry, and
  every period row names its own `companyId`. `order/price` and `order/create` answer only
  under a token switched to the period's `companyId`; under any other department they answer
  a bare nginx 404, not the JSON envelope. `order/list` and `order/detail` see the token's
  department alone.
- Every other call carries `Authorization: Bearer {token}`; a stale token is a 401 JSON
  body, and the client logs in — or switches — again once.
- Errors are `{code, message, data: null}` with the HTTP status equal to `code`: 400
  parameter or business refusal (the message is advisor-facing Chinese), 401 credentials,
  403 department, 404 unknown or invisible, 405 method, 429 login throttle, 500 fault.
- Paging: `pageNum ≥ 1`, `pageSize 1–50`; responses carry `list, total, pageNum, pageSize,
  totalPages`. Dates are `YYYY-MM-DD`; money is 元; ids are integers. `route/list`,
  `period/list` and `customer/list` are read to the last page the answer names — the catalog
  is larger than one page and a read that stopped early would hide 线路 and 团期 the advisor
  asked for — with a ceiling of 20 pages against a runaway `totalPages`. The first page names
  `totalPages` and the rest are fetched four at a time.
- Timeouts: 8 s on a paged listing read, with one retry when the page times out; 3 s on every
  other read; 5 s on the login and on an order.

## Reads

| Call | Endpoint | Filters | Row |
|---|---|---|---|
| routes | `GET /route/list` | `routeName~`, `routeCode~`, `departDateStart/End` | `routeId, routeCode, routeName, days, departCityId, departCityName, companyId, companyName, groupId, fromPrice, tags[], itineraryTags[], itineraryTagsStatus, periodTags[], periodPriceTags[], periodHolidayTags[], features[], firstImageUrl, posterUrls[], routeAttachmentName, routeAttachmentUrl` |
| departures | `GET /period/list` | `periodCode~`, `routeName~`, `departDateStart/End` — **no `routeId`**, so a route's departures are fetched by name and kept by `routeId`, or the whole window is read once and grouped by `routeId`, which is what a search does | `periodId, periodCode, routeId, routeName, departDate, returnDate, days, planGuests, minGroupSize, confirmCount, availableSeats, reserveHours, companyId, companyName, departCityName, groupId` |
| itinerary | `GET /route/itinerary?routeId=` | **asked for, not built** | `routeId, version, days[{dayNo, title, morning, midday, afternoon, evening, transport[], hotel, meals}]` → one `ItineraryDay` per row, its `text` the four period fields joined by `；` with the empty ones left out, `source_ref` the `version` |
| departure | `GET /period/detail?periodId=` | | the row above (`companyId` included) plus `reserveCount, placeholderCount, waitlistCount, flightInfo[], priceInfo{adultPrice, childPrice, elderPrice, singleRoomDiff, currency}` (the 市场价) |
| customers | `GET /customer/list` | `keyword~` | `customerId, companyName, csCode, companyType` (1 = 同行) |
| quote | `GET /order/price?periodId=&customerId=` | needs a token in the period's `companyId` | `isExternalOrder, priceType, priceInfo{...}` — the customer's 同业价 |
| my orders | `GET /order/list` | `orderNo~, orderStatus, routeName, departDateStart/End, createTimeStart/End` | `orderId, orderNo, periodId, periodCode, routeName, customerId, customerName, totalAmount, receivedAmount, unreceivedAmount, orderStatus, orderStatusText, createTime` |
| order | `GET /order/detail?orderId=` | | the row plus `storeId, storeName, contactName, contactMobile, adultCount, childCount, elderCount, roomCount, singleRoomDiffCount, originalAmount, adjustAmount, refundedAmount, departDate, returnDate, reserveExpireAt, remark` |

Also `GET /order/passengers` and `GET /order/receipts`, not used by the agent.

## The one write

`POST /order/create` `{periodId, customerId, adultCount, childCount, elderCount, roomCount,
singleRoomDiffCount, storeId?, storeName?, contactName, contactMobile, remark?}` →
`{orderId, needsApproval, approvalId, orderStatus, isWaitlist}`.

- `orderStatus`: 0 预留, 1 占位, 2 确认, 3 取消, 4 审批中, 5 候补. A new order is a 预留
  whose ERP-side expiry is the departure's `reserveHours`; a party larger than the seats
  left becomes a 候补 (`isWaitlist`) rather than a refusal.
- adults + children + elders ≥ 1; counts 0–999; `contactName` 2–50 Chinese characters;
  `contactMobile` a mainland number; a 同行 customer needs `storeId` or `storeName`.
- The department is the token's, not the body's: the call is made on a token switched to the
  period's `companyId`, and there is no department field on the body.
- No idempotency key. On a timeout, list orders before deciding anything; never retry
  blindly. **There is no cancel, release, or amend endpoint.**

## Decisions

- **The advisor is the ERP salesperson, and each one signs in.** An advisor signs in to the
  workbench with their own ERP mobile and password; the host forwards the pair to `POST /login`
  once and keeps only the token, in memory, keyed by `userInfo.userId` (`api/advisors.py`). The
  reads their session makes then span every department that account is authorised for, and a
  quote or an order is written in the departure's own, on that advisor's token.
  `contactName` and `contactMobile` on an order are the name the login named and the mobile it
  was made with. A password is not stored anywhere and not logged. A token is not renewed, so
  the advisor signs in again after its eight hours and after a restart of the host; until they
  do, their session's ERP calls answer `请先登录 ERP 账号`. The deployment's own account
  (`TOUR_ERP_MOBILE`, `TOUR_ERP_PASSWORD`) is what boots the listing snapshot and resolves the
  同行 customer, and it answers no advisor's session.
- **Two prices, and the advisor is quoted one of them.** A departure's `priceInfo` from
  `period/detail` is the 市场价, the direct customer's price and what the customer-facing
  share page shows. `order/price` is the 同业价, the advisor's settlement price and what an
  order is booked at, so that is what every record the advisor reads is priced and totalled
  at; the 市场价 rides along beside it (`market_adult_price`). A departure the catalog has no
  price row for keeps only the 市场价, and the record says so.
- **Orders of other departments are not listed.** `order/list` answers for the login
  department alone, so an order written after a switch does not come back in it. A known gap:
  the cart reads each order by id, which does answer, and the advisor sees the rest in the
  ERP.
- **One customer per deployment for now, named by its code.** `TOUR_ERP_CUSTOMER_CODE` is the
  customer's `csCode` (`HS00994`), and `load_listings` resolves it through `customer/list` at
  boot to exactly one `CustomerRecord`; picking a customer in conversation comes later. A code
  that is unset, unknown, shared by several rows or unreadable leaves the deployment with no
  customer: the error is logged at boot, the reads all still answer, `order/price` is not
  called at all so every record falls back to the departure's 市场价 (`quote_source=list`), and
  a write is refused with `未配置下单客户（TOUR_ERP_CUSTOMER_CODE）`. The internal `customerId`
  is not a deployment setting: an id typed into an environment file names a different customer
  on the next environment, and the code does not.
- **A 同行 order needs a 门店, and it is the deployment's.** `TOUR_ERP_STORE_NAME` is the
  `storeName` every order carries; unset, the write is refused with
  `未配置下单门店（TOUR_ERP_STORE_NAME）` rather than falling back to anything. `TOUR_BRAND_NAME`
  and `TOUR_ASSISTANT_NAME` are what the agency and the assistant are called.
- **The advisor's identity comes from their own login.** `userInfo.userName` is the advisor's
  name and `userInfo.companyName` the department that login landed in; `companies` is how many
  the account reads across, and `userInfo.userId` is what the session and the memory are keyed
  by, so two advisors of one deployment share neither. Against a live ERP the name and the
  department are the whole profile the model is handed — there are no habits and no 门店
  preference — and no memory is seeded, because a seeded habit is one the advisor never wrote.
- **政策 are not answered here.** The agency keeps 退改, 儿童价, 成团, 定金 and 发票 rules in its
  own knowledge base, and this API reads none of it, so `search_policies` is not registered
  against a live ERP (`enable_policies=False`) and the search notes tell the model to say the
  rule has to come from the 门店 or the ERP. A rule stated from memory is the one mistake an
  advisor cannot catch.
- **The customer book is one department's, and not always the login one's.** `customer/list`
  answers for the token's department alone, and in production the department the login lands
  in keeps no customers at all. So `search_customers` asks on the login token first and, while
  the answer is empty, asks each other authorised department in turn on its own switched token
  until one answers. The keyword is what keeps that bounded: a department holds thousands of
  customers, and the pages of a match are read to the end.
- **A hold is a 预留 order** the session created, shown in the cart with a 30-minute
  countdown of our own. Removing or resizing a hold is not offered: the ERP has no such
  call, and the advisor is told to handle it in the ERP.
- **The itinerary tags are the catalog's only structure, and they are ours to read.**
  `route/list` carries `itineraryTags`: some fifty free-text tags per 线路, auto-extracted from
  the itinerary attachment (`itineraryTagsStatus` is the extraction's own state and is not
  read here), naming the destination, the departure city and airline, the hotel standard,
  购物, what the price includes, the guide and every attraction on the way. There is **no
  server-side tag filter**, and the extraction has mistakes in it, so the normalisation and
  the filtering are both ours: `api/tags.py` maps the tags onto controlled attributes with
  the vocabulary in `data/tag-rules.json`, and the backend filters on those. `periodTags` and
  `periodPriceTags` carry a budget band on a few routes (预算约9999—1万元) and
  `periodHolidayTags` is empty; `RouteRecord` keeps `itineraryTags` as `itinerary_tags` and
  `periodPriceTags` as `price_tags`, and a row without either maps to an empty tuple. The
  vocabulary is the production catalog's own: over a whole `route/list` of 269 线路, 232 carry
  the extraction and the destination rules place 231 of those. Because a rule matches by
  substring, a place name that sits inside another place's name goes to whichever rule is
  listed first — 罗马尼亚 and 布加勒斯特 above 罗马 and 加勒, 都柏林 above 柏林, 菲斯特 above
  菲斯, 马拉喀什 above 喀什 — and a tag naming a dish or a square is a negative, because
  土耳其烤肉卷 is a meal on 65 European lines and 西班牙广场 is in Rome.
- **The day-by-day 行程 is in a Word attachment, and the host reads it.** `route/list` carries
  `routeAttachmentName` and `routeAttachmentUrl` and no itinerary field, so the only place a
  线路's days are written down is that .docx. **Asked of the ERP:** `GET
  /route/itinerary?routeId=` returning the day rows in the ERP's own 上午/中午/下午/晚上 +
  内陆交通 + 酒店 + 餐 model; until it exists the host parses `routeAttachmentUrl`.
  `ErpClient.get_itinerary` is the seam for it and `HttpErpClient` maps the row shape above;
  the endpoint answers 404 or 405 today and the call reads as no itinerary, which is when
  `api/itinerary_source.py` fetches the attachment, reads its days and keeps the reading under
  `TOUR_STATE_DIR`. Two document layouts are in the production catalog — one table of `第 N 天`
  rows with `用餐` and `住宿` rows under each, and an English `DAY-N` overview table above a
  Chinese detail table — and a document that names no day yields none rather than a guess.
- **A search reads the window's 团期 once, not each route's** (`WindowReader.list_window`, the
  one call beside the nine). `period/list` takes no route
  id, so weighing every candidate 线路 against the window would cost a call apiece — and the
  candidates are the window's whole catalog whenever the destination is written only in the
  tags. The backend reads `period/list` for the window alone, pages it, groups it by `routeId`
  and keeps it for the search run: a 线路 the read does not name has no 团期 inside the window,
  and there is nothing to ask again. `list_departures` stays for the route the advisor opened,
  where the name query plus the window fallback is one route's freshest seat counts; the client
  holds a window's read for a couple of minutes, so that fallback — a 线路 renamed since its
  团期 were made — costs nothing after a search.
- **Search is still name-based on the ERP's side.** The ERP carries no destination, hotel,
  vehicle, shopping or child-age *field* and no tag filter, so `destination` becomes a
  `routeName` fuzzy match, and `days`, 纯玩, the hotel standard and the departure city are
  filtered client-side off the tags. A destination written only in the tags is why the backend
  reads the window's whole catalog once and matches the normalised attributes itself.
- **Test data.** Beta has no future departures, so `TOUR_ERP_ALLOW_PAST=1` lets the
  backend list departures that already left; it is off in production.
- Ids: `RT-{routeId}` and `DP-{periodId}`. `hold_ttl_minutes` is 30 on our side whatever
  `reserveHours` says.

- **A 包团 is not a search result.** The catalog carries, beside the lines anyone may sell,
  the 包团 a customer chartered, the 会销 one salesperson runs and the 定制 built for one party
  (50 of the 269 production 线路 by name), and `route/list` has no field saying which is which.
  So `api/private_lines.py` reads the name for the words `data/private-lines.json` lists and
  keeps such a line out of every shortlist and out of the boot snapshot; it still opens by id
  and by its full name. **Asked of the ERP:** a `saleType` on each `route/list` row — `公开`,
  `包团`, `会销`, `定制` as strings, or a documented code — maintained by the editors. The
  client already reads it into `RouteRecord.sale_type`: a value outside the file's
  `public_sale_types` makes the line private whatever the name says, and the name becomes the
  fallback for a row that carries none.

## Beta observations

- `GET /order/price` answers a bare nginx HTML 404, not the JSON envelope, for a departure
  the catalog has no price row for, and for one asked under the wrong department. The client
  reads a 404 without a JSON body as a missing record all the same, and the backend falls
  back to the departure's 市场价.
- The account behind the beta credentials is authorised for six departments, its login lands
  in the lowest (2), and one window's reads bring back 8 routes and their departures across
  departments 2, 4 and 5. A quote for a departure in department 5 answers only after a switch
  into it.

## Production observations

- The shape of it: 11 departments on the account, some 270 线路 in the catalog and 160 of
  them inside a plain 60-day window, 190 团期 in that window across a third of those 线路 and
  six departments, and several thousand customers in each department that keeps any. Six pages
  of 线路 with no window, four with one, four of 团期 for the window.
- `period/list` with the window alone is the slow read — 1.3–1.9 s a page against 0.1–0.6 s for
  everything else — so a window is read once and around: a week past each end, which is what
  the first relaxation widens by, so that step reads nothing further. The whole of a 60-day
  window's 团期 comes back in about three seconds that way.
- The department the login lands in keeps no customers and sells no 团期 inside the window. A
  keyword the advisor searches customers by is therefore answered by another department, and a
  quote is always a switch away.
- **A 0 in a price row is 未发布, not free.** Of twelve `order/price` reads across five
  departments, seven answered `childPrice: 0`, nine `elderPrice: 0` and eight
  `singleRoomDiff: 0` — the department has published an adult fare and nothing else. So a fare
  the party actually needs at 0 makes the record `quote_source=partial` with no
  `party_quote_total`, and the card says `儿童价未发布，合计待定`; an adults-only party is
  unaffected by the same row. `priceType` is worth reading and carrying: the same endpoint
  answers 同行价 on some departures and 市场价 on others.
- **Three fields a beta catalog leaves at 0 or wrong.** In one 680-row `period/list`: 32 rows
  carry `routeId: 0`, which belongs to no 线路 that can be named, priced or booked, so those
  rows are dropped from a window read and id 0 is never looked up; 2 rows carry
  `minGroupSize: 0` with `confirmCount: 0`, which states no 最低成团人数 rather than needing
  nobody, so the 团期 is 待成团 and not 已成团; and 162 rows have a `days` that is not
  `returnDate - departDate + 1`, so a 线路's length is the record's own `days` and is never
  counted off the dates.
- **The 行程附件 read.** 168 of 274 线路 link a `.docx`. Over 60 of them the file is mostly its
  photographs: median 1.0 MB, largest 12.4 MB, a third over 5 MB, and the largest download took
  0.7 s. Of 25 read end to end all 25 yielded days and 22 had exactly the 线路's own `days`; the
  three that did not are the attachment's own doing — one writes a range row (`第 5 天-第 7 天`)
  that reads as one day, one writes seven days for a 线路 the catalog calls eight, and one uses
  a third layout whose day markers `api/itinerary_source.py` does not read.
- `GET /route/list` applies `departDateStart`/`departDateEnd` loosely: a week's query answers
  with 线路 that have no 团期 inside it at all — five 斯里兰卡 lines for 10-01..10-07, of which
  `period/list` shows two departing in October. The window on a route search is therefore a
  hint and not a filter, and `TourBackend._search` reads each candidate's departures for the
  pass's window and drops the ones with none, whichever pass it is; they come back through a
  relaxation step with a note naming their nearest date.
