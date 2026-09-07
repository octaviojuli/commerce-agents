// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** How the advisor says a price, a date, and a 团期's state. Every figure is 人民币. */

import type { Product } from "./types";

const GROUPED = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });

/** "¥7,880" — the figure on a card. */
export function formatYuan(value: number): string {
  return `¥${GROUPED.format(value)}`;
}

/** "7,880 元" — the figure inside a sentence, as an advisor quotes it. */
export function formatYuanText(value: number): string {
  return `${GROUPED.format(value)} 元`;
}

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function parts(iso?: string | null): [number, number, number] | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

/** "2026-10-14" → "10/14", from the string parts so no timezone shifts the day. */
function dayLabel(iso?: string | null): string | null {
  const ymd = parts(iso);
  return ymd ? `${ymd[1]}/${ymd[2]}` : null;
}

/** "2026-10-14" → "10/14 周三"; Date.UTC keeps the weekday timezone-stable. */
export function dateLabel(iso?: string | null): string | null {
  const ymd = parts(iso);
  if (!ymd) return null;
  const weekday = WEEKDAYS[new Date(Date.UTC(ymd[0], ymd[1] - 1, ymd[2])).getUTCDay()];
  return `${ymd[1]}/${ymd[2]} ${weekday}`;
}

/** The three states `group_status` carries, in the words the advisor uses. */
const STATUS_TEXT: Record<string, string> = {
  confirmed: "已成团",
  pending: "待成团",
  waitlist: "候补",
};

export function statusText(status?: string): string | null {
  if (!status) return null;
  return STATUS_TEXT[status] ?? status;
}

/** "1/2" — how far a 待成团 团期 is from the ERP's own 最低成团人数; nothing on the others. */
export function groupProgress(product: Product): string | null {
  const attrs = product.attributes ?? {};
  if (attrs.group_status !== "pending") return null;
  const confirmed = Number(attrs.confirm_count);
  const minimum = Number(attrs.min_group_size);
  if (!Number.isFinite(confirmed) || !Number.isFinite(minimum) || minimum <= 0) return null;
  return `${confirmed}/${minimum}`;
}

/** Which of the ERP's three prices a quote came from, so the advisor knows what they read. */
const QUOTE_SOURCE: Record<string, string> = {
  customer: "同行价",
  list: "挂牌价",
  none: "暂无报价",
};

export function quoteSourceText(source?: string): string | null {
  if (!source) return null;
  return QUOTE_SOURCE[source] ?? source;
}

/** A pipe-joined attribute (tags, features) as its parts. */
export function attrList(raw?: string): string[] {
  return (raw ?? "").split("|").filter(Boolean);
}

/**
 * The ERP's tags for a route: `labels` holds the first four, and the joined attribute is
 * there for a record that carries no labels. An editor may have written a whole sentence
 * into one, so the chip that shows it is the one that cuts it.
 */
export function routeTags(product: Product, limit = 4): string[] {
  const tags = product.labels?.length ? product.labels : attrList(product.attributes?.tags);
  return tags.filter(Boolean).slice(0, limit);
}

/**
 * The one feature written as a sentence; the ERP's other features are place names. The
 * backend already picks it as the record's short description, so that is read first.
 */
export function featureSentence(product: Product): string | null {
  if (product.short_description) return product.short_description;
  const sentence = attrList(product.attributes?.features).find(
    (text) => text.includes("，") || text.includes("。"),
  );
  return sentence ?? null;
}

/** "29:41"; a hold past its deadline reads 已过期 at the call site. */
export function formatCountdown(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** True for a 团期: it belongs to a route and carries a departure date. */
export function isDeparture(product: Product): boolean {
  return Boolean(product.variant_of || product.attributes?.depart_date);
}

export interface Spec {
  label: string;
  value: string;
}

/** What the ERP's catalog states about a 线路: how long it runs, and where from. */
export function routeSpecs(product: Product): Spec[] {
  const attrs = product.attributes ?? {};
  const specs: Spec[] = [];
  if (attrs.days) specs.push({ label: "天数", value: `${attrs.days} 天` });
  if (attrs.depart_city) specs.push({ label: "出发城市", value: attrs.depart_city });
  return specs;
}

/** The 团期 figures: the dates it runs between, and the 团号 the advisor quotes it by. */
export function departureSpecs(product: Product): Spec[] {
  const attrs = product.attributes ?? {};
  const specs: Spec[] = [];
  const depart = dateLabel(attrs.depart_date);
  if (depart) {
    const back = dayLabel(attrs.return_date);
    specs.push({ label: "出发日期", value: back ? `${depart} – ${back}` : depart });
  }
  if (attrs.period_code) specs.push({ label: "团号", value: attrs.period_code });
  return specs;
}

/** "成人 ¥7,880 · 儿童 ¥4,980"; a head the ERP prices at zero is left off. */
export function perHeadPrices(product: Product): string | null {
  const attrs = product.attributes ?? {};
  const heads: [string, string | undefined][] = [
    ["成人", attrs.adult_price],
    ["儿童", attrs.child_price],
  ];
  const priced = heads
    .map(([label, raw]) => [label, Number(raw)] as const)
    .filter(([, value]) => Number.isFinite(value) && value > 0)
    .map(([label, value]) => `${label} ${formatYuan(value)}`);
  return priced.length ? priced.join(" · ") : null;
}

/** "2大2小合计 25,720 元", the quote the departure was priced for; nothing unpriced. */
export function partyQuote(product: Product): string | null {
  const attrs = product.attributes ?? {};
  const total = Number(attrs.party_quote_total);
  if (!attrs.quote_party || !Number.isFinite(total) || total <= 0) return null;
  return `${attrs.quote_party}合计 ${formatYuanText(total)}`;
}

/** What a relaxed result misses, said in the ERP's own words; nothing on an exact match. */
export function mismatchNote(product: Product): string | null {
  const attrs = product.attributes ?? {};
  if (!attrs.mismatch || attrs.match === "exact") return null;
  return attrs.mismatch;
}

/**
 * 余位 as a state the advisor can act on: none, tight, or plenty. A 团期 with seats left but
 * too few for this party is tight, not gone — the seat count beside it says how many.
 */
export function seatsTone(product: Product): "gone" | "tight" | "open" | null {
  const left = Number(product.attributes?.seats_left);
  if (!Number.isFinite(left)) return null;
  if (left <= 0) return "gone";
  return left <= 2 || product.in_stock === false ? "tight" : "open";
}
