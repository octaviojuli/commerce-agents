// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** Weeks mirror data/merchant_occupancy.json; the change is shaped as `stage_promotion` emits. */

import type { ChangePreviewPayload, OccupancyCalendarPayload } from "./types";

const change_preview: ChangePreviewPayload = {
  change_id: "chg-9107",
  headline: "平日房价下调 — ACME Guesthouses Condesa",
  note: "10月5日至30日，周一至周四的每晚房价从 $204.00 下调到 $183.60；周末房价不变。",
  change: {
    change_id: "chg-9107",
    kind: "promotion",
    status: "staged",
    summary:
      "平季平日下调（每晚房价九折，2026-10-05 至 2026-10-30，周一至周四的夜晚）",
    items: [{ target: "AL-STAY-110", field: "nightly_rate", before: 204.0, after: 183.6 }],
    created_at: "2026-07-09",
    created_by: "Marta",
    created_by_kind: "agent",
    margin_impact: -612.0,
    // Margins as the backend computes them, so the showcase renders the margin bar.
    margin_before_pct: 58.0,
    margin_after_pct: 53.3,
  },
};

const occupancy_calendar: OccupancyCalendarPayload = {
  title: "ACME Guesthouses Condesa — 夏末入住率",
  period: "2026-07-27/2026-08-09",
  grain: "week",
  listings: [
    {
      listing_id: "AL-STAY-110",
      title: "ACME Guesthouses Condesa",
      rooms: 10,
      base_nightly_rate: 204.0,
      note: "周末按 204/晚满房；平日的低谷才是定向下调能换来预订的地方。",
      weeks: [
        {
          week_start: "2026-07-27",
          nightly_rate: 204.0,
          occupancy_pct: 84,
          midweek_occupancy_pct: 80,
          weekend_occupancy_pct: 93,
          on_the_books_pace_pct: 3,
        },
        {
          week_start: "2026-08-03",
          nightly_rate: 204.0,
          occupancy_pct: 85,
          midweek_occupancy_pct: 81,
          weekend_occupancy_pct: 94,
          on_the_books_pace_pct: 4,
        },
      ],
    },
  ],
};

export const SHOWCASE = { change_preview, occupancy_calendar };
