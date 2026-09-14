// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import {
  Greeting,
  HomeSection,
  type Starter,
  Starters,
  useCatalogIndex,
  useStoreFrame,
} from "web-shared";
import { fetchProducts } from "@/lib/api";
import { attrList } from "@/lib/format";
import type { Product } from "@/lib/types";

/**
 * The four openings an advisor's first sentence takes, filled from the catalog so that every
 * one of them is a search that answers: a whole request, a direction on its own, a month on
 * its own, and a condition on its own. Nothing here presupposes a 线路 or a 团期 — a new
 * conversation has neither, and a starter that names one is a tap the advisor wastes.
 */
function starters(products: Product[]): Starter[] {
  const directions = catalogDirections(products);
  const lead = directions[0] ?? "欧洲";
  const second = directions[1] ?? lead;
  const month = nextMonth();
  const days = typicalDays(products, lead);
  const budget = budgetBand(products, second);
  return [
    {
      icon: "search",
      prompt: `有 2 位客人，${month}月想去${lead}，玩 ${days} 天左右，购物团也可以`,
    },
    { icon: "pin", prompt: `${second}线路有哪些？` },
    { icon: "calendar", prompt: `${month}月还能报名的${lead}团期有哪些？` },
    { icon: "tag", prompt: `${budget}去${second}的线路` },
  ];
}

/** The month a first search is most likely about: the next one, in the advisor's own clock. */
function nextMonth(): number {
  const now = new Date();
  return ((now.getMonth() + 1) % 12) + 1;
}

/** The 线路系 the catalog files its lines under, commonest first; the directions row and the
 * starters both read them, so what is offered is what the agency sells. */
function catalogDirections(products: Product[]): string[] {
  const counted = new Map<string, number>();
  for (const product of products) {
    const direction = product.attributes?.region || attrList(product.attributes?.destination)[0];
    if (direction) counted.set(direction, (counted.get(direction) ?? 0) + 1);
  }
  return [...counted.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, MAX_DESTINATIONS)
    .map(([direction]) => direction);
}

/** The length that direction is usually sold at, rounded to the nearest day. */
function typicalDays(products: Product[], direction: string): number {
  const lengths = products
    .filter((product) => (product.attributes?.region ?? "") === direction)
    .map((product) => Number(product.attributes?.days))
    .filter((days) => days > 0)
    .sort((a, b) => a - b);
  return lengths.length ? lengths[Math.floor(lengths.length / 2)] : 10;
}

/** A budget the catalog can meet on that direction, as an advisor says it. */
function budgetBand(products: Product[], direction: string): string {
  const prices = products
    .filter((product) => (product.attributes?.region ?? "") === direction && product.price > 0)
    .map((product) => product.price)
    .sort((a, b) => a - b);
  if (!prices.length) return "1.5 万左右";
  const middle = prices[Math.floor(prices.length / 2)];
  if (middle < 10000) {
    const floor = Math.floor(middle / 1000) * 1000;
    return `${floor}-${floor + 2000} 元`;
  }
  return `${(Math.round(middle / 1000) * 1000) / 10000} 万左右`;
}

/** As many directions as the row holds without wrapping past two lines. */
const MAX_DESTINATIONS = 8;

function DestinationRow({ destinations }: { destinations: string[] }) {
  const { ask, chat } = useStoreFrame();
  const disabled = !chat || chat.busy || !chat.ready;
  return (
    <div className="flex flex-wrap gap-2">
      {destinations.map((destination) => (
        <button
          key={destination}
          type="button"
          disabled={disabled}
          onClick={() => ask(`${destination}线路有哪些？`)}
          className="chip"
        >
          {destination}
        </button>
      ))}
    </div>
  );
}

/** The advisor's own name greets them; the 门店 they book through is in the app bar. */
export default function HomeView({ advisorName }: { advisorName: string }) {
  const catalog = useCatalogIndex(fetchProducts);
  const products = Object.values(catalog);
  const destinations = catalogDirections(products);
  return (
    <div className="flex flex-col gap-5">
      <Greeting
        title={
          <h1 className="text-[30px] font-semibold leading-tight tracking-[-0.02em] text-(--ink) sm:text-[38px]">
            {advisorName}，这单客人想去哪儿？
          </h1>
        }
      >
        把客人的原话打进来：选团助手查线路、开团期、按人数结构报价，确认后再占位。占位 30 分钟到期自动释放，全程不收款。
      </Greeting>
      <Starters items={starters(products)} />
      {destinations.length ? (
        <HomeSection title="目录里的方向" subtitle="点一个方向直接开线路">
          <DestinationRow destinations={destinations} />
        </HomeSection>
      ) : null}
    </div>
  );
}
