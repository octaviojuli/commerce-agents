// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The conversation's live 占位, as the strip and the panel both read them. Every cart payload
 * carries the session's holds with what is left of each 30-minute window (`api/main.py`
 * `holds_payload`); the deadlines are stamped from `seconds_remaining` the moment a payload
 * arrives and tick locally from there, so the countdown stays honest between turns.
 */

import { useEffect, useMemo, useState } from "react";
import type { CartItem, CartPayload, Hold } from "./types";

/** The backend marks a 候补 line in its title; such a line took no seats and holds no order. */
const WAITLIST_MARK = "（候补）";

export function isWaitlist(item: CartItem): boolean {
  return item.title.includes(WAITLIST_MARK);
}

/** The line's title without that mark, which the 候补 badge says instead. */
export function lineTitle(item: CartItem): string {
  return item.title.replace(WAITLIST_MARK, "");
}

/** One tick a second while anything is held. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export interface HoldClock {
  /** The 占位 the payload carries, in its own order. */
  holds: Hold[];
  /** Seconds left on each 占位 by `hold_id`; null until the first tick. */
  seconds: Record<string, number | null>;
  /** The 占位 on a 团期, by the departure id; a 候补 line has none. */
  byProduct: Record<string, Hold>;
}

export function useHoldClock(cart: CartPayload | null): HoldClock {
  const holds = useMemo(() => cart?.holds ?? [], [cart]);
  const [deadlines, setDeadlines] = useState<Record<string, number>>({});
  useEffect(() => {
    const at = Date.now();
    setDeadlines(
      Object.fromEntries(holds.map((hold) => [hold.hold_id, at + hold.seconds_remaining * 1000])),
    );
  }, [holds]);
  const now = useNow(holds.length > 0);
  return useMemo(() => {
    const seconds = Object.fromEntries(
      holds.map((hold) => {
        const deadline = deadlines[hold.hold_id];
        const left =
          deadline === undefined || now === 0 ? null : Math.max(0, Math.round((deadline - now) / 1000));
        return [hold.hold_id, left];
      }),
    );
    const byProduct = Object.fromEntries(holds.map((hold) => [hold.product_id, hold]));
    return { holds, seconds, byProduct };
  }, [holds, deadlines, now]);
}
