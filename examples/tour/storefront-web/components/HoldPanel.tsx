// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The 占位 beside the conversation: one line per 团期 this session holds, which is one 预留
 * order in the ERP. The line states the 订单号 and what is left of the half hour; it offers no
 * way to resize or release, because the ERP has neither call and the advisor does both in its
 * own backstage. `productIndex` lets /showcase render the panel from fixtures.
 */

import { AskLink, BagPanel, TotalRow, useCatalogIndex, useStoreFrame } from "web-shared";
import { fetchProducts } from "@/lib/api";
import { dateLabel, formatCountdown, formatYuan, formatYuanText } from "@/lib/format";
import { isWaitlist, lineTitle, useHoldClock } from "@/lib/holds";
import type { CartItem, CartPayload, Hold, Product } from "@/lib/types";

/** The route a held 团期 belongs to; the catalog lists 线路, not 团期. */
function routeOf(item: CartItem, index: Record<string, Product>): Product | undefined {
  return item.variant_of ? index[item.variant_of] : undefined;
}

/** The route's own line, from what the ERP's catalog carries: how long, and out of where. */
function routeLine(route: Product): string {
  const attrs = route.attributes ?? {};
  return [attrs.days ? `${attrs.days} 天` : null, attrs.depart_city ? `${attrs.depart_city}出发` : null]
    .filter(Boolean)
    .join(" · ");
}

/** The half hour left on the 预留, or the 候补 badge for a line that holds no seats. */
function HoldState({ hold, seconds, waitlisted }: { hold?: Hold; seconds: number | null; waitlisted: boolean }) {
  if (waitlisted) {
    return (
      <span className="rounded-full bg-(--warn-soft) px-2 py-0.5 text-[11.5px] font-semibold text-(--warn)">
        候补
      </span>
    );
  }
  if (!hold) return null;
  const expired = seconds !== null && seconds <= 0;
  return (
    <span className="tg-num text-[12px] font-semibold text-(--ink-2)">
      {seconds === null ? "占位 —:—" : expired ? "占位已过期" : `占位 ${formatCountdown(seconds)}`}
    </span>
  );
}

function HoldLine({
  item,
  route,
  hold,
  seconds,
}: {
  item: CartItem;
  route?: Product;
  hold?: Hold;
  seconds: number | null;
}) {
  const depart = dateLabel(item.option_values?.depart_date);
  const waitlisted = isWaitlist(item);
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-[14px] font-semibold leading-snug text-(--ink)">
          {lineTitle(item)}
        </span>
        <HoldState hold={hold} seconds={seconds} waitlisted={waitlisted} />
      </div>
      <div className="tg-num tg-label mt-0.5">
        {hold ? `订单号 ${hold.hold_id}` : item.product_id}
      </div>
      {route ? <div className="tg-label mt-1">{routeLine(route)}</div> : null}
      {depart ? <div className="tg-label mt-0.5">出发 {depart}</div> : null}
      <div className="mt-1.5 flex items-baseline justify-between gap-3">
        <span className="tg-num text-[12px] text-(--ink-soft)">
          {formatYuan(item.price)} × {item.quantity} 人
        </span>
        <span className="tg-num shrink-0 text-[15px] font-bold text-(--ink)">
          {formatYuan(item.line_total)}
        </span>
      </div>
    </div>
  );
}

/** Scrolls to the staged 报价单 once the assistant has written one; asks for it otherwise. */
function QuoteButton({ staged, disabled }: { staged: boolean; disabled: boolean }) {
  const { ask } = useStoreFrame();
  if (staged && !disabled) {
    return (
      <button
        type="button"
        onClick={() => {
          const cards = document.querySelectorAll("[data-checkout-card]");
          const card = cards[cards.length - 1];
          if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
          else ask("再把刚才那份报价单给我看一下。");
        }}
        className="mt-3 w-full rounded-(--radius) border border-(--line-strong) bg-(--card) py-2.5 text-[14px] font-semibold text-(--ink) transition hover:border-(--accent)"
      >
        查看报价单
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={() => ask("把当前占位整理成一份报价单。")}
      disabled={disabled}
      className="btn-primary mt-3 w-full"
    >
      生成报价单
    </button>
  );
}

export default function HoldPanel({
  cart,
  quoteStaged = false,
  productIndex,
}: {
  cart: CartPayload | null;
  quoteStaged?: boolean;
  productIndex?: Record<string, Product>;
}) {
  const catalog = useCatalogIndex(fetchProducts);
  const index = productIndex ?? catalog;
  const { seconds, byProduct } = useHoldClock(cart);
  const items = cart?.items ?? [];
  const people = cart?.item_count ?? 0;
  return (
    <BagPanel
      title="占位"
      count={`${items.length} 个团期 · ${people} 人`}
      isEmpty={items.length === 0}
      empty={
        <>
          还没有占位。
          <br />
          先让选团助手开出团期，再锁位置。
        </>
      }
      footer={
        <>
          <TotalRow
            label="占位合计"
            value={formatYuanText(cart?.subtotal ?? 0)}
            note={items.length ? "未收款；占位到期自动释放。" : undefined}
          />
          <QuoteButton staged={quoteStaged} disabled={items.length === 0} />
          {items.length ? (
            <div className="mt-2.5 flex justify-center">
              <AskLink label="核一遍这些占位" prompt="把我现在占的团期核对一遍：余位、成团状态和总价有没有问题？" />
            </div>
          ) : null}
        </>
      }
    >
      <ul>
        {items.map((item, position) => {
          const hold = byProduct[item.product_id];
          return (
            <li
              key={item.product_id}
              className={`py-3.5 first:pt-0 ${position > 0 ? "border-t border-dashed border-(--line)" : ""}`}
            >
              <HoldLine
                item={item}
                route={routeOf(item, index)}
                hold={hold}
                seconds={hold ? (seconds[hold.hold_id] ?? null) : null}
              />
            </li>
          );
        })}
      </ul>
      {/* The ERP has no cancel and no amend call, so neither is offered here. */}
      <p className="mt-1 border-t border-dashed border-(--line) pt-3 text-[12.5px] leading-relaxed text-(--ink-soft)">
        取消或改人数请在 ERP 后台处理。
      </p>
    </BagPanel>
  );
}
