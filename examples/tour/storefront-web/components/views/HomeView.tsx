// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { Greeting, HomeSection, type Starter, Starters, useStoreFrame } from "web-shared";

/** The advisor types what the customer just said; each starter is one of those sentences. */
const STARTERS: Starter[] = [
  { icon: "search", prompt: "10月中旬四位客人去新疆伊犁，8–10天，两大两小，孩子5岁和9岁，不要购物店。" },
  { icon: "calendar", prompt: "那条 10 天的喀拉峻深度线路，10 月 15 号前后有什么团？" },
  { icon: "clock", prompt: "就 10/14 那个团，帮我把 4 个位置锁上。" },
  { icon: "message", prompt: "客人问不成团怎么办，退改政策是怎么写的？" },
];

/** The four directions this 门店 sells, as the catalog's own destinations. */
const DESTINATIONS = ["伊犁", "喀纳斯", "南疆", "青海"];

function DestinationRow() {
  const { ask, chat } = useStoreFrame();
  const disabled = !chat || chat.busy || !chat.ready;
  return (
    <div className="flex flex-wrap gap-2">
      {DESTINATIONS.map((destination) => (
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
      <HomeSection title="常走方向" subtitle="点一个方向直接开线路">
        <DestinationRow />
      </HomeSection>
    </div>
  );
}
