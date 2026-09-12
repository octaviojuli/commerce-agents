// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `present_focus`: the 聚焦卡. A search that matched more 线路 than a shortlist shows is put to
 * the advisor as one question over the catalog's own groups, and every group is a row of chips:
 * the dimension the question asks about first, then the others. A chip is a pick, not a send —
 * the advisor may hold several in one group and across groups, and the bar under the rows sends
 * them as one message, because a customer who wants 德法意瑞 or 法意瑞 in 10 月 or 11 月 is one
 * request and not four turns. A value the overview counted but cannot be narrowed by carries no
 * `ask` and is not selectable. Up to three results sit under the rows as a foothold.
 */

import { useState } from "react";
import { useStoreFrame } from "web-shared";
import type { FocusPayload } from "@/lib/types";
import { MiniProductCard } from "./shared";

type Group = FocusPayload["groups"][number];

/** The picks of one group, in the order the card lists them rather than the order they were tapped. */
interface Picked {
  label: string;
  values: string[];
}

/** 不限条件，直接看这些线路 — the advisor drops the question and asks for the lines as they are. */
const ANY = "不限条件，直接看这些线路";

/**
 * What the card sends: `只看 目的地：德法意瑞、法意瑞；天数：12 天`. The group's own label and the
 * chip's own value, verbatim — the words the overview grouped by are the words the model filters
 * on, so the card invents no vocabulary of its own.
 */
function askText(picked: Picked[]): string {
  return `只看 ${picked.map((group) => `${group.label}：${group.values.join("、")}`).join("；")}`;
}

/** The same picks as the bar reads them back: `目的地 德法意瑞、法意瑞 · 天数 12 天`. */
function summaryText(picked: Picked[]): string {
  return picked.map((group) => `${group.label} ${group.values.join("、")}`).join(" · ");
}

export default function FocusCard({ payload }: { payload: FocusPayload }) {
  const { ask, chat } = useStoreFrame();
  const groups = payload.groups ?? [];
  const primary = groups.find((group) => group.label === payload.dimension);
  const rows: Group[] = primary
    ? [primary, ...groups.filter((group) => group !== primary)]
    : groups;
  const busy = chat?.busy ?? false;

  // The picks live on the card until the advisor confirms them; sending clears them, so the
  // card that stays in the transcript is not still holding a question already answered.
  const [selection, setSelection] = useState<Record<string, string[]>>({});
  const isOn = (label: string, value: string) => (selection[label] ?? []).includes(value);
  const toggle = (label: string, value: string) =>
    setSelection((current) => {
      const values = current[label] ?? [];
      const next = values.includes(value)
        ? values.filter((held) => held !== value)
        : [...values, value];
      const updated = { ...current, [label]: next };
      if (!next.length) delete updated[label];
      return updated;
    });

  const picked: Picked[] = rows
    .map((group) => ({
      label: group.label,
      values: group.values
        .filter((item) => isOn(group.label, item.value))
        .map((item) => item.value),
    }))
    .filter((group) => group.values.length > 0);

  const send = (message: string) => {
    setSelection({});
    ask(message);
  };

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
              {group.values.map((item, index) => {
                const on = isOn(group.label, item.value);
                return (
                  <button
                    key={item.value}
                    type="button"
                    aria-pressed={on}
                    className={`chip ${on ? "chip-on" : ""}`}
                    disabled={busy || !item.ask}
                    onClick={() => item.ask && toggle(group.label, item.value)}
                    style={{ animationDelay: `${(row * 3 + index) * 50}ms` }}
                  >
                    {item.value}
                    <span className="tg-num ml-1 opacity-70">{item.count}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* One send for the whole card: what is held, and the two ways out of the question. */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-(--line) pt-3">
        <p className="min-w-0 flex-1 text-[13px] leading-snug text-(--ink-2)" aria-live="polite">
          {picked.length ? (
            <>
              <span className="tg-label mr-0.5">已选：</span>
              {summaryText(picked)}
            </>
          ) : (
            <span className="text-(--ink-faint)">还没选，点上面的标签</span>
          )}
        </p>
        <div className="flex shrink-0 items-center gap-3">
          <button
            type="button"
            className="btn-primary text-[13.5px]"
            disabled={busy || !picked.length}
            onClick={() => send(askText(picked))}
          >
            按所选条件筛选
          </button>
          <button
            type="button"
            className="text-[13px] text-(--ink-soft) underline decoration-dotted underline-offset-2 hover:text-(--ink) disabled:opacity-50"
            disabled={busy}
            onClick={() => send(ANY)}
          >
            不限，直接看线路
          </button>
        </div>
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
