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
| `twin_of` | the 线路 whose reviewed document this line reads, where the two parses are identical | `api/route_docs.py`, never the parser |
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

Rules read a day around its 【】 rather than inside them, because that is where the
attachments put the qualifiers: a sentence ending 均为外观或车游 makes every 【】 in it an
外观; a 参考行程如下 under a 全天自由活动 with a 自费套餐 makes every 【】 after it 自费
up to the first 【赠送…】; a note saying X为赠送项目 marks the sight named X; a 自费推荐 or
（自费游览…） right after a bracket makes it 自费; a 若…/将调整为… sentence is the 温馨提示's
alternative and its 【】 is no sight of the day; 【特别赠送】 is a label and the gift is the
words beside it, as 赠送观鲸 written with no bracket at all is; a group written as prose
(日内瓦游览（均外观）：A，B，C) is read name by name; a 自由活动 the prose states is an entry of
its own (全天/上午/下午), while one inside a parenthesis — （总观光+自由活动时间不少于2小时） —
is the duration of the 【】 before it and nothing more. A priced line is a 自费 item whether or
not the word 自费 stands beside the price (美瑞莎出海观鲸120美金/人); a price in a 退门票
sentence is money given back and no item. A 茶园, 香料园 or 宝石 kept as a sight, a sight the
prose calls a 购物场所 or 露天市场, a line saying 此处不算购物店, and a 赠送 promised on the
cover each add a `needs_review` note, since the parser cannot decide them and a product person
can. A 【】 is a 购物 by its name (百货, Outlet, 老佛爷, 莎玛丽丹, 花宫娜, 天鹅广场) or by the
words its sentence writes beside it (购物广场, 售卖, 门店); 含船票, 含缆车 and 含小火车 count as
含门票 and 不入内 as an 外观, while 含官导 and 含讲解 are a guide and leave the ticket unstated
and 不含园内门票 states there is none; a 包含 list writing 所含景点首道门票（其余景点均为外观）：A、B
gives the ticket to the sights it names and takes it from the 景点 it does not. A day whose
住宿 row names only the city takes its hotel grade from the overview table's HOTEL column, and
a grade keeps the digits as written (4星, 4-5星); a 火车参考班次, a 参考船次 and a 参考航班：待定
are the day's `transport`, never a place of its title, and a flight's `raw` keeps the 参考航班
label and the full-width parentheses the attachment wrote. `summary.countries` is read off the
线路 name, the day titles and places and the cover — a country word in a day's prose belongs to a
sight's name (英国花园) and not to the itinerary — and falls back to the line's tags without
their region words (东欧, 北欧, 巴尔干), which `facets.region` keeps instead. The 退改 policy is
the 团体订位…概不退回 sentence with the 游客取消规则 day bands after it, never the 不可抗力
clause; 签证 is read from a notice only where the notice is about the fee, so a 销签 reminder is
not one; a 儿童 clause saying 与成人同价 loses to a 周岁/不占床 one; a 单人间房差…; 儿童… line is
split at the semicolon; a 另行付费 table is read row by row, a row with columns being an item
whatever its name's length, with the price from the price column and the unit from the
价格(欧元/人) header.

