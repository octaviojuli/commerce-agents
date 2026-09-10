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
 * The advisor types what the customer just said, and a starter is the shape of one of those
 * sentences with the customer's own particulars left blank: no destination, no 线路, no 团期 id,
 * and nothing that writes an order, because a starter is pressed before anyone has been quoted.
 */
const STARTERS: Starter[] = [
  { icon: "search", prompt: "【几月几号】前后，【几位】客人想去【目的地】，玩【几天】，有什么线路？" },
  { icon: "calendar", prompt: "就第一条线路，那一周有哪些团期？" },
  { icon: "message", prompt: "把这两个团期做成清单发给客人。" },
  { icon: "bed", prompt: "客人要五钻、纯玩不进店的线路。" },
];

/** As many directions as the row holds without wrapping past two lines. */
const MAX_DESTINATIONS = 8;

/**
 * The directions the catalog itself names: `destination` on every 线路 the API lists, which is
 * what `api/tags.py` normalised the ERP's itinerary tags into, in catalog order and deduplicated.
 * A deployment whose ERP listed nothing gets no row at all rather than a guess.
 */
function catalogDestinations(products: Product[]): string[] {
  const directions: string[] = [];
  for (const product of products) {
    for (const destination of attrList(product.attributes?.destination)) {
      if (!directions.includes(destination)) directions.push(destination);
      if (directions.length >= MAX_DESTINATIONS) return directions;
    }
  }
  return directions;
}

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
          onClick={() => ask(`${destination}方向这个月有哪些纯玩线路？`)}
          className="chip"
        >
          {destination}
        </button>
      ))}
    </div>
  );
}

export default function HomeView({ advisorName, store }: { advisorName: string; store?: string }) {
  const catalog = useCatalogIndex(fetchProducts);
  const destinations = catalogDestinations(Object.values(catalog));
  return (
    <div className="flex flex-col gap-5">
      <Greeting
        eyebrow={store}
        title={
          <h1 className="text-[30px] font-semibold leading-tight tracking-[-0.02em] text-(--ink) sm:text-[38px]">
            {advisorName}，这单客人想去哪儿？
          </h1>
        }
      >
        把客人的原话打进来：选团助手查线路、开团期、按人数结构报价，确认后再占位。占位 30 分钟到期自动释放，全程不收款。
      </Greeting>
      <Starters items={STARTERS} />
      {destinations.length ? (
        <HomeSection title="目录里的方向" subtitle="点一个方向直接开线路">
          <DestinationRow destinations={destinations} />
        </HomeSection>
      ) : null}
    </div>
  );
}
