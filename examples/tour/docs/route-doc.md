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
attachment, correct the fields, and set `quality.reviewed_by` and `reviewed_at`. A document
with those set is published; the runtime reads published documents for content and the ERP
for everything dated. That second arrow is the next step and is not built yet.

## Not yet

- `.pdf` attachments (65 of the production catalog's 269), image attachments (34), `.xlsx` (3).
- The runtime reading published documents: search facets off `days[].sights`, `hotel.grade`,
  `meals`, `inclusions`; the card showing the programme; the customer page.
- A review tool. The first round is the JSON files and the report.
- The ERP holding the document. `erp-contract.md` asks for a structured itinerary endpoint;
  when it exists the parser becomes the fallback.
