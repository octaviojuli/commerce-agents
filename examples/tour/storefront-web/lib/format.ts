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

const STATUS_TEXT: Record<string, string> = {
  confirmed: "已成团",
  pending: "待成团",
  closed: "已截止",
};

export function statusText(status?: string): string | null {
  if (!status) return null;
  return STATUS_TEXT[status] ?? status;
}

/** A pipe-joined attribute (highlights, fit_tags) as its parts. */
export function attrList(raw?: string): string[] {
  return (raw ?? "").split("|").filter(Boolean);
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

/** The trade-offs an advisor reads out of a route, in the order they get said. */
export function routeSpecs(product: Product): Spec[] {
  const attrs = product.attributes ?? {};
  const specs: Spec[] = [];
  if (attrs.days) specs.push({ label: "天数", value: `${attrs.days} 天${attrs.nights ? ` ${attrs.nights} 晚` : ""}` });
  if (attrs.hotel_level) specs.push({ label: "住宿标准", value: attrs.hotel_level });
  if (attrs.vehicle) specs.push({ label: "车型", value: attrs.vehicle });
  if (attrs.shopping_stops) specs.push({ label: "购物店", value: `${attrs.shopping_stops} 个` });
  return specs;
}

/** The 团期 figures: dates, seats, and this party's total. */
export function departureSpecs(product: Product): Spec[] {
  const attrs = product.attributes ?? {};
  const specs: Spec[] = [];
  const depart = dateLabel(attrs.depart_date);
  if (depart) {
    const back = dayLabel(attrs.return_date);
    specs.push({ label: "出发日期", value: back ? `${depart} – ${back}` : depart });
  }
  if (attrs.seats_left && attrs.seats_total) {
    specs.push({ label: "余位", value: `${attrs.seats_left} / ${attrs.seats_total}` });
  }
  const status = statusText(attrs.group_status);
  if (status) specs.push({ label: "成团状态", value: status });
  const deadline = dayLabel(attrs.booking_deadline);
  if (deadline) specs.push({ label: "报名截止", value: deadline });
  return specs;
}

/** "2大2小合计 25,720 元", the quote the departure was priced for. */
export function partyQuote(product: Product): string | null {
  const attrs = product.attributes ?? {};
  const total = Number(attrs.party_quote_total);
  if (!attrs.quote_party || !Number.isFinite(total)) return null;
  return `${attrs.quote_party}合计 ${formatYuanText(total)}`;
}

/** What a relaxed result misses, said in the ERP's own words; nothing on an exact match. */
export function mismatchNote(product: Product): string | null {
  const attrs = product.attributes ?? {};
  if (!attrs.mismatch || attrs.match === "exact") return null;
  return attrs.mismatch;
}

/** 余位 as a state the advisor can act on: none, tight, or plenty. */
export function seatsTone(product: Product): "gone" | "tight" | "open" | null {
  const left = Number(product.attributes?.seats_left);
  if (!Number.isFinite(left)) return null;
  if (left <= 0 || product.in_stock === false) return "gone";
  return left <= 2 ? "tight" : "open";
}
