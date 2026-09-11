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
| `days[]` | `places` (the title's A-B-C), `distances_km`, `transport` (车程/交通), `overnight` (hotel / flight / home), `flights`, `sights[]` (名, 景点/外观/购物/自费/赠送, duration, 含门票), `meals` (three, each text and included), `hotel` (names, 或同级, grade), `text` (the programme whole) | the day table |
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
document winning over a selected one for the same 线路, and the backend reads a document ahead
of everything it would otherwise guess:

- the 行程 on a details record and under a 定制方案 comes from the document's days, and the
  card says 线路文档（已复核）or 线路文档（解析稿，待复核）as its 行程来源;
- the card's 规格 gain 参考航班, 酒店标准, 用餐安排, 购物店, 自费项目, 费用包含 / 不含, 单房差,
  儿童 and 签证;
- the model reads `doc` (reviewed / draft), `shopping_stops`, `meals_included`,
  `doc_hotel_grade` and `flights` on the route record;
- the 纯玩 and hotel-standard filters, and the note a relaxed record carries, follow the
  document's 购物店 count and stated grade rather than the tags.

A 线路 with no document is read as before: the ERP's own days where it has them, the
attachment parsed where it does not, the tags for the rest. The team's 团期, seats and prices
are never in a document.

## Not yet

- `.pdf` attachments (65 of the production catalog's 269), image attachments (34), `.xlsx` (3).
- Search facets off `days[].sights`, `inclusions` and the meals beyond the three filters
  above; the card drawing the programme day by day; the customer page.
- A review tool. The first round is the JSON files and the report.
- The ERP holding the document. `erp-contract.md` asks for a structured itinerary endpoint;
  when it exists the parser becomes the fallback.
