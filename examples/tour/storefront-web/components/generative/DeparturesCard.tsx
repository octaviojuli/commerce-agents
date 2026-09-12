// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `present_departures`: the 团期 card. One 线路's sellable dates inside the window the advisor
 * asked about, each row the 团期 record itself: the day it leaves, what it is open for, the
 * 同业价 for one adult where the ERP published one, and the 团期 id the advisor quotes it by.
 * A tap asks for that one 团期's 报价 in the advisor's own words, which is where a price and a
 * party's total are made.
 */

import { useStoreFrame } from "web-shared";
import { dateLabel, dayLabel, formatYuan, routeHeadline } from "@/lib/format";
import type { DeparturesPayload } from "@/lib/types";
import { departureTone, SeatsPill } from "./shared";

type Item = DeparturesPayload["items"][number];

/** "10-03 周六": the weekday the payload states, or the one the date itself gives. */
function dayText(item: Item): string {
  const day = dayLabel(item.date) ?? item.date;
  const weekday = item.weekday ?? dateLabel(item.date)?.split(" ")[1] ?? "";
  return weekday ? `${day} ${weekday}` : day;
}

function Row({ item }: { item: Item }) {
  const { ask, chat } = useStoreFrame();
  const busy = chat?.busy ?? false;
  const price = item.price_adult;
  return (
    <li>
      <button
        type="button"
        className="tg-card tg-lift flex w-full items-center gap-3 px-3 py-2.5 text-left disabled:opacity-60"
        disabled={busy}
        onClick={() => ask(`看 ${item.departure.product_id} 的报价`)}
      >
        <span className="tg-num shrink-0 text-[14.5px] font-bold text-(--ink)">
          {dayText(item)}
        </span>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${departureTone(
            item.status,
          )}`}
        >
          {item.status}
        </span>
        <SeatsPill product={item.departure} />
        <span className="min-w-0 flex-1 text-right">
          {/* 同业价 for one adult; a 团期 the ERP has not priced says so rather than showing 0. */}
          {price != null && price > 0 ? (
            <span className="tg-num whitespace-nowrap text-[14px] font-bold text-(--accent)">
              <span className="tg-label mr-1">同业价</span>
              {formatYuan(price)}
            </span>
          ) : (
            <span className="tg-label">报价待查</span>
          )}
          <span className="tg-num tg-label block truncate">{item.departure.product_id}</span>
        </span>
      </button>
    </li>
  );
}

export default function DeparturesCard({
  payload,
  partial,
}: {
  payload: DeparturesPayload;
  partial?: boolean;
}) {
  const items = payload.items ?? [];
  const route = payload.route;
  const from = dayLabel(payload.window?.from);
  const to = dayLabel(payload.window?.to);
  const dateWindow = from && to ? `${from}–${to}` : null;
  return (
    <section className="tg-card ac-reveal p-5">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">
          {route?.title ?? "团期"}
        </h3>
        {route?.attributes?.route_code ? (
          <span className="tg-num tg-label">{route.attributes.route_code}</span>
        ) : null}
      </div>
      <div className="tg-label mt-1">
        {[dateWindow ? `${dateWindow} 可出发` : null, items.length ? `${items.length} 个团期` : null]
          .filter(Boolean)
          .join(" · ")}
      </div>
      {route ? (
        <div className="mt-0.5 text-[12.5px] leading-snug text-(--ink-soft)">
          {routeHeadline(route)}
        </div>
      ) : null}

      <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {items.map((item) => (
          <Row key={item.departure.product_id} item={item} />
        ))}
        {partial ? (
          <li aria-hidden className="tg-card flex flex-col gap-1.5 px-3 py-2.5">
            <div className="ac-skeleton h-4 w-2/5 rounded" />
            <div className="ac-skeleton h-3 w-3/5 rounded" />
          </li>
        ) : null}
      </ul>

      {!items.length && !partial ? (
        <p className="mt-2 text-[13px] text-(--ink-soft)">这个窗口内没有可报名的团期。</p>
      ) : null}
    </section>
  );
}
