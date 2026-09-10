// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `present_shortlist`: the 团期 the advisor hands the customer, each on the 线路 it departs
 * from, with the link the customer answers on. The link is minted on the finished call, so a
 * still-streaming card says the link is coming instead of showing one.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  dateLabel,
  groupProgress,
  marketAdultYuan,
  partyQuote,
  routeSpecs,
  tradePriceLabel,
} from "@/lib/format";
import type { Product, ShortlistPayload } from "@/lib/types";
import { SeatsPill, StatusPill } from "./shared";

const COPIED_MS = 2000;

/**
 * The 线路 on one line, in the order `routeSpecs` states it: how many days, then the city it
 * leaves from with 出发 after it, because a place name alone does not say what it is.
 */
function tradeOffs(route: Product): string {
  return routeSpecs(route)
    .map((spec) => (spec.label === "出发城市" ? `${spec.value}出发` : spec.value))
    .join(" · ");
}

/**
 * One 团期: when it leaves, what it is, what is left of it, and both prices — the one this
 * party's total is made at, named for which of the two it is, and the 市场价 for one adult,
 * which is the figure the customer reads on the page this card is sent to.
 */
function Row({ departure, route }: { departure: Product; route: Product }) {
  const attrs = departure.attributes ?? {};
  const depart = dateLabel(attrs.depart_date);
  const specs = tradeOffs(route);
  const quote = partyQuote(departure);
  const market = marketAdultYuan(departure);
  return (
    <li className="ac-reveal flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 border-t border-dashed border-(--line) py-3 first:border-t-0 first:pt-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {depart ? (
            <span className="tg-num text-[15.5px] font-bold leading-snug text-(--ink)">
              {depart}
            </span>
          ) : null}
          <StatusPill status={attrs.group_status} detail={groupProgress(departure)} />
          <SeatsPill product={departure} />
        </div>
        <div className="mt-1 truncate text-[14px] font-semibold text-(--ink-2)">{route.title}</div>
        {specs ? (
          <div className="mt-0.5 text-[12.5px] leading-snug text-(--ink-soft)">{specs}</div>
        ) : null}
      </div>
      {quote || market ? (
        <div className="shrink-0 text-right">
          {quote ? (
            <div className="tg-num text-[15px] font-bold text-(--accent)">
              <span className="tg-label mr-1">{tradePriceLabel(departure)}</span>
              {quote}
            </div>
          ) : null}
          {market ? (
            <div className="tg-num tg-label mt-0.5">市场价 成人 {market}</div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/** The link, ready to paste; the clipboard is asked first and the field is selected if it refuses. */
function ShareLink({ url }: { url: string }) {
  const fieldRef = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // No clipboard permission, or no secure context: hand the advisor the selected text.
      fieldRef.current?.focus();
      fieldRef.current?.select();
    }
  }, [url]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="tg-label shrink-0">客人链接</span>
      <input
        ref={fieldRef}
        readOnly
        value={url}
        aria-label="客人链接"
        onFocus={(event) => event.currentTarget.select()}
        className="tg-num min-w-0 flex-1 rounded-(--radius) border border-(--line) bg-(--well) px-2.5 py-1.5 text-[12.5px] text-(--ink-2)"
      />
      <button type="button" onClick={copy} className="chip shrink-0">
        {copied ? "已复制" : "复制链接"}
      </button>
    </div>
  );
}

export default function ShortlistCard({
  payload,
  partial,
}: {
  payload: ShortlistPayload;
  partial?: boolean;
}) {
  const items = payload.items ?? [];
  return (
    <section className="tg-card ac-reveal p-5">
      {payload.title ? (
        <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">
          {payload.title}
        </h3>
      ) : null}
      {payload.note ? (
        <p className="mt-1 text-[14px] leading-relaxed text-(--ink-2)">{payload.note}</p>
      ) : null}

      <ul className="mt-3 flex flex-col">
        {items.map(({ departure, route }) => (
          <Row key={departure.product_id} departure={departure} route={route} />
        ))}
        {partial ? (
          <li
            className="flex flex-col gap-1.5 border-t border-dashed border-(--line) py-3 first:border-t-0 first:pt-0"
            aria-hidden
          >
            <div className="ac-skeleton h-4 w-2/5 rounded" />
            <div className="ac-skeleton h-3 w-3/5 rounded" />
          </li>
        ) : null}
      </ul>

      <div className="mt-3 border-t border-(--line) pt-3">
        {payload.share_url ? (
          <ShareLink url={payload.share_url} />
        ) : (
          <p className="tg-label">生成分享链接中…</p>
        )}
      </div>
    </section>
  );
}
