// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * Orders as a storefront lists them: the home's "coming up" card and the Orders view. A vertical
 * whose orders are something else (trips) passes its own nouns, status labels, and filters.
 */

import { type ReactNode, useState } from "react";
import { type Copy, DEFAULT_COPY, useCopy } from "../copy";
import { formatDayMonth, formatMoney, formatWeekday, plural } from "../format";
import type { Order } from "../protocol";
import { AskButton, Notice, PageHeader, Panel, Pill, Segmented, Skeleton, type Tone } from "../ui";
import { useStoreFrame } from "./frame";
import { MoreLink } from "./home";
import { StorePage } from "./Shell";

/** The tone of each shared status; its words are the chrome's `orderStatuses`. */
const STATUS_TONE: Record<string, Tone> = {
  processing: "muted",
  shipped: "info",
  out_for_delivery: "info",
  delayed: "warn",
  delivered: "ok",
  cancelled: "muted",
  return_initiated: "violet",
  refunded: "ok",
};

const OPEN = new Set(["processing", "shipped", "out_for_delivery", "delayed"]);
const ISO_DAY = /\d{4}-\d{2}-\d{2}/g;

/** Not yet delivered, cancelled, or refunded. */
export function isOpen(order: Order): boolean {
  return OPEN.has(order.status);
}

/** The open orders, soonest estimate first. */
export function upcoming(orders: Order[]): Order[] {
  const day = (order: Order) => order.estimated_delivery?.match(ISO_DAY)?.[0] ?? "9999";
  return orders.filter(isOpen).sort((a, b) => day(a).localeCompare(day(b)));
}

/** Splits an estimate like "2026-08-23 (revised; was 2026-08-17)" into the date and the note. */
export function estimateOf(order: Order): { date: string; note: string | null } | null {
  const raw = order.estimated_delivery;
  if (!raw) return null;
  const match = /^(\S+)\s*\((.*)\)\s*$/.exec(raw);
  const date = match ? match[1] : raw;
  return {
    date: /^\d{4}-\d{2}-\d{2}$/.test(date) ? formatWeekday(date) : date,
    note: match ? match[2].replace(ISO_DAY, formatDayMonth) : null,
  };
}

/** The label for a status, the vertical's own word first ("return_initiated" → "Return requested"). */
export function orderStatusLabel(status: string, labels?: Record<string, string>, shared: Record<string, string> = DEFAULT_COPY.orderStatuses): string {
  return labels?.[status] ?? shared[status] ?? status.replaceAll("_", " ");
}

export function OrderStatusPill({ status, labels }: { status: string; labels?: Record<string, string> }) {
  const copy = useCopy();
  return (
    <Pill tone={STATUS_TONE[status] ?? "muted"} dot>
      {orderStatusLabel(status, labels, copy.orderStatuses)}
    </Pill>
  );
}

/** What a row hands to the assistant. */
export interface OrderHandoff {
  label: string;
  prompt: string;
}

export interface OrderNouns {
  /** "order", "trip" */
  one: string;
  /** The plural, when it is not `one` + "s". */
  many?: string;
  /** The view: "Orders", "Trips" */
  title: string;
  /** The home card: "Arriving", "Coming up" */
  cardTitle: string;
  /** The home card with nothing open: "Nothing on the way" */
  noneOpen: string;
  /** Before an open order's date: "Arrives", "Starts". */
  openVerb: string;
  /** The date line of a closed order: delivered, cancelled, or refunded. */
  closedWhen: (order: Order, date: string) => string;
  /** This vertical's words for the shared statuses ("shipped" → "Confirmed"). */
  statusLabels?: Record<string, string>;
  /** The view's filters after "All". */
  filters: { id: string; label: string; match: (order: Order) => boolean }[];
  handoff: (order: Order) => OrderHandoff;
}

/** Retail's vocabulary; a vertical spreads this and overrides what differs. */
export const ORDER_NOUNS: OrderNouns = {
  one: "order",
  title: "Orders",
  cardTitle: "Arriving",
  noneOpen: "Nothing on the way",
  openVerb: "Arrives",
  closedWhen: (order, date) => (order.status === "delivered" ? `Delivered ${date}` : `Placed ${formatDayMonth(order.placed_at)}`),
  filters: [
    { id: "open", label: "On the way", match: isOpen },
    { id: "delayed", label: "Delayed", match: (order) => order.status === "delayed" },
    { id: "closed", label: "Past", match: (order) => !isOpen(order) },
  ],
  handoff(order) {
    const ref = `order ${order.order_id}`;
    if (order.status === "delayed") return { label: "Ask why", prompt: `Why is ${ref} delayed, and when will it arrive?` };
    if (order.status === "delivered") return { label: "Ask about a return", prompt: `Can I still return something from ${ref}?` };
    if (isOpen(order)) return { label: "Ask", prompt: `Where is ${ref} right now?` };
    return { label: "Ask", prompt: `What's the status of ${ref}?` };
  },
};

