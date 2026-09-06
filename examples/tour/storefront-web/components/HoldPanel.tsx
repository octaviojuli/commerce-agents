// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The 占位 beside the conversation: one line per held 团期, priced for the party on it. Every
 * control here is a message to 选团助手, because the hold lives in the ERP and only the agent
 * writes to it. `productIndex` lets /showcase render the panel from fixtures.
 */

import { AskLink, BagPanel, TotalRow, useCatalogIndex, useStoreFrame } from "web-shared";
import { fetchProducts } from "@/lib/api";
import { dateLabel, formatYuan, formatYuanText } from "@/lib/format";
import type { CartItem, CartPayload, Product } from "@/lib/types";

/** The route a held 团期 belongs to; the catalog lists 线路, not 团期. */
function routeOf(item: CartItem, index: Record<string, Product>): Product | undefined {
  return item.variant_of ? index[item.variant_of] : undefined;
}

function PartyStepper({ item }: { item: CartItem }) {
  const { ask, chat } = useStoreFrame();
  const busy = chat?.busy ?? false;
  const set = (quantity: number) =>
    ask(
      quantity < 1
        ? `把 ${item.title} 的占位释放掉。`
        : `${item.title} 改成 ${quantity} 个人。`,
    );
  return (
    <div className="flex items-center rounded-full border border-(--line-strong) bg-(--card)">
      <button
        type="button"
        disabled={busy}
        onClick={() => set(item.quantity - 1)}
        aria-label={`减少 ${item.title} 的人数`}
        className="px-2.5 py-0.5 text-sm text-(--ink-soft) hover:text-(--ink) disabled:opacity-40"
      >
        −
      </button>
      <span className="tg-num min-w-10 text-center text-[12.5px] font-semibold text-(--ink)">
        {item.quantity} 人
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={() => set(item.quantity + 1)}
        aria-label={`增加 ${item.title} 的人数`}
        className="px-2.5 py-0.5 text-sm text-(--ink-soft) hover:text-(--ink) disabled:opacity-40"
      >
        +
      </button>
    </div>
  );
}

function HoldLine({ item, route }: { item: CartItem; route?: Product }) {
  const { ask, chat } = useStoreFrame();
  const busy = chat?.busy ?? false;
  const attrs = route?.attributes ?? {};
  const depart = dateLabel(item.option_values?.depart_date);
  return (
    <div>
      <div className="text-[14px] font-semibold leading-snug text-(--ink)">{item.title}</div>
      <div className="tg-num tg-label mt-0.5">{item.product_id}</div>
      {route ? (
        <div className="tg-label mt-1">
          {[attrs.days ? `${attrs.days} 天` : null, attrs.hotel_level, attrs.vehicle]
            .filter(Boolean)
            .join(" · ")}
        </div>
      ) : null}
      {depart ? <div className="tg-label mt-0.5">出发 {depart}</div> : null}
      <div className="mt-1.5 flex items-baseline justify-between gap-3">
        <span className="tg-num text-[12px] text-(--ink-soft)">
          {formatYuan(item.price)} × {item.quantity} 人
        </span>
        <span className="tg-num shrink-0 text-[15px] font-bold text-(--ink)">
          {formatYuan(item.line_total)}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-2.5">
        <PartyStepper item={item} />
        <button
          type="button"
          disabled={busy}
          onClick={() => ask(`把 ${item.title} 的占位释放掉。`)}
          aria-label={`释放 ${item.title} 的占位`}
          className="text-[12px] text-(--ink-soft) underline-offset-2 hover:text-(--danger) hover:underline disabled:opacity-40"
        >
          释放占位
        </button>
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
              <AskLink label="核一遍这些占位" prompt="把我现在占的团期核对一遍：余位、截止时间和总价有没有问题？" />
            </div>
          ) : null}
        </>
      }
    >
      <ul>
        {items.map((item, position) => (
          <li
            key={item.product_id}
            className={`py-3.5 first:pt-0 ${position > 0 ? "border-t border-dashed border-(--line)" : ""}`}
          >
            <HoldLine item={item} route={routeOf(item, index)} />
          </li>
        ))}
      </ul>
    </BagPanel>
  );
}
