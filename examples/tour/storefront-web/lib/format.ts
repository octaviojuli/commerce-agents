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

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function parts(iso?: string | null): [number, number, number] | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

/**
 * "2026-10-08" → "10/08", from the string parts so no timezone shifts the day. Both halves keep
 * their zero, because these dates are read down a column of 团期 rather than out of a sentence.
 */
export function dayLabel(iso?: string | null): string | null {
  const ymd = parts(iso);
  return ymd ? `${pad(ymd[1])}/${pad(ymd[2])}` : null;
}

/** "2026-10-14" → "10/14 周三"; Date.UTC keeps the weekday timezone-stable. */
export function dateLabel(iso?: string | null): string | null {
  const ymd = parts(iso);
  if (!ymd) return null;
  const weekday = WEEKDAYS[new Date(Date.UTC(ymd[0], ymd[1] - 1, ymd[2])).getUTCDay()];
  return `${dayLabel(iso)} ${weekday}`;
}

/**
 * "2026年9月10日" — the day a customer's page says a plan was made on, read off the timestamp's
 * own date so neither the reader's clock nor the server's moves it.
 */
export function fullDateLabel(iso: string): string {
  const ymd = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (ymd) return `${Number(ymd[1])}年${Number(ymd[2])}月${Number(ymd[3])}日`;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return `${at.getFullYear()}年${at.getMonth() + 1}月${at.getDate()}日`;
}

