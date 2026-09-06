// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The advisor's live 占位, across the top of the workbench. Every cart payload carries the
 * conversation's holds with what is left of each TTL (`api/main.py` `holds_payload`); the
 * deadlines are stamped from `seconds_remaining` the moment a payload arrives and the strip
 * ticks locally from there, so the countdown stays honest between turns.
 */

import { useEffect, useMemo, useState } from "react";
import { formatCountdown } from "@/lib/format";
import type { CartPayload, Hold } from "@/lib/types";

/** Under a minute, the strip presses. */
const URGENT_SECONDS = 60;

/** Local ms epochs by hold id, re-stamped whenever a new cart payload arrives. */
function useDeadlines(holds: Hold[]): Record<string, number> {
  const [deadlines, setDeadlines] = useState<Record<string, number>>({});
  useEffect(() => {
    const at = Date.now();
    setDeadlines(
      Object.fromEntries(holds.map((hold) => [hold.hold_id, at + hold.seconds_remaining * 1000])),
    );
  }, [holds]);
  return deadlines;
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

function HoldRow({ title, holdId, seconds }: { title: string; holdId: string; seconds: number | null }) {
  const expired = seconds !== null && seconds <= 0;
  const urgent = seconds !== null && seconds > 0 && seconds <= URGENT_SECONDS;
  return (
    <li
      className={`flex shrink-0 items-center gap-2.5 rounded-full border py-1 pl-3 pr-2.5 ${
        expired
          ? "border-(--danger)/40 bg-(--danger-soft)"
          : urgent
            ? "border-(--warn)/40 bg-(--warn-soft)"
            : "border-(--line) bg-(--card)"
      }`}
      title={`占位 ${holdId}`}
    >
      <span className="max-w-[19rem] truncate text-[13px] font-semibold text-(--ink)">{title}</span>
      <span
        aria-label={expired ? "占位已过期" : "占位剩余时间"}
        className={`tg-num text-[13px] font-bold ${
          expired ? "text-(--danger)" : urgent ? "tg-urgent text-(--warn)" : "text-(--ink-2)"
        }`}
      >
        {seconds === null ? "—:—" : expired ? "已过期" : formatCountdown(seconds)}
      </span>
    </li>
  );
}

export default function HoldBar({ cart }: { cart: CartPayload | null }) {
  const holds = useMemo(() => cart?.holds ?? [], [cart]);
  const deadlines = useDeadlines(holds);
  const now = useNow(holds.length > 0);
  if (!holds.length) return null;

  // The cart's own lines name each held departure; the id stands in until one arrives.
  const index = Object.fromEntries((cart?.items ?? []).map((item) => [item.product_id, item.title]));
  return (
    <div className="ac-reveal flex items-center gap-3 border-b border-(--line) bg-(--well)/70 px-4 py-2 sm:px-5">
      <span className="tg-label shrink-0">占位 {holds.length}</span>
      <ul className="flex min-w-0 flex-1 gap-2 overflow-x-auto">
        {holds.map((hold) => {
          const deadline = deadlines[hold.hold_id];
          return (
            <HoldRow
              key={hold.hold_id}
              title={index[hold.product_id] ?? hold.product_id}
              holdId={hold.hold_id}
              seconds={
                deadline === undefined || now === 0
                  ? null
                  : Math.max(0, Math.round((deadline - now) / 1000))
              }
            />
          );
        })}
      </ul>
    </div>
  );
}
