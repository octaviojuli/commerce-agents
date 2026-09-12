// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * Renders from fixtures; no API needed. It is a development page: `NEXT_PUBLIC_TOUR_SHOWCASE=1`
 * is what turns it on, which the `dev` script sets and `build` and `start` do not, so a
 * deployment of the workbench answers this path with nothing.
 */

import type { ReactNode } from "react";
import { CopyProvider, mergeCopy, type UISlotStatus } from "web-shared";
import GenerativeBlock from "@/components/generative";
import HoldBar from "@/components/HoldBar";
import HoldPanel from "@/components/HoldPanel";
import { BRAND } from "@/lib/brand";
import { TOUR_COPY } from "@/lib/copy";
import { SHOWCASE, SHOWCASE_PRODUCT_INDEX } from "@/lib/showcase-fixtures";

// The page mounts the panels without a StoreShell, so it carries the copy itself.
const COPY = mergeCopy(TOUR_COPY);

const ENABLED = process.env.NEXT_PUBLIC_TOUR_SHOWCASE === "1";

/** `id` sets the data-component when a component repeats. */
const SECTIONS: { component: string; id?: string; payload: unknown; status?: UISlotStatus }[] = [
  { component: "products", payload: SHOWCASE.products },
  // 线路 as their reviewed documents state them: the picture, the three facts, what the line
  // stops at, where it passes, and the button that asks for the whole 行程.
  { component: "products", id: "products-routes", payload: SHOWCASE.route_products },
  // The same cards after the advisor stated dates: each carries the 团期 it sells in the window.
  { component: "products", id: "products-routes-dated", payload: SHOWCASE.route_products_dated },
  // One 线路 as its 逐日行程, every day open, the 去程 and 回程 as flight strips.
  { component: "route_days", payload: SHOWCASE.route_days },
  // A short line whose document is still the parser's draft.
  { component: "route_days", id: "route_days-draft", payload: SHOWCASE.route_days_short },
  // The same call still streaming: the head and the first days, with the next one sized.
  {
    component: "route_days",
    id: "route_days-streaming",
    payload: SHOWCASE.route_days_streaming,
    status: "partial",
  },
  // The 团期 of one 线路 in the window the advisor asked about.
  { component: "departures", payload: SHOWCASE.route_departures },
  // The same component with 团期 instead of 线路: dates, seats, deadlines, one party's total.
  { component: "products", id: "products-departures", payload: SHOWCASE.departures },
  // A shortlist frozen mid-stream: two cards plus the skeleton the third lands in.
  {
    component: "products",
    id: "products-streaming",
    payload: { ...SHOWCASE.products, items: SHOWCASE.products.items.slice(0, 2) },
    status: "partial",
  },
  // A request too broad to shortlist: one question, the catalog's groups as chips the advisor
  // holds several of before the bar sends them as one filter.
  { component: "focus", payload: SHOWCASE.focus },
  // The 行程附件 the advisor asked for, as downloads under the agency's own file names.
  { component: "attachments", payload: SHOWCASE.attachments },
  // The 团期 the advisor sends the customer, with the link they answer on.
  { component: "shortlist", payload: SHOWCASE.shortlist },
  // The same call still streaming: one 团期 in, and no share link until it finishes.
  {
    component: "shortlist",
    id: "shortlist-streaming",
    payload: SHOWCASE.shortlist_streaming,
    status: "partial",
  },
  // One version of a 定制方案: what it changed, what the 计调 still has to price, and the link.
  { component: "itinerary", payload: SHOWCASE.itinerary },
  // The same call still streaming: the days written so far, with the rest of them sized.
  {
    component: "itinerary",
    id: "itinerary-streaming",
    payload: SHOWCASE.itinerary_streaming,
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
  if (!ENABLED) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-12">
        <p className="tg-label">404 · 页面不存在</p>
      </main>
    );
  }
  return (
    <CopyProvider value={COPY}>
      <main className="mx-auto max-w-3xl px-6 py-12">
        <p className="tg-label">{BRAND} 组件预览（固定数据）</p>
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
    </CopyProvider>
  );
}
