// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `present_focus`: the 聚焦卡. A search that matched more 线路 than a shortlist shows is put to
 * the advisor as one question over the catalog's own groups: the chips are the values of the
 * dimension the question asks about, each tap sends its `ask` into the conversation as the
 * advisor's own words, and the other groups stand beside as counts. Up to three results may
 * sit under the question as a foothold.
 */

import { useStoreFrame } from "web-shared";
import type { FocusPayload, Product } from "@/lib/types";
import { MiniProductCard } from "./shared";

function Anchor({ product }: { product: Product }) {
  return (
    <li className="min-w-0">
      <MiniProductCard product={product} />
    </li>
  );
}

export default function FocusCard({ payload }: { payload: FocusPayload }) {
  const { ask, chat } = useStoreFrame();
  const groups = payload.groups ?? [];
  const primary = groups.find((group) => group.label === payload.dimension) ?? groups[0];
  const others = groups.filter((group) => group !== primary);
  const busy = chat?.busy ?? false;
  return (
    <section className="tg-card ac-reveal p-5">
      <div className="tg-label">
        共 {payload.total} 条线路符合 · 先缩小范围
      </div>
      <h3 className="mt-1 text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">
        {payload.question}
      </h3>

      {primary ? (
        <div className="mt-3">
          <div className="tg-label mb-1.5">按{primary.label}</div>
          <div className="flex flex-wrap gap-2">
            {primary.values.map((item, index) => (
              <button
                key={item.value}
                type="button"
                className="chip"
                disabled={busy || !item.ask}
                onClick={() => item.ask && ask(item.ask)}
                style={{ animationDelay: `${index * 60}ms` }}
              >
                {item.value}
                <span className="tg-num ml-1 opacity-70">{item.count}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {others.length ? (
        <dl className="mt-3 flex flex-col gap-1 border-t border-dashed border-(--line) pt-3 text-[12.5px] leading-snug text-(--ink-soft)">
          {others.map((group) => (
            <div key={group.label} className="flex flex-wrap gap-x-2">
              <dt className="shrink-0 font-semibold text-(--ink-2)">按{group.label}</dt>
              <dd className="min-w-0">
                {group.values.map((item, index) => (
                  <span key={item.value}>
                    {index ? "、" : ""}
                    {item.ask ? (
                      <button
                        type="button"
                        className="underline decoration-dotted underline-offset-2 hover:text-(--ink)"
                        disabled={busy}
                        onClick={() => item.ask && ask(item.ask)}
                      >
                        {item.value} {item.count}
                      </button>
                    ) : (
                      <span>
                        {item.value} {item.count}
                      </span>
                    )}
                  </span>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {payload.anchors?.length ? (
        <div className="mt-3 border-t border-(--line) pt-3">
          <div className="tg-label mb-1.5">先看几条</div>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {payload.anchors.map((product) => (
              <Anchor key={product.product_id} product={product} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