A `.pdf` goes through `api/pdf_source.py` first: `pdftotext` (poppler, on the PATH) gives
the page text, runs of three or more spaces become the `||` cell separator so a table row
reads like a .docx row, and the day-header shapes the .pdf attachments use — `DAY` with
the number drawn as a picture and the bare number on the line below, a day written as a
number alone, a number printed against the middle of the right column's paragraph, and one
printed in the same row as the day's 餐/住/行 fields — are rewritten into `第N天`. A
private-use character (Word's Wingdings) between two words is a separator and reads as a dash,
and elsewhere it is decoration and goes; a page-edge 出发日期 stamp printed inside a sentence
(佛罗9.23伦萨) goes with it. A 报价包含 ｜ 报价不含 table printed side by side is read by
column, a cell that does not close with ；or 。 being the row above wrapping. pdftotext reads
a page in two orders, `-layout` (columns kept) and default (text blocks in sequence); the
parser reads each .pdf both ways and keeps the more complete document, and `source.parser`
says which (`docx-rules-3/pdf-layout`).
A .pdf with fewer than 300 Chinese characters of text is a scan and is refused. The .pdf
attachments' own labels — `餐食：早午晚`, `酒店：…`, `餐：/ 住：飞机上 行：无` — are read by
`itinerary_source.split_days` cell by cell.

`score` is the completeness: the attachment's day count against the ERP's (0.20), a night
read for every day (0.15), meals read for every day away from home (0.15), at least one
flight (0.10), the 包含 and 不含 lists (0.10 each), sights on the hotel days (0.10), a cover
(0.05), places on every title (0.05).

## The pipeline

```
attachment (.docx / .pdf) ──parse──▶ {routeId}.json (draft, needs_review) ──review──▶ published
                                     │                                     │
                              index.json, REPORT.md                 search facets, cards
```

`scripts/parse_attachments.py` runs the first arrow over the catalog: every public `.docx`
and `.pdf` line (包团/会销/定制 skipped unless asked), four downloads at a time, one JSON per line under
`$TOUR_STATE_DIR/route-docs/`, `index.json` and `REPORT.md` ranking them, and with
`--select N` N of them copied into `selected/` for a review round. The documents are the
agency's product data and are not committed.

A round is chosen one of two ways. `--select N` alone takes the most complete documents,
0.9 and over first, one department at a time so the round sees every department's layout.
`--select N --selling` takes what sells instead: the 团期 of the next 180 days read in one
window call (`erp_client.WindowReader`; a call per 线路 for a client without it), the lines with
one inside it, the lines a round has already answered for — the published documents and the
twins reading them — left out, a completeness floor of 0.6 because a selling line is worth a
reviewer filling a field in and a `.pdf` scores lower than a `.docx`, then the same round-robin
over the departments. `selected/selected.json` carries the criterion that chose the round
(`selling-180d` or `completeness-0.9`), the window it was read over and the lines it holds.

The review is the agency's product staff's: they read each `selected/` document against the
attachment, correct the fields, set `quality.reviewed_by` and `reviewed_at`, and move the
file to `route-docs/published/`.

## The runtime

`api/route_docs.py`'s `RouteDocStore` reads three directories at boot — the drafts under
`route-docs/`, the round being checked under `selected/`, the checked documents under
`published/` — a published document winning over a selected one and a selected one over the
draft beside it. Every document is the catalog, the drafts included: the 线路 selling this month
are mostly ones no round has reached, so a search over the reviewed documents alone answers with
the lines that do not sell. A reviewed document is ranked ahead of a draft and the card says
which of the two it is. The ERP is asked only for the dynamic half — which 团期 run, their 成团
state, the seats left and the 同业价.

Two lines whose parsed 逐日行程 is identical word for word are one product sold under two names
— a second 出发城市, an 加班 line, a second airline — and the draft of such a line reads the
reviewed document of the line it copies: that document's 行程, its corrections and the review
that signed it, under the twin's own 线路 id, code, name, department, 出发城市 and attachment,
with `twin_of` naming where it came from. The reviewed line is matched on its *draft* — the
`selected/` copy, or the draft beside it — because that is the same parser's reading as the
twin's; where several reviewed lines share one 行程 the lowest 线路 id is the one inherited, they
being the same product too. The store logs how many lines read another's document.

- `api/catalog.py` reads every document into `RouteFacts` and matches a request on them: the
  destination against the countries, the 线路系, the name, the department, the places the days
  pass through and the sights they name; the day count, 出发城市, 纯玩, the hotel standard, the
  feature words and the 线路系 against what the document states, each filter meeting any one of
  the values the advisor's chips sent. A 线路 with no document is not searched at all.
- a 线路 card is the document's own fields, and its labels say 纯玩 or 购物店N家, the 钻 grade
  the 酒店标准 states where it states one, the airline and 已复核 or 解析稿 (`doc`);
- `present_route_days` draws the whole 逐日行程 off the document — the days with their 车程
  notes, the flights out and back, 费用包含 / 不含, 购物店, 自费项目 and the policy sentences;
- the 行程 on a details record and under a 定制方案 comes from the document's days, and the
  card says 线路文档（已复核）or 线路文档（解析稿，待复核）as its 行程来源;
- the card's 规格 gain 参考航班, 酒店标准, 用餐安排, 购物店, 自费项目, 费用包含 / 不含, 单房差,
  儿童 and 签证.

A deployment whose state directory holds no document reads the fixture catalog's own under
`data/route-docs/published/`, which is what the demo searches. A 团期, its seats and its
prices are never in a document.

## Not yet

- Scanned `.pdf` attachments (no text layer; `pdf_source.ImageOnlyPdf`, listed in the report
  as skipped), image attachments (34), `.xlsx` (3).
- Filters off `inclusions` and the meals; the customer page.
- A review tool. The first round is the JSON files and the report.
- The ERP holding the document. `erp-contract.md` asks for a structured itinerary endpoint;
  when it exists the parser becomes the fallback.
