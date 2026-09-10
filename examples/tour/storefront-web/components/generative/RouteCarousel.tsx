// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** `present_products`: a shortlist of 线路, or the 团期 of one route. */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  departureSpecs,
  featureSentence,
  formatYuan,
  groupProgress,
  isDeparture,
  marketPerHead,
  partyQuote,
  quoteSourceText,
  routeSpecs,
  routeTags,
  tradePerHead,
  tradePriceLabel,
} from "@/lib/format";
import { api } from "@/lib/api";
import { downloadAttachment } from "@/lib/attachments";
import type { Product, ProductsPayload } from "@/lib/types";
import { MismatchNote, SeatsPill, SkeletonCard, SpecGrid, StatusPill, Tag } from "./shared";

/** A route: what the ERP's catalog says about it, its tags, and its 同业起价. */
function RouteCard({ product }: { product: Product }) {
  const attrs = product.attributes ?? {};
  const tags = routeTags(product);
  const sentence = featureSentence(product);
  const soldOut = product.in_stock === false;
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h4 className="text-[15.5px] font-semibold leading-snug text-(--ink)">{product.title}</h4>
        {attrs.route_code ? <span className="tg-num tg-label">{attrs.route_code}</span> : null}
      </div>
      {tags.length ? (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((tag) => (
            <Tag key={tag} text={tag} />
          ))}
        </div>
      ) : null}
      {/* One spec a row: a card in the carousel is too narrow for two, and an ERP city name
          is long enough to be cut in half there. */}
      <SpecGrid specs={routeSpecs(product)} cols={1} />
      <MismatchNote product={product} />
      <AttachmentButton product={product} />
      {sentence ? (
        <p className="line-clamp-2 text-[13px] leading-relaxed text-(--ink-soft)">{sentence}</p>
      ) : null}
      {soldOut || product.price > 0 ? (
        <div className="mt-auto flex items-end justify-between gap-2 pt-1">
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
    </>
  );
}

/**
 * The 行程附件 the 线路 links, as a download under the name the agency gave it; nothing when
 * the record names none. The button says what happened until the next click.
 */
function AttachmentButton({ product }: { product: Product }) {
  const name = product.attributes?.attachment ?? "";
  const [state, setState] = useState<"idle" | "busy" | "done" | "failed">("idle");
  const [detail, setDetail] = useState("");
  if (!name) return null;
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1).toLowerCase() : "";
  const label =
    state === "busy" ? "下载中…" : state === "done" ? "已下载" : state === "failed" ? "重试下载" : "行程附件";
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        className="chip self-start"
        disabled={state === "busy"}
        title={name}
        onClick={async () => {
          setState("busy");
          const result = await downloadAttachment(api, product.product_id, name);
          if (result.ok) {
            setState("done");
            setDetail("");
          } else {
            setState("failed");
            setDetail(result.detail);
          }
        }}
      >
        <span aria-hidden>↓</span> {label}
        {ext ? <span className="tg-label ml-1 uppercase">{ext}</span> : null}
      </button>
      {detail ? <span className="text-[12px] text-(--danger,#b3261e)">{detail}</span> : null}
    </div>
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
              className={layout === "carousel" ? "w-64 shrink-0" : ""}
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
