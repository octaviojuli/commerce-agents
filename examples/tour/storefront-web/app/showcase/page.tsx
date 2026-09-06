// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** Renders from fixtures; no API needed. */

import type { ReactNode } from "react";
import type { UISlotStatus } from "web-shared";
import GenerativeBlock from "@/components/generative";
import HoldBar from "@/components/HoldBar";
import HoldPanel from "@/components/HoldPanel";
import { SHOWCASE, SHOWCASE_PRODUCT_INDEX } from "@/lib/showcase-fixtures";

/** `id` sets the data-component when a component repeats. */
const SECTIONS: { component: string; id?: string; payload: unknown; status?: UISlotStatus }[] = [
  { component: "products", payload: SHOWCASE.products },
  // The same component with 团期 instead of 线路: dates, seats, deadlines, one party's total.
  { component: "products", id: "products-departures", payload: SHOWCASE.departures },
  // A shortlist frozen mid-stream: two cards plus the skeleton the third lands in.
  {
    component: "products",
    id: "products-streaming",
    payload: { ...SHOWCASE.products, items: SHOWCASE.products.items.slice(0, 2) },
    status: "partial",
  },
  { component: "comparison", payload: SHOWCASE.comparison },
  { component: "plan", payload: SHOWCASE.plan },
  { component: "guide", payload: SHOWCASE.guide },
  { component: "checkout", payload: SHOWCASE.checkout },
  { component: "order_status", payload: SHOWCASE.order_status },
];

function Section({ name, children }: { name: string; children: ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="tg-label mb-3">{name}</h2>
      <div data-component={name}>{children}</div>
    </section>
  );
}

export default function ShowcasePage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <p className="tg-label">ACME 旅行社 组件预览（固定数据）</p>
      <Section name="hold-bar">
        <div className="overflow-hidden rounded-(--radius) border border-(--line)">
          <HoldBar cart={SHOWCASE.cart} />
        </div>
      </Section>
      {SECTIONS.map(({ component, id = component, payload, status = "final" }) => (
        <Section key={id} name={id}>
          <GenerativeBlock block={{ component, payload }} status={status} />
        </Section>
      ))}
      <Section name="holds">
        <div className="flex h-[460px] max-w-[380px] flex-col overflow-hidden rounded-(--radius-lg) border border-(--line) bg-(--card) shadow-(--shadow)">
          <HoldPanel cart={SHOWCASE.cart} productIndex={SHOWCASE_PRODUCT_INDEX} />
        </div>
      </Section>
    </main>
  );
}
