// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { AskLink, BagPanel, CheckoutButton, RemoveLink, Stepper, TotalRow, useCatalogIndex, useStoreFrame } from "web-shared";
import { fetchProducts } from "@/lib/api";
import { ASSISTANT } from "@/lib/copy";
import { formatPrice, productCity, productPlace, quantityLabel, shortDate } from "@/lib/format";
import type { CartItem, CartPayload, Product } from "@/lib/types";
import { PostcardWindow } from "./PostcardWindow";

/** Stays count nights, experiences count guests; flights get no stepper. */
function quantityNoun(item: CartItem): "night" | "guest" | null {
  if (item.product_id.startsWith("AL-STAY-")) return "night";
  if (item.product_id.startsWith("AL-EXP-")) return "guest";
  return null;
}

function quantityMessage(item: CartItem, quantity: number): string {
  return quantityNoun(item) === "night" ? `把${item.title}改为 ${quantity} 晚。` : `把${item.title}改为 ${quantity} 人。`;
}

function cancellationLine(product?: Product): { text: string; free: boolean } | null {
  if (!product?.attributes) return null;
  const refundable = /^(yes|true)$/i.test(product.attributes.refundable ?? "");
  if (refundable) {
    const until = shortDate(product.attributes.free_cancellation_until);
    return { text: until ? `可免费取消至 ${until}` : "免费取消", free: true };
  }
  if (/^(no|false|none)$/i.test((product.attributes.refundable ?? "").trim())) {
    return { text: "不可退款", free: false };
  }
  return null;
}

function CartRow({ item, product }: { item: CartItem; product?: Product }) {
  const { ask } = useStoreFrame();
  const noun = quantityNoun(item);
  const cancellation = cancellationLine(product);
  const nights = noun === "night" ? item.quantity : 0;
  return (
    <div>
      <div className="flex min-w-0 items-start gap-3">
        <PostcardWindow
          city={product ? productPlace(product) : undefined}
          title={item.title}
          className="h-[56px] w-[88px] shrink-0"
        />
        <div className="min-w-0 flex-1">
          <div className="al-display truncate text-[14px] font-semibold leading-snug text-(--ink)">
            {item.title}
          </div>
          {product ? (
            <div className="al-meta mt-0.5 truncate">
              {[product.brand, productCity(product)].filter(Boolean).join(" · ")}
            </div>
          ) : null}
          {nights > 0 ? (
            <div className="mt-1.5 flex items-center gap-1.5">
              <span aria-hidden className="flex gap-[3px]">
                {Array.from({ length: Math.min(nights, 7) }, (_, i) => (
                  <span
                    key={i}
                    className="h-2 w-3.5 rounded-[3px]"
                    style={{ background: "var(--well)", border: "1px solid var(--line)" }}
                  />
                ))}
              </span>
              <span className="text-[11px] font-semibold text-(--ink-soft)">
                {nights} 晚
              </span>
            </div>
          ) : null}
          {cancellation ? (
            <div
              className={`mt-1 text-[11px] font-semibold ${
                cancellation.free ? "text-(--accent)" : "text-(--ink-soft)"
              }`}
            >
              {cancellation.free ? "✓ " : ""}
              {cancellation.text}
            </div>
          ) : null}
        </div>
      </div>
      {/* Line totals share the trip total's right edge. */}
      <div className="mt-1.5 flex items-baseline justify-between gap-3 pl-[100px]">
        <span className="text-[12px] text-(--ink-soft)">
          {formatPrice(item.price)} {quantityLabel(item.product_id, item.quantity)}
        </span>
        <span className="al-display shrink-0 text-[15px] font-bold text-(--ink)">
          {formatPrice(item.line_total)}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-2.5">
        {noun ? (
          <Stepper
            quantity={item.quantity}
            unit={noun}
            itemTitle={item.title}
            onChange={(quantity) => ask(quantity < 1 ? `把${item.title}从我的行程中移除。` : quantityMessage(item, quantity))}
          />
        ) : null}
        <RemoveLink itemTitle={item.title} onClick={() => ask(`把${item.title}从我的行程中移除。`)} />
      </div>
    </div>
  );
}

/** The trip beside the conversation. `productIndex` lets /showcase render it from fixtures. */
export default function TripPanel({
  cart,
  checkoutStaged = false,
  productIndex,
}: {
  cart: CartPayload | null;
  checkoutStaged?: boolean;
  productIndex?: Record<string, Product>;
}) {
  const catalog = useCatalogIndex(fetchProducts);
  const index = productIndex ?? catalog;
  const items = cart?.items ?? [];
  // A three-night stay counts as one booking.
  const count = items.length;
  return (
    <BagPanel
      title="本次行程"
      count={`${count} 项预订`}
      isEmpty={count === 0}
      empty={
        <>
          还没有预订任何项目。
          <br />
          问问{ASSISTANT}想去哪儿。
        </>
      }
      footer={
        <>
          <TotalRow label="行程总额" value={formatPrice(cart?.subtotal ?? 0)} note={count ? "全含价；结算前不会扣款。" : undefined} />
          <CheckoutButton staged={checkoutStaged} disabled={count === 0} prompt="结算我的行程。" />
          {count ? (
            <div className="mt-2.5 flex justify-center">
              <AskLink label="问问这次行程" prompt="帮我看看这次行程：有没有遗漏或值得调整的地方？" />
            </div>
          ) : null}
        </>
      }
    >
      <ul>
        {items.map((item, position) => (
          <li key={item.product_id} className={`py-3.5 first:pt-0 ${position > 0 ? "border-t border-dashed border-(--line)" : ""}`}>
            <CartRow item={item} product={index[item.product_id]} />
          </li>
        ))}
      </ul>
    </BagPanel>
  );
}
