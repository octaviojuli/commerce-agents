// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** `present_products`: a shortlist of 线路, or the 团期 of one route. */

import { useCallback, useEffect, useRef, useState } from "react";
import { useStoreFrame } from "web-shared";
import {
  adjacentDates,
  departuresWindow,
  departureSpecs,
  formatYuan,
  groupProgress,
  isDeparture,
  marketPerHead,
  partyQuote,
  quoteSourceText,
  routeDepartures,
  routeFacts,
  routeHeadline,
  routePlaces,
  routeTags,
  tradePerHead,
  tradePriceLabel,
} from "@/lib/format";
import type { Product, ProductsPayload } from "@/lib/types";
import {
  CoverBlock,
  DeparturePill,
  MismatchNote,
  SeatsPill,
  SkeletonCard,
  SpecGrid,
  StatusPill,
  Tag,
} from "./shared";

/** 纯玩 and a passed document read as good news; a shop and a parser's draft read as a caveat. */
function tagTone(tag: string): string | undefined {
  if (tag === "纯玩" || tag === "已复核") return "bg-(--ok-soft) text-(--ok)";
  if (tag.startsWith("购物店") || tag === "解析稿") return "bg-(--warn-soft) text-(--warn)";
  return undefined;
}

/**
 * A 线路 as its reviewed document states it: the picture, what the line is, the three facts an
 * advisor is asked for first, what it stops at, where it passes, its 起价, and — when the
 * advisor stated dates — the 团期 inside that window. The whole 行程 is a card of its own,
 * which the button asks for in the advisor's own words.
 */
