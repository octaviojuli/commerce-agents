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

function clock(at: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/**
 * "今天 14:20", "昨天", "9月3日" — when a 历史会话 was last spoken in, on the browser's clock.
 * A timestamp from another year names it; one the API sent in a shape this side cannot read
 * says nothing at all.
 */
export function sessionTimeLabel(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const now = new Date();
  const day = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const days = Math.round((day(now).getTime() - day(at).getTime()) / 86_400_000);
  if (days === 0) return `今天 ${clock(at)}`;
  if (days === 1) return "昨天";
  if (at.getFullYear() !== now.getFullYear()) {
    return `${at.getFullYear()}年${at.getMonth() + 1}月${at.getDate()}日`;
  }
  return `${at.getMonth() + 1}月${at.getDate()}日`;
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

/**
 * What the party total beside it was made at. A 团期 the ERP quoted this customer totals at
 * the 同业价, which is what an order is booked at; one it has only listed totals at the 市场价
 * and the 同业价 is still to be found; one it has priced neither way has no total at all, and
 * one the ERP priced for part of the party only totals nothing the advisor can quote.
 */
const QUOTE_SOURCE: Record<string, string> = {
  list: "按市场价，同业价待查",
  none: "报价待查",
  partial: "报价未覆盖全部人数",
};

/**
 * A quote the ERP made for this customer says which of its two prices it gave when it gave
 * the 市场价, so the note under the total names that price rather than the settlement one.
 * A word this side does not know is not shown at all, because the keys are English.
 */
export function quoteSourceText(product: Product): string | null {
  const attrs = product.attributes ?? {};
  const source = attrs.quote_source;
  if (!source) return null;
  if (source === "customer") return attrs.price_type === "市场价" ? "按市场价" : "按同业价";
  return QUOTE_SOURCE[source] ?? null;
}

/**
 * What to call the priced row on a 团期. The ERP quotes this customer the 同业价, and a 团期 it
 * answered with its own 市场价 instead says so in `price_type`, so the row is named for the
 * price it holds rather than for the one an order is usually booked at.
 */
export function tradePriceLabel(product: Product): string {
  return product.attributes?.price_type === "市场价" ? "报价（市场价）" : "同业价";
}

/** The party a 团期 was quoted for, off `quote_party`: "2大2小" → two adults, two children. */
export function quoteParty(product: Product): { adults: number; children: number } | null {
  const match = /^(\d+)大(\d+)小$/.exec(product.attributes?.quote_party ?? "");
  return match ? { adults: Number(match[1]), children: Number(match[2]) } : null;
}

/** True while the numbers on the record are a quote the ERP made, whole or for part of it. */
function isQuoted(source?: string): boolean {
  return source === "customer" || source === "partial";
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

/**
 * "成人 ¥7,880 · 儿童 ¥4,980"; a head the ERP prices at zero is left off. A party with children
 * is the exception: the missing 儿童价 is what the advisor has to go back to the ERP for, so it
 * is said rather than left out, and the customer is not quoted the 成人价 for a child.
 */
function perHead(adult?: string, child?: string, hasChildren = false): string | null {
  const heads: [string, string | undefined][] = [
    ["成人", adult],
    ["儿童", child],
  ];
  const priced = heads
    .map(([label, raw]) => [label, Number(raw)] as const)
    .filter(([, value]) => Number.isFinite(value) && value > 0)
    .map(([label, value]) => `${label} ${formatYuan(value)}`);
  const childless = !priced.some((text) => text.startsWith("儿童"));
  if (hasChildren && childless) priced.push("儿童价未发布");
  return priced.length ? priced.join(" · ") : null;
}

/**
 * The 同业价 per head: the advisor's settlement price, which an order is booked at. A 团期 the
 * ERP has only listed carries its 市场价 in these keys instead, so it has no 同业价 to show and
 * says so beside the 市场价 row rather than repeating that price as if it were one.
 */
export function tradePerHead(product: Product): string | null {
  const attrs = product.attributes ?? {};
  if (!isQuoted(attrs.quote_source)) return null;
  return perHead(attrs.adult_price, attrs.child_price, (quoteParty(product)?.children ?? 0) > 0);
}

/** The 市场价 per head: the departure's own price, which is what the customer is shown. */
export function marketPerHead(product: Product): string | null {
  const attrs = product.attributes ?? {};
  return perHead(attrs.market_adult_price, attrs.market_child_price);
}

/** "¥7,880", the 市场价 for one adult; the customer-facing figure a card quotes on one line. */
export function marketAdultYuan(product: Product): string | null {
  const value = Number(product.attributes?.market_adult_price);
  return Number.isFinite(value) && value > 0 ? formatYuan(value) : null;
}

/**
 * "2大2小合计 25,720 元", the quote the departure was priced for. A total the ERP could not make
 * for every head — no 儿童价 on a party with children, or a quote it marked as covering part of
 * the party — reads 合计待定, because a figure short of one head is not the party's price.
 */
export function partyQuote(product: Product): string | null {
  const attrs = product.attributes ?? {};
  if (!attrs.quote_party) return null;
  const total = Number(attrs.party_quote_total);
  const children = quoteParty(product)?.children ?? 0;
  const everyHead = children === 0 || Number(attrs.child_price) > 0;
  const whole =
    attrs.quote_source !== "partial" && everyHead && Number.isFinite(total) && total > 0;
  return `${attrs.quote_party}合计${whole ? ` ${formatYuanText(total)}` : "待定"}`;
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
