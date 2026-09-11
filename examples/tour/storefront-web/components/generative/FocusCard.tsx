// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `present_focus`: the 聚焦卡. A search that matched more 线路 than a shortlist shows is put to
 * the advisor as one question over the catalog's own groups, and every group is a row of chips:
 * the dimension the question asks about first, then the others. A tap sends that chip's `ask`
 * into the conversation as the advisor's own words; a value the overview counted but cannot be
 * narrowed by is the same chip without the tap. Up to three results sit under the rows as a
 * foothold.
 */

import { useStoreFrame } from "web-shared";
import type { FocusPayload } from "@/lib/types";
import { MiniProductCard } from "./shared";

export default function FocusCard({ payload }: { payload: FocusPayload }) {
  const { ask, chat } = useStoreFrame();
  const groups = payload.groups ?? [];
  const primary = groups.find((group) => group.label === payload.dimension);
  const rows = primary ? [primary, ...groups.filter((group) => group !== primary)] : groups;
  const busy = chat?.busy ?? false;
  return (
    <section className="tg-card ac-reveal p-5">
      <div className="tg-label">共 {payload.total} 条线路符合 · 先缩小范围</div>
      <h3 className="mt-1 text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">
        {payload.question}
      </h3>

      <div className="mt-3 flex flex-col gap-3">
        {rows.map((group, row) => (
          <div key={group.label}>
            <div className="tg-label mb-1.5">按{group.label}</div>
            <div className="flex flex-wrap gap-2">
              {group.values.map((item, index) => (
                <button
                  key={item.value}
                  type="button"
                  className="chip"
                  disabled={busy || !item.ask}
                  onClick={() => item.ask && ask(item.ask)}
                  style={{ animationDelay: `${(row * 3 + index) * 50}ms` }}
                >
                  {item.value}
                  <span className="tg-num ml-1 opacity-70">{item.count}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {payload.anchors?.length ? (
        <div className="mt-4 border-t border-(--line) pt-3">
          <div className="tg-label mb-1.5">先看几条</div>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {payload.anchors.map((product) => (
              <li key={product.product_id} className="min-w-0">
                <MiniProductCard product={product} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
