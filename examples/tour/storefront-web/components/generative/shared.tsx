// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** The pieces the cards share: the specs an advisor reads out, and the state of a 团期. */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  dayLabel,
  formatYuan,
  mismatchNote,
  routeCover,
  routeHeadline,
  seatsTone,
  statusText,
  type Spec,
} from "@/lib/format";
import type { Product } from "@/lib/types";

const COPIED_MS = 2000;

/**
 * Text the advisor hands on — a customer's link, the plan the 计调 works from — put on the
 * clipboard, with 已复制 said for a moment after. A browser that refuses the clipboard, or a
 * page not served over a secure context, leaves the advisor the text selected in `fieldRef`
 * to copy themselves, so the card that uses this renders the text in a field of its own.
 */
export function useCopy<T extends HTMLInputElement | HTMLTextAreaElement>(text: string) {
  const fieldRef = useRef<T>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      fieldRef.current?.focus();
      fieldRef.current?.select();
    }
  }, [text]);

  return { copied, copy, fieldRef };
}

const STATUS_TONE: Record<string, string> = {
  confirmed: "bg-(--ok-soft) text-(--ok)",
  pending: "bg-(--warn-soft) text-(--warn)",
  waitlist: "bg-(--danger-soft) text-(--danger)",
};

// An ERP tag is free text: most are two words, and some are a whole sentence. The chip keeps
// the first few characters and hands the rest to the title.
const MAX_TAG_CHARS = 12;

export function Tag({ text, tone }: { text: string; tone?: string }) {
  const short = text.length > MAX_TAG_CHARS ? `${text.slice(0, MAX_TAG_CHARS)}…` : text;
  return (
    <span
      title={text}
      className={`rounded-full px-2 py-0.5 text-[11.5px] ${tone ?? "bg-(--well) text-(--ink-2)"}`}
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

const DEPARTURE_TONE: Record<string, string> = {
  可报名: "bg-(--accent-soft) text-(--accent-ink)",
  已成团: "bg-(--ok-soft) text-(--ok)",
  满员: "bg-(--danger-soft) text-(--danger)",
  截止: "bg-(--well) text-(--ink-faint)",
};

/** The ground a 团期's state is shown on, wherever it is shown. */
export function departureTone(status?: string): string {
  return DEPARTURE_TONE[status ?? ""] ?? "bg-(--well) text-(--ink-2)";
}

/**
 * One sellable 团期 as a pill: the day it leaves and what it is open for, tinted by the state.
 * The route card's footer row and the 团期 card both read it, so the colours mean one thing.
 */
export function DeparturePill({
  date,
  status,
  weekday,
}: {
  date?: string | null;
  status?: string;
  weekday?: string;
}) {
  const day = dayLabel(date);
  const tone = departureTone(status);
  return (
    <span
      className={`tg-num inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${tone}`}
    >
      {day ?? date}
      {weekday ? <span className="font-normal opacity-80">{weekday}</span> : null}
      {status ? <span className="font-semibold">{status}</span> : null}
    </span>
  );
}

/**
 * The picture a 线路 card carries until the agency's own photos are in: a soft wash named for
 * the first country the line covers. The angle and the wash come off the record's id, so one
 * route keeps one picture wherever the card is drawn.
 */
export function CoverBlock({ product, className = "" }: { product: Product; className?: string }) {
  const name = routeCover(product);
  let hash = 0;
  for (const char of product.product_id) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return (
    <div
      aria-hidden
      className={`flex items-end justify-start overflow-hidden rounded-(--radius) p-2.5 ${className}`}
      style={{
        background: `linear-gradient(${120 + (hash % 120)}deg, var(--accent-soft), var(--well) 72%)`,
      }}
    >
      <span className="truncate text-[15px] font-bold tracking-[0.06em] text-(--accent-ink) opacity-70">
        {name}
      </span>
    </div>
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

/** A route or 团期 named on a plan step, or a foothold under a 聚焦卡's chips. */
export function MiniProductCard({ product }: { product: Product }) {
  const headline = routeHeadline(product);
  return (
    <div className="tg-card tg-lift min-w-0 px-3 py-2">
      <div className="truncate text-[13.5px] font-semibold text-(--ink)" title={product.title}>
        {product.title}
      </div>
      {headline ? (
        <div className="mt-0.5 truncate text-[12px] leading-snug text-(--ink-soft)">{headline}</div>
      ) : null}
      {/* A 线路 the window never priced carries no 起价, and says nothing rather than ¥0. */}
      {product.price > 0 ? (
        <div className="tg-num mt-0.5 text-[13px] font-bold text-(--accent)">
          {formatYuan(product.price)}
        </div>
      ) : null}
    </div>
  );
}

/** Sized like a route card so a streaming shortlist does not reflow. */
export function SkeletonCard({ horizontal = false }: { horizontal?: boolean }) {
  return (
    <div
      className={`tg-card flex flex-col gap-2 p-3.5 ${horizontal ? "w-full" : "w-72 shrink-0"}`}
      aria-hidden
    >
      <div className="ac-skeleton aspect-[4/3] w-full rounded-(--radius)" />
      <div className="ac-skeleton h-4 w-4/5 rounded" />
      <div className="ac-skeleton h-3 w-3/5 rounded" />
      <div className="ac-skeleton h-3 w-full rounded" />
      <div className="ac-skeleton h-5 w-2/5 rounded" />
    </div>
  );
}
