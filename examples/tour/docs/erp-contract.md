# The real ERP behind the tour vertical (Phase 4)

The agency's B2B ERP exposes an "AI CLI" API. This page is the contract `HttpErpClient`
maps onto `ErpClient`, as observed on the beta environment on 2026-09-07, and the
decisions that shape Phase 4. Fictional example values throughout.

## Access

- Base URL `{backend}/aicli`; beta `http://beta-b2b-backend.hl1j.com/aicli`; production
  over HTTPS. JSON both ways; `Content-Type: application/json` on POST.
- `POST /login` `{mobile, password, companyId}` → `{token, tokenType: "Bearer", expiresIn:
  28800, expiresAt, userInfo{userId, userName, companyId, companyName}, companies[]}`.
  The token lives 8 hours, is not renewed, and is bound to one department. **`companyId`
  is required in practice**: an account without a default department makes the endpoint
  fail without it. Ten failed logins per mobile and IP lock the account for 15 minutes.
- `POST /switch-company` `{companyId}` with the current bearer → a new token for that
  department plus the full `companies` list.
- Every other call carries `Authorization: Bearer {token}`; a stale token is a 401 JSON
  body, and the client logs in again once.
- Errors are `{code, message, data: null}` with the HTTP status equal to `code`: 400
  parameter or business refusal (the message is advisor-facing Chinese), 401 credentials,
  403 department, 404 unknown or invisible, 405 method, 429 login throttle, 500 fault.
- Paging: `pageNum ≥ 1`, `pageSize 1–50`; responses carry `list, total, pageNum, pageSize,
  totalPages`. Dates are `YYYY-MM-DD`; money is 元; ids are integers.

## Reads

| Call | Endpoint | Filters | Row |
|---|---|---|---|
| routes | `GET /route/list` | `routeName~`, `routeCode~`, `departDateStart/End` | `routeId, routeCode, routeName, days, departCityId, departCityName, companyId, companyName, groupId, fromPrice, tags[], features[], firstImageUrl, posterUrls[], routeAttachmentName, routeAttachmentUrl` |
| departures | `GET /period/list` | `periodCode~`, `routeName~`, `departDateStart/End` — **no `routeId`**, so a route's departures are fetched by name and kept by `routeId` | `periodId, periodCode, routeId, routeName, departDate, returnDate, days, planGuests, minGroupSize, confirmCount, availableSeats, reserveHours, companyName, departCityName, groupId` |
| departure | `GET /period/detail?periodId=` | | the row above plus `reserveCount, placeholderCount, waitlistCount, flightInfo[], priceInfo{adultPrice, childPrice, elderPrice, singleRoomDiff, currency}` (the list price) |
| customers | `GET /customer/list` | `keyword~` | `customerId, companyName, csCode, companyType` (1 = 同行) |
| quote | `GET /order/price?periodId=&customerId=` | | `isExternalOrder, priceType, priceInfo{...}` — the customer's price |
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
- No idempotency key. On a timeout, list orders before deciding anything; never retry
  blindly. **There is no cancel, release, or amend endpoint.**

## Decisions

- **The advisor is the ERP salesperson.** The host logs in with the advisor's ERP mobile,
  password, and department; reads use that salesperson's visibility; `contactName` and
  `contactMobile` on an order are the advisor's own.
- **One customer per deployment for now.** `TOUR_ERP_CUSTOMER_ID` names the 同行 customer
  every quote and order is made for; picking a customer in conversation comes later.
- **A hold is a 预留 order** the session created, shown in the cart with a 30-minute
  countdown of our own. Removing or resizing a hold is not offered: the ERP has no such
  call, and the advisor is told to handle it in the ERP.
- **Search is name-based.** The ERP carries no destination, hotel, vehicle, shopping or
  child-age fields; `destination` becomes a `routeName` fuzzy match, `days` is filtered
  client-side, and 纯玩/零购物 is read from `tags` when present. A structured tag scheme
  on the ERP side is a separate task.
- **Test data.** Beta has no future departures, so `TOUR_ERP_ALLOW_PAST=1` lets the
  backend list departures that already left; it is off in production.
- Ids: `RT-{routeId}` and `DP-{periodId}`. `hold_ttl_minutes` is 30 on our side whatever
  `reserveHours` says.

## Beta observations

- `GET /order/price` answers a bare nginx HTML 404, not the JSON envelope, for a departure
  the catalog has no price row for. The client reads a 404 without a JSON body as a missing
  record all the same, and the backend falls back to the departure's list price.