function clock(at: Date): string {
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
export function quoteSourceLabel(source?: string, priceType?: string): string | null {
  // The 定制方案 card reads the same two keys off a plan's reference price rather than a record.
  if (!source) return null;
  if (source === "customer") return priceType === "市场价" ? "按市场价" : "按同业价";
  return QUOTE_SOURCE[source] ?? null;
}

/** The same words for a 团期, off the two keys the record carries them in. */
export function quoteSourceText(product: Product): string | null {
  const attrs = product.attributes ?? {};
  return quoteSourceLabel(attrs.quote_source, attrs.price_type);
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
 * The tag row of a 线路 card, off what its reviewed document counted: whether the line stops at
 * a shop at all, how many 自费 and 赠送 items it lists, how many stops it holds the first ticket
 * for, and whether an editor has passed the document or it is still the parser's draft.
 */
export function routeTags(product: Product, limit = 5): string[] {
  const attrs = product.attributes ?? {};
  const count = (raw?: string) => {
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : 0;
  };
  const tags: string[] = [];
  const shops = count(attrs.shopping_stops);
  if (attrs.shopping_stops) tags.push(shops ? `购物店 ${shops} 家` : "纯玩");
  if (count(attrs.optional_count)) tags.push(`自费 ${count(attrs.optional_count)} 项`);
  if (count(attrs.ticket_count)) tags.push(`含门票 ${count(attrs.ticket_count)} 处`);
  if (count(attrs.gift_count)) tags.push(`赠送 ${count(attrs.gift_count)} 项`);
  if (attrs.doc === "reviewed") tags.push("已复核");
  else if (attrs.doc === "draft") tags.push("解析稿");
  return tags.slice(0, limit);
}

/**
 * The 线路 on one line: how long it runs, the city it leaves from, and the countries it covers
 * in the order the document names them. A record missing any of the three states the rest.
 */
export function routeHeadline(product: Product): string {
  const attrs = product.attributes ?? {};
  const parts: string[] = [];
  const nights = attrs.nights ? ` ${attrs.nights} 晚` : "";
  if (attrs.days) parts.push(`${attrs.days} 天${nights}`);
  if (attrs.depart_city) parts.push(`${attrs.depart_city}出发`);
  const countries = attrList(attrs.countries).join("·");
  if (countries) parts.push(countries);
  else if (attrs.region) parts.push(attrs.region);
  return parts.join(" · ");
}

/** The three facts an advisor is asked for first: which airline, which hotels, which meals. */
export function routeFacts(product: Product): Spec[] {
  const attrs = product.attributes ?? {};
  const facts: [string, string | undefined][] = [
    ["航空", attrs.airline],
    ["酒店", attrs.hotel_standard],
    ["用餐", attrs.meal_standard],
  ];
  return facts
    .filter(([, value]) => Boolean(value))
    .map(([label, value]) => ({ label, value: value as string }));
}

/** Where the line passes, the first few in the document's order; "…" stands for the rest. */
export function routePlaces(product: Product, limit = 8): string | null {
  const places = attrList(product.attributes?.places);
  if (!places.length) return null;
  return places.slice(0, limit).join(" · ") + (places.length > limit ? " …" : "");
}

/** What the picture on a route card names: the first country, or the 线路系 it belongs to. */
export function routeCover(product: Product): string {
  const attrs = product.attributes ?? {};
  return attrList(attrs.countries)[0] ?? attrs.region ?? attrs.depart_city ?? "线路";
}

export interface DepartureDate {
  date: string;
  status: string;
}

/**
 * The sellable 团期 a search stamped onto a 线路 when the advisor stated dates:
 * "2026-10-01:可报名|2026-10-03:已成团" as the dates the card's pills show, each with its state.
 * A line the window holds none of carries the attribute empty and states its nearest date
 * instead, which `adjacentDates` reads.
 */
export function routeDepartures(product: Product): DepartureDate[] {
  return attrList(product.attributes?.departures)
    .map((entry) => {
      const [date, status] = entry.split(":");
      return date ? { date, status: status ?? "" } : null;
    })
    .filter((item): item is DepartureDate => item !== null);
}

/**
 * "10/01–10/07", the window those 团期 were read out of; nothing when none was stated. A line
 * with no 团期 in the window still carries it, because the window is what the row explains.
 */
export function departuresWindow(product: Product): string | null {
  const [from, to] = (product.attributes?.departures_window ?? "").split("..");
  const start = dayLabel(from);
  const end = dayLabel(to);
  return start && end ? `${start}–${end}` : start;
}

/**
 * A 线路 the dated search kept although it sells nothing inside the window: the window asked
 * for, and the nearest 团期 outside it where the ERP has one. The 团期 footer says both, so
 * `mismatchNote` leaves this case to the card rather than stating it twice.
 */
export function adjacentDates(
  product: Product,
): { window: string | null; nearest: string | null } | null {
  const attrs = product.attributes ?? {};
  if (attrs.match !== "adjacent_date") return null;
  return { window: departuresWindow(product), nearest: dayLabel(attrs.nearest_departure) };
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

/** A 线路 in two figures, for the cards that name it on one line: how long, and where from. */
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

/**
 * What a relaxed result misses, said in the ERP's own words; nothing on an exact match, and
 * nothing on a line kept for its nearest date, whose 团期 footer states that in figures.
 */
export function mismatchNote(product: Product): string | null {
  const attrs = product.attributes ?? {};
  if (!attrs.mismatch || attrs.match === "exact" || attrs.match === "adjacent_date") return null;
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

const DIGITS = "零一二三四五六七八九";

/** "十五" → 15, over the range a 行程 is written in: 一 to 三十. */
function chineseNumber(text: string): number | null {
  const digit = (char: string) => DIGITS.indexOf(char);
  const tens = text.indexOf("十");
  if (tens < 0) {
    const value = digit(text);
    return value > 0 ? value : null;
  }
  const high = tens === 0 ? 1 : digit(text.slice(0, tens));
  const low = tens === text.length - 1 ? 0 : digit(text.slice(tens + 1));
  if (high <= 0 || low < 0) return null;
  return high * 10 + low;
}

const DAY_MARKERS: [RegExp, (match: string) => number | null][] = [
  [/第\s*(\d+)\s*天/, (match) => Number(match)],
  [new RegExp(`第\\s*([${DIGITS.slice(1)}十]+)\\s*天`), chineseNumber],
  [/\bD\s*(\d+)/i, (match) => Number(match)],
];

/**
 * The day a 行程 line is written for: "第 5 天", "第五天" and "D5" all read as 5. A label with
 * no marker in it has none, and the card falls back to the day's place in the list.
 */
export function planDayNumber(label: string): number | null {
  for (const [pattern, read] of DAY_MARKERS) {
    const match = pattern.exec(label);
    if (!match) continue;
    const value = read(match[1]);
    if (value != null && Number.isFinite(value) && value > 0 && value <= 60) return value;
  }
  return null;
}

/**
 * How many 天 a free-text date range covers, counting both ends: "2026-10-14 至 2026-10-23" is
 * 10 天, and so is "10月14日–10月23日". A range this side cannot read — one date, a month name,
 * a spell of prose — sizes nothing, and the card streams without a day count.
 */
export function planDaySpan(travelDates?: string): number | null {
  if (!travelDates) return null;
  const inRange = (days: number) => (days >= 1 && days <= 60 ? days : null);
  const iso = travelDates.match(/\d{4}-\d{2}-\d{2}/g);
  if (iso && iso.length >= 2) {
    return inRange(Math.round((Date.parse(iso[1]) - Date.parse(iso[0])) / 86_400_000) + 1);
  }
  const pair = /(\d{1,2})\s*日?\s*[–—\-~至到]\s*(?:\d{1,2}\s*月\s*)?(\d{1,2})\s*日?/.exec(
    travelDates,
  );
  return pair ? inRange(Number(pair[2]) - Number(pair[1]) + 1) : null;
}