function RouteCard({ product }: { product: Product }) {
  const { ask, chat } = useStoreFrame();
  const attrs = product.attributes ?? {};
  const headline = routeHeadline(product);
  const facts = routeFacts(product);
  const tags = routeTags(product);
  const places = routePlaces(product);
  const dates = routeDepartures(product);
  const dateWindow = departuresWindow(product);
  const adjacent = adjacentDates(product);
  const busy = chat?.busy ?? false;
  const soldOut = product.in_stock === false;
  return (
    <>
      <CoverBlock product={product} className="aspect-[4/3] w-full" />
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h4 className="text-[15.5px] font-semibold leading-snug text-(--ink)">{product.title}</h4>
        {attrs.route_code ? <span className="tg-num tg-label">{attrs.route_code}</span> : null}
      </div>
      {headline ? (
        <div className="text-[12.5px] leading-snug text-(--ink-2)">{headline}</div>
      ) : null}
      {/* One fact a row: a card in the carousel is too narrow for two, and an airline written
          out in full is long enough to be cut in half there. */}
      <SpecGrid specs={facts} cols={1} />
      {tags.length ? (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((tag) => (
            <Tag key={tag} text={tag} tone={tagTone(tag)} />
          ))}
        </div>
      ) : null}
      <MismatchNote product={product} />
      {places ? (
        <p className="line-clamp-2 text-[12.5px] leading-relaxed text-(--ink-soft)">
          <span className="tg-label mr-1">经过</span>
          {places}
        </p>
      ) : null}
      <div className="mt-auto flex flex-col gap-2 pt-1">
        {soldOut || product.price > 0 ? (
          <div className="flex items-end justify-between gap-2">
            <span className="text-[12px] text-(--ink-soft)">{soldOut ? "窗口内无余位" : ""}</span>
            {/* The cheapest 同业价 the window quoted, which is what this route costs the
                agency; the catalog states no 市场价 起价, so nothing stands beside it. A route
                the window never priced carries none at all, and says nothing rather than 0. */}
            {product.price > 0 ? (
              <span className="whitespace-nowrap text-right">
                <span className="tg-label mr-1">同业起价</span>
                <span className="tg-num text-[18px] font-bold text-(--accent)">
                  {formatYuan(product.price)}
                </span>
                <span className="tg-label ml-0.5">/人</span>
              </span>
            ) : null}
          </div>
        ) : null}
        {/* The 团期 a dated search stamped on the record: the days this line actually sells in
            that window, each saying what it is open for. A line the window holds none of says
            so in the same place, with the nearest date the ERP has, because an advisor asked
            about 国庆 needs the near miss rather than a card that goes quiet. */}
        {dates.length ? (
          <div className="border-t border-dashed border-(--line) pt-2">
            <div className="tg-label mb-1.5">{dateWindow ? `${dateWindow} 团期` : "团期"}</div>
            <div className="flex flex-wrap gap-1.5">
              {dates.map((date) => (
                <DeparturePill key={date.date} date={date.date} status={date.status} />
              ))}
            </div>
          </div>
        ) : adjacent ? (
          <div className="border-t border-dashed border-(--line) pt-2">
            <p className="tg-num rounded-(--radius) bg-(--warn-soft) px-2.5 py-1.5 text-[12px] leading-snug text-(--warn)">
              {[
                adjacent.window ? `${adjacent.window} 无团期` : "窗口内无团期",
                adjacent.nearest ? `最近 ${adjacent.nearest}` : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
        ) : null}
        <button
          type="button"
          className="chip w-full"
          disabled={busy}
          onClick={() => ask(`看 ${product.product_id} 的逐日行程`)}
        >
          看逐日行程
        </button>
      </div>
    </>
  );
}

/** A 团期: the dates, the 团号, what is left of it, and both prices this party is quoted. */
function DepartureCard({ product }: { product: Product }) {
  const attrs = product.attributes ?? {};
  const quote = partyQuote(product);
  const trade = tradePerHead(product);
  const market = marketPerHead(product);
  const source = quoteSourceText(product);
  return (
    <>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h4 className="text-[15.5px] font-semibold leading-snug text-(--ink)">{product.title}</h4>
        <StatusPill status={attrs.group_status} detail={groupProgress(product)} />
        <SeatsPill product={product} />
      </div>
      <SpecGrid specs={departureSpecs(product)} cols={1} />
      <div className="mt-auto flex flex-col gap-1 pt-1">
        {/* Both of the ERP's prices, each named: the price the order is booked at, which is
            the 同业价 unless the ERP answered with its 市场价 and said so, and the 市场价 the
            customer is shown. A 团期 the ERP has only listed has no quote of its own yet. */}
        {trade ? (
          <span className="tg-num text-[12.5px] text-(--ink-2)">
            <span className="tg-label mr-1">{tradePriceLabel(product)}</span>
            {trade}
          </span>
        ) : attrs.quote_source === "list" ? (
          <span className="tg-label">同业价待查</span>
        ) : null}
        {market ? (
          <span className="tg-num text-[12.5px] text-(--ink-soft)">
            <span className="tg-label mr-1">市场价</span>
            {market}
          </span>
        ) : null}
        {/* The party's total, and which of the two prices it was made at. */}
        <span className="flex flex-wrap items-baseline justify-end gap-x-1.5 text-right">
          {quote ? (
            <span className="tg-num text-[15px] font-bold text-(--accent)">{quote}</span>
          ) : null}
          {source ? <span className="tg-label">{source}</span> : null}
        </span>
      </div>
    </>
  );
}

function Card({
  product,
  reason,
  className = "",
}: {
  product: Product;
  reason?: string | null;
  className?: string;
}) {
  return (
    <div className={`tg-card tg-lift ac-reveal flex flex-col gap-2 p-3.5 ${className}`}>
      {isDeparture(product) ? (
        <DepartureCard product={product} />
      ) : (
        <RouteCard product={product} />
      )}
      {reason ? (
        <p className="border-t border-dashed border-(--line) pt-2 text-[13px] leading-relaxed text-(--ink-2)">
          {reason}
        </p>
      ) : null}
    </div>
  );
}

export default function RouteCarousel({
  payload,
  partial,
}: {
  payload: ProductsPayload;
  partial?: boolean;
}) {
  const layout = payload.layout ?? "carousel";
  const items = payload.items ?? [];

  // A right-edge fade shows whenever more cards sit off-screen.
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [moreRight, setMoreRight] = useState(false);
  const updateFade = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setMoreRight(el.scrollWidth - el.clientWidth - el.scrollLeft > 12);
  }, []);
  useEffect(() => {
    // Re-measure whenever the row's content changes (streaming adds cards).
    void items.length;
    void partial;
    updateFade();
    window.addEventListener("resize", updateFade);
    return () => window.removeEventListener("resize", updateFade);
  }, [items.length, partial, updateFade]);

  return (
    <section className="tg-card ac-reveal p-5">
      {payload.title ? (
        <h3 className="mb-3 text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">
          {payload.title}
        </h3>
      ) : null}
      <div className="relative">
        <div
          ref={scrollerRef}
          onScroll={updateFade}
          className={
            layout === "grid"
              ? "grid gap-3 sm:grid-cols-2"
              : layout === "list"
                ? "flex flex-col gap-3"
                : "flex gap-3 overflow-x-auto pb-1"
          }
        >
          {items.map(({ product, reason }) => (
            <Card
              key={product.product_id}
              product={product}
              reason={reason}
              className={layout === "carousel" ? "w-72 shrink-0" : ""}
            />
          ))}
          {partial ? <SkeletonCard horizontal={layout !== "carousel"} /> : null}
        </div>
        {layout === "carousel" && moreRight ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 flex w-14 items-center justify-end pb-1 text-[22px] text-(--ink-faint)"
            style={{ background: "linear-gradient(90deg, transparent, var(--card) 80%)" }}
          >
            ›
          </div>
        ) : null}
      </div>
    </section>
  );
}
