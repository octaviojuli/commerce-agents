# Advisor app

The advisor's phone-first web app, the front end of `../advisor`. Its screens follow the
advisor prototype (`docs/cloud-warehouse/advisor-mobile-prototype-v4.html`), whose styles it
uses as they are.

| Path | Screen |
|---|---|
| `/` | Today: time-limited items, customers waiting for a reply, today's time points, chances |
| `/deals` | Deals by stage, one next step each |
| `/deals/[id]` | The conversation: what was read, gates, change sheets, routes, answers, the WeChat draft, next steps |
| `/deals/[id]/memory` | What the deal remembers; need fields are edited here |
| `/deals/[id]/search` | Must and nice-to-have conditions, the funnel, what relaxing adds |
| `/routes`, `/routes/[pid]` | Route catalog and quick-look with "ask this route" |
| `/deals/[id]/compare`, `/plan` | Compare against the customer's concerns; plan with reasons, drawbacks and margin |
| `/p/[token]` | The customer's plan page, opened from WeChat |
| `/deals/[id]/qa`, `/itinerary` | The question book; notes on itinerary days |
| `/deals/[id]/dates`, `/confirm`, `/quote` | Departure comparison, confirmation sheet, formal quote |
| `/deals/[id]/docs`, `/after` | Document scans; the pre-departure checklist and money |

From 1024 px the deal list and the deal's memory stay beside the current screen.

## Run

```bash
cd examples/tour/advisor-web
ADVISOR_API_URL=http://127.0.0.1:8006 npx next dev -p 3006
```

`/api/*` is forwarded to the advisor service; the browser holds only its session cookie.
