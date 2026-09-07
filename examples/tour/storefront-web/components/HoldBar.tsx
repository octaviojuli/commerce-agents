// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The advisor's live 占位, across the top of the workbench: one 预留 order per 团期, named by
 * the 订单号 the ERP gave it and counting down the half hour this side allows it. The clock
 * itself is `lib/holds.ts`, which the panel beside the conversation reads too.
 */

import { formatCountdown } from "@/lib/format";
import { useHoldClock } from "@/lib/holds";
import type { CartPayload } from "@/lib/types";

/** Under a minute, the strip presses. */
const URGENT_SECONDS = 60;

function HoldRow({
  title,
  holdId,
  seconds,
}: {
  title: string;
  holdId: string;
  seconds: number | null;
}) {
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
      title={`订单号 ${holdId}`}
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
  const { holds, seconds } = useHoldClock(cart);
  if (!holds.length) return null;

  // The cart's own lines name each held departure; the id stands in until one arrives.
  const index = Object.fromEntries(
    (cart?.items ?? []).map((item) => [item.product_id, item.title]),
  );
  return (
    <div className="ac-reveal flex items-center gap-3 border-b border-(--line) bg-(--well)/70 px-4 py-2 sm:px-5">
      <span className="tg-label shrink-0">占位 {holds.length}</span>
      <ul className="flex min-w-0 flex-1 gap-2 overflow-x-auto">
        {holds.map((hold) => (
          <HoldRow
            key={hold.hold_id}
            title={index[hold.product_id] ?? hold.product_id}
            holdId={hold.hold_id}
            seconds={seconds[hold.hold_id] ?? null}
          />
        ))}
      </ul>
    </div>
  );
}