function orderTitle(order: Order, copy: Copy): string {
  const [first, ...rest] = order.items;
  if (!first) return order.order_id;
  return rest.length ? copy.moreItems(first.title, rest.length) : first.title;
}

function When({ order, nouns }: { order: Order; nouns: OrderNouns }) {
  const copy = useCopy();
  const estimate = estimateOf(order);
  if (!estimate) return <span>{copy.placedOn(formatDayMonth(order.placed_at))}</span>;
  if (!isOpen(order)) return <span>{nouns.closedWhen(order, estimate.date)}</span>;
  return (
    <span className={order.status === "delayed" ? "font-semibold text-(--warn)" : ""} title={estimate.note ?? undefined}>
      {order.status === "delayed" ? copy.expected : nouns.openVerb} {estimate.date}
    </span>
  );
}

function OrderRow({ order, nouns, thumb, compact = false }: { order: Order; nouns: OrderNouns; thumb: (order: Order) => ReactNode; compact?: boolean }) {
  const { ask } = useStoreFrame();
  const copy = useCopy();
  const handoff = nouns.handoff(order);
  const estimate = estimateOf(order);
  return (
    <li className="flex items-center gap-3 border-t border-(--line) px-[18px] py-3 first:border-t-0">
      {thumb(order)}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[14px] font-semibold text-(--ink)">{orderTitle(order, copy)}</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] text-(--ink-soft)">
          {compact ? <OrderStatusPill status={order.status} labels={nouns.statusLabels} /> : <span className="tabular-nums">{order.order_id}</span>}
          <span aria-hidden>·</span>
          <When order={order} nouns={nouns} />
        </div>
        {!compact && estimate?.note && isOpen(order) ? <div className="mt-0.5 text-[12px] leading-snug text-(--ink-soft)">{estimate.note}</div> : null}
      </div>
      {compact ? null : <OrderStatusPill status={order.status} labels={nouns.statusLabels} />}
      {compact ? null : (
        <span className="hidden w-20 text-right text-[14px] font-semibold tabular-nums text-(--ink) sm:block">
          {formatMoney(order.total, order.currency)}
        </span>
      )}
      <AskButton label={handoff.label} onClick={() => ask(handoff.prompt)} />
    </li>
  );
}

/** The home's card of what is coming: open orders soonest first, else the two most recent. */
export function ArrivingPanel({
  orders,
  failed = false,
  nouns,
  thumb,
  onSeeAll,
}: {
  orders: Order[] | null;
  failed?: boolean;
  nouns: OrderNouns;
  thumb: (order: Order) => ReactNode;
  onSeeAll?: () => void;
}) {
  const copy = useCopy();
  if (!orders) return failed ? <Notice>{copy.loadFailed(nouns.title)}</Notice> : <Skeleton className="h-[188px]" />;
  // A shopper with no history gets no card; one with nothing open sees the two most recent.
  if (!orders.length) return null;
  const open = upcoming(orders).slice(0, 3);
  const shown = open.length ? open : orders.slice(0, 2);
  return (
    <Panel
      title={nouns.cardTitle}
      subtitle={open.length ? plural(orders.length, nouns.one, nouns.many) : nouns.noneOpen}
      action={onSeeAll ? <MoreLink label={copy.allOf(nouns.title)} onClick={onSeeAll} /> : null}
    >
      <ul>
        {shown.map((order) => (
          <OrderRow key={order.order_id} order={order} nouns={nouns} thumb={thumb} compact />
        ))}
      </ul>
    </Panel>
  );
}

/** The Orders (or Trips) view. */
export function OrdersView({
  orders,
  failed,
  nouns,
  subtitle,
  thumb,
}: {
  orders: Order[] | null;
  failed: boolean;
  nouns: OrderNouns;
  subtitle?: ReactNode;
  thumb: (order: Order) => ReactNode;
}) {
  const copy = useCopy();
  const [filter, setFilter] = useState("all");
  const all = orders ?? [];
  const active = nouns.filters.find((entry) => entry.id === filter);
  const shown = active ? all.filter(active.match) : all;
  return (
    <StorePage>
      <PageHeader title={nouns.title} subtitle={subtitle}>
        <Segmented
          label={copy.filterList(nouns.title)}
          value={filter}
          onChange={setFilter}
          options={[
            { id: "all", label: copy.all, count: all.length },
            ...nouns.filters.map((entry) => ({ id: entry.id, label: entry.label, count: all.filter(entry.match).length })),
          ]}
        />
      </PageHeader>
      {orders === null ? (
        failed ? (
          <Notice>{copy.loadFailedAssistant(nouns.title)}</Notice>
        ) : (
          <Skeleton className="h-[320px]" />
        )
      ) : shown.length ? (
        <Panel>
          <ul>
            {shown.map((order) => (
              <OrderRow key={order.order_id} order={order} nouns={nouns} thumb={thumb} />
            ))}
          </ul>
        </Panel>
      ) : (
        <Notice>{copy.noneHere(nouns.title)}</Notice>
      )}
    </StorePage>
  );
}
