// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `present_guide`: what the agency's 退改政策, 儿童价规则, 成团规则 and 定金规则 say, as
 * `search_policies` answered them. Sources are the policy ids the answer stands on, so the
 * advisor can quote a rule rather than paraphrase it.
 */

import type { GuidePayload } from "@/lib/types";
import { MiniProductCard } from "./shared";

export default function PolicyCard({ payload }: { payload: GuidePayload }) {
  return (
    <section className="tg-card ac-reveal p-5">
      <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">{payload.title}</h3>
      <div className="mt-3 flex flex-col gap-3.5">
        {(payload.sections ?? []).map((section) => (
          <div key={section.heading}>
            <h4 className="text-[14px] font-semibold text-(--ink)">{section.heading}</h4>
            <p className="mt-1 text-[13.5px] leading-relaxed text-(--ink-2)">{section.body}</p>
          </div>
        ))}
      </div>
      {payload.related_products?.length ? (
        <div className="mt-4 flex flex-wrap gap-2 border-t border-(--line) pt-3.5">
          {payload.related_products.map((product) => (
            <MiniProductCard key={product.product_id} product={product} />
          ))}
        </div>
      ) : null}
      {payload.sources?.length ? (
        <p className="tg-label mt-3">依据：{payload.sources.join(" · ")}</p>
      ) : null}
    </section>
  );
}
