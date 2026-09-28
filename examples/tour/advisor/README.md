# Advisor service

The back end of the advisor app (`../advisor-web`). An advisor pastes a customer's WeChat
message; the service reads it into a versioned need, searches and explains routes, answers
questions from published itineraries, keeps what the deal remembers, and returns a WeChat
draft whose every factual sentence has been checked against a fact.

The service keeps its own records (deals, needs, memory, plans, quotes, ledger, documents) in
PostgreSQL schema `advisor`. It reads the cloud warehouse only through the warehouse's advisor
HTTP API, with the advisor's own warehouse token: products, published itineraries,
departures, offers and quotes.

## A turn

1. `model.understand` reads the message into changes with the customer's own words as
   evidence, the questions asked, concerns, a selection and a confirmation.
2. `interpret` checks each change: evidence must be in the message; places and holidays are
   parsed by rule; the party is merged child by child. A field with no saved value is filled;
   a field that changes becomes a change sheet with its consequences computed by the program.
3. The program acts: a search with a visible funnel, answers from the facts of the route a
   question names, a selection, a confirmation. It picks the one question worth asking.
4. `model.answer` and `model.draft` write the answers and the WeChat draft from those facts.
5. `grounding.check` keeps a factual clause only when a fact supports it with the same
   numbers, negations, limits and conditions; questions about saved fields are removed.

## What the program guarantees

- A price belongs to one deal, route, offer and departure, the party and rooms it was asked
  for, and the need's window; `pricing.validity` is the one rule. Choosing another route voids
  the deal's confirmations and prices; choosing another departure or offer voids its
  confirmations. A quote is sent or sold on only when it is formal, current, and on a
  confirmed current sheet.
- A departure sold as several offers is priced only after the advisor picks one: the price
  endpoint answers 409 with `offers`.
- A deal is sold once (a unique ledger row); repeating the same sale returns it. A receipt with
  an `Idempotency-Key` header is recorded once.
- A model reading is applied against the need as it is when written; an edit saved meanwhile
  turns the fill into a change sheet. A plan is written only for the need version it was built
  from.
- The warehouse owns the login: a 401 from it ends the local session, and every five minutes
  the session checks that its organisation still grants the advisor role.
- The customer's plan page withdraws a price past its validity.

## Modules

| Module | What it owns |
|---|---|
| `need.py` | Need fields, the per-child party, gates, clarity, consequences of a change |
| `interpret.py` | Evidence, places, holidays, party merge, fill versus change sheet |
| `grounding.py` | The draft check |
| `facts.py` | A published itinerary as whole citable facts, the quick-look grid, warnings |
| `routes.py` | Search funnel, route cards, departures |
| `pricing.py` | Warehouse price checks, per-bed child lines, the one validity rule, margin |
| `turns.py` | The turn pipeline |
| `memory.py` | Concerns, things to avoid, salutation, the question book, sent statements |
| `selling.py` | Compare, plans, the customer's plan page |
| `closing.py` | Change adoption, dates, confirmation sheet, formal quote, sale, tasks, notes |
| `papers.py` | Encrypted document scans and local passport MRZ reading |
| `warehouse.py` | The warehouse HTTP client |
| `store.py`, `db.py` | Owner-scoped row access and the schema |
| `api.py`, `serve.py` | HTTP interface and entry point |

## Run

```bash
cd examples
ADVISOR_DATABASE_URL=postgresql+psycopg://…/advisor \
WAREHOUSE_URL=http://127.0.0.1:8010 \
ADVISOR_MATERIAL_KEY=… \
python -m tour.advisor.serve          # http://127.0.0.1:8006
```

`ADVISOR_MATERIAL_KEY` is a base64 32-byte key kept as a deployment secret; document scans are
sealed with it, so a new key makes earlier scans unreadable.

The model is read from `ADVISOR_MODEL` or `TOUR_MODEL` with the Anthropic client's usual
variables. `tesseract` on the path enables passport recognition.

## Test

`tests/` covers the draft check, interpretation, pricing and gates without a database, and the
storyline through the HTTP API with a stub warehouse and scripted model when
`WAREHOUSE_TEST_ADMIN_URL` names an isolated `_test` database.
