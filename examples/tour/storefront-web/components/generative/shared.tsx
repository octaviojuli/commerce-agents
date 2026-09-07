// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** The pieces the cards share: the specs an advisor reads out, and the state of a 团期. */

import { formatYuan, mismatchNote, seatsTone, statusText, type Spec } from "@/lib/format";
import type { Product } from "@/lib/types";

const STATUS_TONE: Record<string, string> = {
  confirmed: "bg-(--ok-soft) text-(--ok)",
  pending: "bg-(--warn-soft) text-(--warn)",
  waitlist: "bg-(--danger-soft) text-(--danger)",
};

// An ERP tag is free text: most are two words, and some are a whole sentence. The chip keeps
// the first few characters and hands the rest to the title.
const MAX_TAG_CHARS = 12;

export function Tag({ text }: { text: string }) {
  const short = text.length > MAX_TAG_CHARS ? `${text.slice(0, MAX_TAG_CHARS)}…` : text;
  return (
    <span
      title={text}
      className="rounded-full bg-(--well) px-2 py-0.5 text-[11.5px] text-(--ink-2)"
    >
      {short}
    </span>
  );
}

/** 已成团 / 待成团 / 候补, tinted by which one it is; `detail` is the 待成团 count. */
export function StatusPill({ status, detail }: { status?: string; detail?: string | null }) {
  const label = statusText(status);
  if (!label || !status) return null;
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${STATUS_TONE[status] ?? "bg-(--well) text-(--ink-2)"}`}
    >
      {label}
      {detail ? <span className="tg-num ml-1 font-bold">{detail}</span> : null}
    </span>
  );
}

/** 余位 5/6; a departure with none left says so instead. */
export function SeatsPill({ product }: { product: Product }) {
  const tone = seatsTone(product);
  const attrs = product.attributes ?? {};
  if (!tone) return null;
  if (tone === "gone") {
    return (
      <span className="rounded-full bg-(--danger-soft) px-2 py-0.5 text-[11.5px] font-semibold text-(--danger)">
        无余位
      </span>
    );
  }
  return (
    <span
      className={`tg-num rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${
        tone === "tight" ? "bg-(--warn-soft) text-(--warn)" : "bg-(--well) text-(--ink-2)"
      }`}
    >
      余位 {attrs.seats_left}/{attrs.seats_total}
    </span>
  );
}

/** 标签: 数值 rows, the shape an advisor reads a record's figures in; a long value gets its own. */
export function SpecGrid({ specs, cols = 2 }: { specs: Spec[]; cols?: 1 | 2 }) {
  if (!specs.length) return null;
  return (
    <dl className={`grid gap-x-4 gap-y-1.5 ${cols === 1 ? "grid-cols-1" : "grid-cols-2"}`}>
      {specs.map((spec) => (
        <div key={spec.label} className="flex items-baseline justify-between gap-2">
          <dt className="tg-label shrink-0">{spec.label}</dt>
          <dd className="tg-num truncate text-right text-[13px] font-semibold text-(--ink)">
            {spec.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** What a relaxed result misses, in the ERP's own Chinese; nothing on an exact match. */
export function MismatchNote({ product }: { product: Product }) {
  const note = mismatchNote(product);
  if (!note) return null;
  return (
    <p className="rounded-(--radius) bg-(--warn-soft) px-2.5 py-1.5 text-[12.5px] leading-snug text-(--warn)">
      {note}
    </p>
  );
}

/** A route or 团期 named on a plan step. */
export function MiniProductCard({ product }: { product: Product }) {
  return (
    <div className="tg-card tg-lift min-w-0 px-3 py-2">
      <div className="truncate text-[13.5px] font-semibold text-(--ink)">{product.title}</div>
      <div className="tg-num mt-0.5 text-[13px] font-bold text-(--accent)">
        {formatYuan(product.price)}
      </div>
    </div>
  );
}

/** Sized like a route card so a streaming shortlist does not reflow. */
export function SkeletonCard({ horizontal = false }: { horizontal?: boolean }) {
  return (
    <div
      className={`tg-card flex flex-col gap-2 p-3.5 ${horizontal ? "w-full" : "w-64 shrink-0"}`}
      aria-hidden
    >
      <div className="ac-skeleton h-4 w-4/5 rounded" />
      <div className="ac-skeleton h-3 w-3/5 rounded" />
      <div className="ac-skeleton h-3 w-full rounded" />
      <div className="ac-skeleton h-5 w-2/5 rounded" />
    </div>
  );
}
