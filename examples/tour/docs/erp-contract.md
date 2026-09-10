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
  totalPages`. Dates are `YYYY-MM-DD`; money is 元; ids are integers.

## Reads

| Call | Endpoint | Filters | Row |
|---|---|---|---|
| routes | `GET /route/list` | `routeName~`, `routeCode~`, `departDateStart/End` | `routeId, routeCode, routeName, days, departCityId, departCityName, companyId, companyName, groupId, fromPrice, tags[], itineraryTags[], itineraryTagsStatus, periodTags[], periodPriceTags[], periodHolidayTags[], features[], firstImageUrl, posterUrls[], routeAttachmentName, routeAttachmentUrl` |
| departures | `GET /period/list` | `periodCode~`, `routeName~`, `departDateStart/End` — **no `routeId`**, so a route's departures are fetched by name and kept by `routeId` | `periodId, periodCode, routeId, routeName, departDate, returnDate, days, planGuests, minGroupSize, confirmCount, availableSeats, reserveHours, companyId, companyName, departCityName, groupId` |
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

- **The advisor is the ERP salesperson.** The host logs in with the advisor's ERP mobile and
  password; the reads then span every department that account is authorised for, and a quote
  or an order is written in the departure's own. `contactName` and `contactMobile` on an
  order are the advisor's own.
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
- **One customer per deployment for now.** `TOUR_ERP_CUSTOMER_ID` names the 同行 customer
  every quote and order is made for; picking a customer in conversation comes later.
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
  `periodPriceTags` as `price_tags`, and a row without either maps to an empty tuple.
- **Search is still name-based on the ERP's side.** The ERP carries no destination, hotel,
  vehicle, shopping or child-age *field* and no tag filter, so `destination` becomes a
  `routeName` fuzzy match, and `days`, 纯玩, the hotel standard and the departure city are
  filtered client-side off the tags. A destination written only in the tags is why the backend
  reads the window's whole catalog once and matches the normalised attributes itself.
- **Test data.** Beta has no future departures, so `TOUR_ERP_ALLOW_PAST=1` lets the
  backend list departures that already left; it is off in production.
- Ids: `RT-{routeId}` and `DP-{periodId}`. `hold_ttl_minutes` is 30 on our side whatever
  `reserveHours` says.

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

- `GET /route/list` applies `departDateStart`/`departDateEnd` loosely: a week's query answers
  with 线路 that have no 团期 inside it at all — five 斯里兰卡 lines for 10-01..10-07, of which
  `period/list` shows two departing in October. The window on a route search is therefore a
  hint and not a filter, and `TourBackend._search` reads each candidate's departures for the
  pass's window and drops the ones with none, whichever pass it is; they come back through a
  relaxation step with a note naming their nearest date.
