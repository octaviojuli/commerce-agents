// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `checkout`: the held 团期 written up as the 报价单 the advisor reads to the customer. It
 * charges nothing — the deposit is taken in the 门店 and the seats stay on their own timers.
 */

import { formatYuan, formatYuanText } from "@/lib/format";
import type { CheckoutPayload } from "@/lib/types";

export default function QuoteSheet({ payload }: { payload: CheckoutPayload }) {
  const cart = payload.cart;
  const items = cart?.items ?? [];
  return (
    <section data-checkout-card className="tg-card ac-reveal p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">报价单</h3>
        <span className="tg-label">{items.length} 个团期 · {cart?.item_count ?? 0} 人</span>
      </div>

      <ul className="mt-3 flex flex-col divide-y divide-dashed divide-(--line)">
        {items.map((item) => (
          <li key={item.product_id} className="flex items-start justify-between gap-3 py-2.5">
            <div className="min-w-0">
              <div className="text-[14px] font-semibold leading-snug text-(--ink)">
                {item.title}
              </div>
              <div className="tg-num tg-label mt-0.5">
                {item.product_id} · {formatYuan(item.price)} × {item.quantity} 人
              </div>
            </div>
            <span className="tg-num shrink-0 text-[15px] font-bold text-(--ink)">
              {formatYuan(item.line_total)}
            </span>
          </li>
        ))}
      </ul>

      <div className="mt-3 flex items-baseline justify-between border-t border-(--line) pt-3">
        <span className="text-[13.5px] text-(--ink-2)">合计</span>
        <span className="tg-num text-[19px] font-bold text-(--ink)">
          {formatYuanText(cart?.subtotal ?? 0)}
        </span>
      </div>

      {payload.note ? (
        <p className="mt-2 text-[13.5px] leading-relaxed text-(--ink-2)">{payload.note}</p>
      ) : null}
      <p className="tg-label mt-2">此单不收款；占位到期自动释放，定金在门店收取。</p>
    </section>
  );
}
