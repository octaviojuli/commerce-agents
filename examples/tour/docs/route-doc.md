# The 线路 as a document

The catalog the ERP sells has two halves. The 团期, the seats, the 成团 state and the two
prices change by the hour and are the ERP's: the workbench reads them live. What a 线路 *is*
— its flights, its day-by-day programme, its hotels and meals, what the price includes —
changes when the agency's product staff replace the 行程附件, and is written nowhere but in
that attachment. `RouteDoc` (`api/route_doc.py`) is that half read into fields, so the
search can filter on what a line really contains and a card can show it.

## The model

One document per 线路, `data/route-schema.json` being its JSON Schema:

| Field | What it holds | Where it comes from |
|---|---|---|
| `route_id`, `route_code`, `name`, `department`, `sale_type` | identity | the ERP's `route/list` row |
| `summary` | `days` (the ERP's), `nights` (the hotel nights read), `depart_city`, `countries`, `region` | the row, then `api/tags.py` over its tags and name |
| `cover` | 航空公司, 酒店标准, 用餐安排, the ★ highlights, every labelled front-matter row | the attachment's cover table or its 吃/住/行 lines |
| `transport` | every 参考航班 segment: day, flight number, carrier, route, times as written | the day headers and programmes |
| `days[]` | `places` (the title's A-B-C), `distances_km`, `transport` (车程/交通), `overnight` (hotel / ship / flight / home), `flights`, `sights[]` (名, 景点/外观/购物/自费/赠送/自由活动, duration, 含门票), `meals` (three, each text and included), `hotel` (names, 或同级, grade), `text` (the programme whole) | the day table |
| `inclusions`, `exclusions` | the 费用包含 / 不含 lists, one item each | the terms |
| `shopping`, `optional` | the 购物店 and 自费项目, from the terms and from the days | both |
| `policies` | the sentence the terms write about 单房差, 儿童, 签证, 退改, 定金 | the terms |
| `notices` | the rest of the terms, headings included, capped | the terms |
| `source` | attachment name and URL, ETag, size, parse time, parser version | the fetch |
| `quality` | `completeness` in [0, 1], `needs_review` in the reviewer's words, `reviewed_by` / `reviewed_at` | the parser; the reviewer |

`text` on each day is kept whole: the parsed fields are a reading of it and not a replacement,
and a reviewer corrects the fields against it.

## The parser

`api/route_parser.py` reads by rule, on top of `api/itinerary_source.py`'s split into days.
Rules are deterministic and never invent a field: where one finds nothing the field stays
empty and `needs_review` says so. The two production layouts are the ones the rules were
written against — one table per day with 用餐/住宿 rows, or an English overview table above
a detail table with 餐饮/住宿 lines — and a third layout yields fewer fields and a lower
score, which is the signal to look at it.

Four rules read a day around its 【】 rather than inside them, because that is where the
attachments put the qualifiers: a sentence ending 均为外观或车游 makes every 【】 in it an
外观; a 参考行程如下 under a 全天自由活动 with a 自费套餐 makes every 【】 after it 自费
up to the first 【赠送…】; a note saying X为赠送项目 marks the sight named X; a 自由活动
is an entry of its own (全天/上午/下午). A priced line is a 自费 item whether or not the
word 自费 stands beside the price (美瑞莎出海观鲸120美金/人). A 茶园, 香料园 or 宝石 kept
as a sight, a line saying 此处不算购物店, and a 赠送 promised on the cover each add a
`needs_review` note, since the parser cannot decide them and a product person can.
A 【】 is a 购物 by its name (百货, Outlet, 老佛爷, 莎玛丽丹, 花宫娜, 天鹅广场) or by the words
beside it (购物广场, 售卖, 门店); 含船票, 含缆车 and 含小火车 count as 含门票, 不入内 as an
外观; a day whose 住宿 row names only the city takes its hotel grade from the overview
table's HOTEL column; the 退改 policy is the 团体订位…概不退回 sentence of the notices, never
the 不可抗力 clause; a 单人间房差…; 儿童… line is split at the semicolon; a 另行付费 table is
read by column with the unit from its 价格(欧元/人) header.

`score` is the completeness: the attachment's day count against the ERP's (0.20), a night
read for every day (0.15), meals read for every day away from home (0.15), at least one
flight (0.10), the 包含 and 不含 lists (0.10 each), sights on the hotel days (0.10), a cover
(0.05), places on every title (0.05).

## The pipeline

```
attachment (.docx) ──parse──▶ {routeId}.json (draft, needs_review) ──review──▶ published
                                     │                                     │
                              index.json, REPORT.md                 search facets, cards
```

`scripts/parse_attachments.py` runs the first arrow over the catalog: every public `.docx`
line (包团/会销/定制 skipped unless asked), four downloads at a time, one JSON per line under
`$TOUR_STATE_DIR/route-docs/`, `index.json` and `REPORT.md` ranking them, and with
`--select N` the N most complete copied into `selected/` for the first review round. The
documents are the agency's product data and are not committed.

The review is the agency's product staff's: they read each `selected/` document against the
attachment, correct the fields, set `quality.reviewed_by` and `reviewed_at`, and move the
file to `route-docs/published/`.

## The runtime

`api/route_docs.py`'s `RouteDocStore` reads `published/` and `selected/` at boot, a published
document winning over a selected one for the same 线路. The documents are the catalog: a
search is answered off them and the ERP is asked only for the dynamic half — which 团期 run,
their 成团 state, the seats left and the 同业价.

- `api/catalog.py` reads each document into `RouteFacts` and matches a request on them: the
  destination against the countries, the 线路系, the name, the department, the places the days
  pass through and the sights they name; the day count, 出发城市, 纯玩, the hotel standard and
  the 线路系 against what the document states. A 线路 with no document is not searched at all.
- a 线路 card is the document's own fields, and its labels say 纯玩 or 购物店N家, the 钻 grade
  the 酒店标准 states where it states one, the airline and 已复核 or 解析稿 (`doc`);
- `present_route_days` draws the whole 逐日行程 off the document — the days, the flights out
  and back, 费用包含 / 不含, 购物店, 自费项目 and the policy sentences;
- the 行程 on a details record and under a 定制方案 comes from the document's days, and the
  card says 线路文档（已复核）or 线路文档（解析稿，待复核）as its 行程来源;
- the card's 规格 gain 参考航班, 酒店标准, 用餐安排, 购物店, 自费项目, 费用包含 / 不含, 单房差,
  儿童 and 签证.

A deployment whose state directory holds no document reads the fixture catalog's own under
`data/route-docs/published/`, which is what the demo searches. A 团期, its seats and its
prices are never in a document.

## Not yet

- `.pdf` attachments (65 of the production catalog's 269), image attachments (34), `.xlsx` (3).
- Filters off `inclusions` and the meals; the customer page.
- A review tool. The first round is the JSON files and the report.
- The ERP holding the document. `erp-contract.md` asks for a structured itinerary endpoint;
  when it exists the parser becomes the fallback.
