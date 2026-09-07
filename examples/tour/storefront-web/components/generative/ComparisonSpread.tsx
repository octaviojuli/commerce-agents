// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** `present_comparison`: two to four 线路 side by side, on the rows the advisor argues from. */

import type { CSSProperties } from "react";
import { attrList, dateLabel, formatYuan, formatYuanText, statusText } from "@/lib/format";
import type { ComparisonPayload, Product } from "@/lib/types";
import { MismatchNote, SeatsPill } from "./shared";

// ERP keys that are plumbing, already on the column's header, or unreadable as one cell.
const HIDDEN = new Set([
  "company",
  "tags",
  "features",
  "match",
  "mismatch",
  "quote_party",
  "party_quote_total",
  "quote_source",
  "adult_price",
  "elder_price",
  "seats_left",
  "seats_total",
  "confirm_count",
  "min_group_size",
  "reserve_hours",
  "return_date",
]);

const LABELS: Record<string, string> = {
  days: "天数",
  depart_city: "出发城市",
  route_code: "线路编号",
  period_code: "团号",
  depart_date: "出发日期",
  group_status: "成团状态",
  child_price: "儿童价",
  single_room_diff: "单房差",
};

// The rows an advisor reads out first; anything else follows in catalog order.
const ORDER = [
  "days",
  "depart_city",
  "route_code",
  "depart_date",
  "period_code",
  "group_status",
  "child_price",
  "single_room_diff",
];

const MAX_ROWS = 7;

// A 线路编号 or a 团号 tells the two apart; it is not what the dearer one buys.
const IDENTIFIERS = new Set(["route_code", "period_code"]);

const UNITS: Record<string, (raw: string) => string> = {
  days: (raw) => `${raw} 天`,
  child_price: (raw) => formatYuan(Number(raw)),
  single_room_diff: (raw) => formatYuan(Number(raw)),
};

function label(key: string): string {
  return LABELS[key] ?? key.replace(/_/g, " ");
}

function cellText(product: Product, key: string): string {
  const raw = product.attributes?.[key];
  if (raw == null || raw === "") return "—";
  if (key === "group_status") return statusText(raw) ?? raw;
  if (key === "depart_date") return dateLabel(raw) ?? raw;
  // A price the ERP has not set is no row, not a ¥0 one.
  if (UNITS[key] && Number(raw) === 0) return "—";
  return UNITS[key]?.(raw) ?? raw;
}

function rowKeys(products: Product[]): string[] {
  const seen: string[] = [];
  for (const product of products) {
    for (const key of Object.keys(product.attributes ?? {})) {
      if (!HIDDEN.has(key) && !seen.includes(key)) seen.push(key);
    }
  }
  seen.sort((a, b) => {
    const ai = ORDER.indexOf(a);
    const bi = ORDER.indexOf(b);
    return (ai < 0 ? ORDER.length : ai) - (bi < 0 ? ORDER.length : bi);
  });
  return seen.slice(0, MAX_ROWS);
}

/**
 * What the dearer line buys over the cheaper one: the rows where the two differ, or, when the
 * ERP states nothing that separates them, the tags the dearer one claims and the cheaper one
 * does not — which on this catalog is usually where the money went.
 */
function deltaBuys(high: Product, low: Product, keys: string[]): string[] {
  const buys: string[] = [];
  for (const key of keys) {
    if (IDENTIFIERS.has(key)) continue;
    const highText = cellText(high, key);
    const lowText = cellText(low, key);
    if (highText !== "—" && highText !== lowText) buys.push(`${label(key)} ${highText}`);
  }
  if (buys.length) return buys.slice(0, 3);
  const cheaper = new Set(attrList(low.attributes?.tags));
  return attrList(high.attributes?.tags)
    .filter((tag) => !cheaper.has(tag))
    .slice(0, 3);
}

function SkeletonColumn({ first, rowSpan }: { first: boolean; rowSpan: number }) {
  return (
    <div
      className={`flex min-w-0 flex-col gap-2 pt-2.5 ${first ? "sm:pr-4" : "sm:border-l sm:px-4"} border-(--line)`}
      style={{ gridRow: `span ${rowSpan}` }}
      aria-hidden
    >
      <div className="h-4" />
      <div className="ac-skeleton h-4 w-4/5 rounded" />
      <div className="ac-skeleton h-5 w-2/5 rounded" />
      <div className="ac-skeleton h-3 w-full rounded" />
      <div className="ac-skeleton h-3 w-5/6 rounded" />
    </div>
  );
}

