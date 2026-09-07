// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** `present_plan`: the steps between a shortlist and a 占位, each naming what it acts on. */

import type { PlanPayload } from "@/lib/types";
import { MiniProductCard } from "./shared";

export default function PlanChecklist({
  payload,
  partial,
}: {
  payload: PlanPayload;
  partial?: boolean;
}) {
  const steps = payload.steps ?? [];
  return (
    <section className="tg-card ac-reveal p-5">
      <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">{payload.title}</h3>
      {payload.intro ? (
        <p className="mt-1 text-[14px] leading-relaxed text-(--ink-2)">{payload.intro}</p>
      ) : null}

      <ol className="mt-4">
        {steps.map((step, i) => {
          const last = i === steps.length - 1 && !partial;
          const products = step.products ?? [];
          return (
            <li key={`${step.label}-${i}`} className="ac-reveal grid grid-cols-[30px_1fr] gap-x-3">
              <div className="flex flex-col items-center">
                <span className="tg-num grid h-[26px] w-[26px] shrink-0 place-items-center rounded-full bg-(--accent-soft) text-[13px] font-bold text-(--accent-ink)">
                  {i + 1}
                </span>
                {!last ? (
                  <span aria-hidden className="mt-1 w-px flex-1 bg-(--line)" />
                ) : null}
              </div>
              <div className={last ? "pt-0.5" : "pb-5 pt-0.5"}>
                <div className="text-[14.5px] font-semibold text-(--ink)">{step.label}</div>
                {step.detail ? (
                  <p className="mt-0.5 text-[13.5px] leading-relaxed text-(--ink-soft)">
                    {step.detail}
                  </p>
                ) : null}
                {products.length ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {products.map((product) => (
                      <MiniProductCard key={product.product_id} product={product} />
                    ))}
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
        {partial ? (
          <li className="grid grid-cols-[30px_1fr] gap-x-3" aria-hidden>
            <span className="ac-skeleton h-[26px] w-[26px] rounded-full" />
            <div className="flex flex-col gap-2 pt-1">
              <div className="ac-skeleton h-4 w-2/5 rounded" />
              <div className="ac-skeleton h-3 w-4/5 rounded" />
            </div>
          </li>
        ) : null}
      </ol>
    </section>
  );
}
