// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * The customer's own page for one version of a 定制方案, opened from the link their advisor sent
 * them on a phone. It is not the workbench: no login, no session, no 占位, and no 同业价 — the
 * page reads `GET /api/share/plan/{token}`, which hands out the 市场价 alone. The token stands
 * for the plan, so a link that no longer names one is a dead link and says so.
 */

import type { Metadata } from "next";
import { API_URL } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { formatYuan, fullDateLabel } from "@/lib/format";
import type { SharedPlan } from "@/lib/types";
import PlanReply from "./PlanReply";

export const metadata: Metadata = {
  title: `${BRAND} · 定制方案`,
  description: "顾问为你排的行程。",
};

// The plan changes as the advisor works on it, so the page is read at request time.
export const dynamic = "force-dynamic";

type Result = { plan: SharedPlan } | { error: "gone" | "unreachable" };

async function readPlan(token: string): Promise<Result> {
  try {
    const response = await fetch(`${API_URL}/api/share/plan/${encodeURIComponent(token)}`, {
      cache: "no-store",
    });
    if (!response.ok) return { error: "gone" };
    return { plan: (await response.json()) as SharedPlan };
  } catch {
    // The API is not answering; the link itself may still be good.
    return { error: "unreachable" };
  }
}

function Notice({ text }: { text: string }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-[560px] flex-col items-center justify-center px-6">
      <p className="text-[15px] text-(--ink-soft)">{text}</p>
    </main>
  );
}

/** 10 天 · 乌鲁木齐出发 — the baseline line, from the words the API sends rather than a record. */
function routeLine(route: SharedPlan["route"]): string {
  const days = route.days ? `${route.days} 天` : "";
  const from = route.depart_city ? `${route.depart_city}出发` : "";
  return [route.title, days, from].filter(Boolean).join(" · ");
}

export default async function SharedPlanPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const result = await readPlan(token);
  if ("error" in result) {
    return (
      <Notice
        text={result.error === "gone" ? "链接不存在或已失效" : "暂时打不开，请稍后再试一次。"}
      />
    );
  }
  const plan = result.plan;
  const days = plan.days ?? [];

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[560px] flex-col px-5 py-8">
      <p className="text-[15px] font-semibold tracking-[-0.01em] text-(--ink)">
        <span aria-hidden className="mr-1.5 text-[12px] text-(--accent)">
          ◆
        </span>
        {BRAND}
      </p>
      <p className="tg-num tg-label mt-1">
        方案 v{plan.version} · 顾问 {plan.advisor_name} · {fullDateLabel(plan.created_at)}
      </p>

      <h1 className="mt-3 text-[22px] font-semibold leading-snug tracking-[-0.01em] text-(--ink)">
        {plan.title}
      </h1>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {plan.travel_dates ? (
          <span className="tg-num rounded-full bg-(--well) px-2.5 py-1 text-[12.5px] text-(--ink-2)">
            {plan.travel_dates}
          </span>
        ) : null}
        {plan.party ? (
          <span className="rounded-full bg-(--well) px-2.5 py-1 text-[12.5px] text-(--ink-2)">
            {plan.party}
          </span>
        ) : null}
      </div>
      <p className="mt-2 text-[13px] leading-snug text-(--ink-soft)">{routeLine(plan.route)}</p>

      <ol className="mt-6">
        {days.map((day, i) => (
          <li key={`${day.label}-${i}`} className="grid grid-cols-[36px_1fr] gap-x-3">
            <div className="flex flex-col items-center pt-0.5">
              <span
                aria-hidden
                className={`tg-num text-[22px] font-bold leading-none ${
                  i === 0 ? "text-(--accent)" : "text-(--ink)"
                }`}
              >
                {i + 1}
              </span>
              {i < days.length - 1 ? (
                <span aria-hidden className="mt-1.5 w-px flex-1 bg-(--line)" />
              ) : null}
            </div>
            <div className={i === days.length - 1 ? "pt-0.5" : "pb-5 pt-0.5"}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[15px] font-semibold leading-snug text-(--ink)">
                  {day.label}
                </span>
                {day.request ? (
                  <span className="rounded-full bg-(--warn-soft) px-2 py-0.5 text-[11.5px] font-semibold text-(--warn)">
                    待确认
                  </span>
                ) : null}
              </div>
              {day.note ? (
                <p className="mt-1 text-[14px] leading-relaxed text-(--ink-2)">{day.note}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-6 rounded-(--radius) bg-(--well) px-4 py-3">
        {plan.market_adult != null ? (
          <p className="tg-num text-[15px] font-bold text-(--ink)">
            <span className="tg-label mr-1.5">市场价</span>
            成人 {formatYuan(plan.market_adult)}
          </p>
        ) : (
          <p className="text-[14px] text-(--ink-2)">价格由顾问确认后告诉你。</p>
        )}
        <p className="tg-label mt-1">这是参考价，最终价格以顾问的报价为准。</p>
      </div>

      <div className="mt-6">
        <PlanReply token={token} />
      </div>
      <p className="tg-label mt-4 text-center">确认后顾问会联系你，本页面不收款。</p>
    </main>
  );
}