export default function ComparisonSpread({
  payload,
  partial,
}: {
  payload: ComparisonPayload;
  partial?: boolean;
}) {
  const entries = payload.entries ?? [];
  const skeletons = partial ? (entries.length === 0 ? 2 : entries.length < 4 ? 1 : 0) : 0;
  const columns = Math.max(1, Math.min(entries.length + skeletons, 4));
  const keys = rowKeys(entries.map((entry) => entry.product));
  // Four fixed rows (tag, title, price, seats), then one per compared attribute, then the note.
  const rowSpan = 5 + keys.length;

  const delta = payload.price_delta;
  const high = delta ? entries.find((e) => e.product_id === delta.high_product_id) : undefined;
  const low = delta ? entries.find((e) => e.product_id === delta.low_product_id) : undefined;
  const buys = delta && high && low ? deltaBuys(high.product, low.product, keys) : [];

  return (
    <section className="tg-card ac-reveal p-5">
      {payload.title ? (
        <h3 className="mb-4 text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">
          {payload.title}
        </h3>
      ) : null}

      <div
        className="grid grid-cols-1 gap-y-2 sm:[grid-template-columns:var(--cmp-cols)]"
        style={{ "--cmp-cols": `repeat(${columns}, minmax(0, 1fr))` } as CSSProperties}
      >
        {entries.map((entry, i) => {
          const recommended = payload.recommended_product_id === entry.product_id;
          const product = entry.product;
          return (
            <div
              key={entry.product_id}
              className={`ac-reveal grid min-w-0 gap-y-2 pt-2.5 [grid-template-rows:subgrid] ${
                i === 0 ? "sm:pr-4" : "max-sm:mt-4 max-sm:border-t max-sm:pt-4 sm:border-l sm:px-4"
              } border-(--line)`}
              style={{
                gridRow: `span ${rowSpan}`,
                // The recommendation rides an inset shadow, leaving border-top for the
                // stacked-mobile hairline between columns.
                boxShadow: recommended ? "inset 0 3px 0 var(--accent)" : undefined,
              }}
            >
              <div className="h-4">
                {recommended ? (
                  <span className="text-[11.5px] font-semibold text-(--accent)">◆ 推荐</span>
                ) : null}
              </div>

              <div className="text-[15px] font-semibold leading-snug text-(--ink)">
                {product.title}
              </div>
              <div className="tg-num text-[16px] font-bold text-(--accent)">
                {formatYuan(product.price)}
                <span className="tg-label ml-1 font-semibold">/人起</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <SeatsPill product={product} />
              </div>

              {keys.map((key) => (
                <div key={key} className="flex items-baseline justify-between gap-2">
                  <span className="tg-label shrink-0">{label(key)}</span>
                  <span className="tg-num text-right text-[13px] font-semibold text-(--ink)">
                    {cellText(product, key)}
                  </span>
                </div>
              ))}

              {/* One slot, last in the column, so the rows above stay aligned. */}
              <div className="flex flex-col gap-1.5">
                {entry.best_for ? (
                  <p className="text-[13.5px] leading-relaxed text-(--ink-2)">{entry.best_for}</p>
                ) : null}
                {entry.pros?.map((pro) => (
                  <p key={pro} className="text-[13px] leading-snug text-(--ink)">
                    <span aria-hidden className="mr-1 text-(--ok)">
                      ✓
                    </span>
                    {pro}
                  </p>
                ))}
                {entry.cons?.map((con) => (
                  <p key={con} className="text-[13px] leading-snug text-(--ink-soft)">
                    <span aria-hidden className="mr-1">
                      −
                    </span>
                    {con}
                  </p>
                ))}
                <MismatchNote product={product} />
              </div>
            </div>
          );
        })}
        {Array.from({ length: skeletons }, (_, i) => (
          <SkeletonColumn key={`skeleton-${i}`} first={entries.length + i === 0} rowSpan={rowSpan} />
        ))}
      </div>

      {delta && buys.length ? (
        <p className="mt-4 border-t border-(--line) pt-3 text-[13.5px] text-(--ink)">
          <span className="tg-num font-bold text-(--accent)">
            贵 {formatYuanText(delta.amount)} 买到：
          </span>{" "}
          {buys.join(" · ")}
        </p>
      ) : null}

      {payload.dimensions?.length ? (
        <p className="tg-label mt-3">对比维度：{payload.dimensions.join(" · ")}</p>
      ) : null}
    </section>
  );
}
