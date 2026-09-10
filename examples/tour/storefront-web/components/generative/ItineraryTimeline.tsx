// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `itinerary`: one version of a 定制方案, as a day-by-day 行程 over the baseline 线路 it changes.
 * Nothing here is a price the customer can be quoted: the figures are the baseline 团期's own,
 * and the 定制 difference is the 计调's to make, which the card says under them. The advisor
 * leaves with two pieces of text — the customer's link, and the plan the 计调 works from.
 */

import { Fragment } from "react";
import {
  departureSpecs,
  formatYuan,
  formatYuanText,
  planDayNumber,
  planDaySpan,
  quoteSourceLabel,
  routeSpecs,
} from "@/lib/format";
import type { ItineraryDay, ItineraryPayload, Product } from "@/lib/types";
import { useCopy } from "./shared";

/** What a day's `changed_fields` name, in the words the timeline shows them in. */
const FIELD_NAMES: Record<string, string> = { label: "标题", note: "行程" };

const MARK_TONE = {
  added: "bg-(--accent-soft) text-(--accent-ink)",
  changed: "bg-(--info-soft) text-(--info)",
  removed: "bg-(--danger-soft) text-(--danger)",
};

/** 新增 / 修改·行程 / 删除 / 待计调确认 — one chip, sized like the pills the other cards use. */
function Mark({ text, tone }: { text: string; tone: string }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${tone}`}>{text}</span>
  );
}

/** 修改 with the fields it touched after it; a change with no fields named is just 修改. */
function changedText(fields?: ("label" | "note")[]): string {
  const named = (fields ?? []).map((field) => FIELD_NAMES[field]).filter(Boolean);
  return named.length ? `修改·${named.join("·")}` : "修改";
}

/** The 线路 this plan is a change to, on one line: which one it is, how long, and from where. */
function baselineText(route: Product): string {
  const specs = routeSpecs(route)
    .map((spec) => (spec.label === "出发城市" ? `${spec.value}出发` : spec.value))
    .join(" · ");
  return [route.product_id, route.title, specs].filter(Boolean).join(" · ");
}

/** The rail's own numeral: what the label says, or where the day sits when it says nothing. */
function dayNumerals(days: ItineraryDay[]): (number | null)[] {
  const parsed = days.map((day, i) => planDayNumber(day.label) ?? i + 1);
  // A second line written for the same day gets a diamond rather than the numeral again.
  return parsed.map((value, i) => (i > 0 && value === parsed[i - 1] ? null : value));
}

function Numeral({ value, first }: { value: number | null; first: boolean }) {
  return (
    <span
      aria-hidden
      className={`tg-num font-bold leading-none ${
        value === null
          ? "text-[15px] text-(--ink-faint)"
          : `text-[28px] ${first ? "text-(--accent)" : "text-(--ink)"}`
      }`}
    >
      {value ?? "◈"}
    </span>
  );
}

/** One day of the plan, with what this version did to it. */
function DayRow({
  day,
  numeral,
  first,
  last,
  delay,
}: {
  day: ItineraryDay;
  numeral: number | null;
  first: boolean;
  last: boolean;
  delay: number;
}) {
  const change = day.change;
  return (
    <li
      className="ac-reveal grid grid-cols-[44px_1fr] gap-x-3"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex flex-col items-center pt-0.5">
        <Numeral value={numeral} first={first} />
        {!last ? <span aria-hidden className="mt-1.5 w-px flex-1 bg-(--line)" /> : null}
      </div>
      <div className={last ? "pt-0.5" : "pb-5 pt-0.5"}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-[14.5px] font-semibold leading-snug text-(--ink)">{day.label}</span>
          {change === "added" ? <Mark text="新增" tone={MARK_TONE.added} /> : null}
          {change === "changed" ? (
            <Mark text={changedText(day.changed_fields)} tone={MARK_TONE.changed} />
          ) : null}
          {day.request ? (
            <Mark text="待计调确认" tone="bg-(--warn-soft) text-(--warn)" />
          ) : null}
        </div>
        {day.note ? (
          <p className="mt-1 text-[13.5px] italic leading-relaxed text-(--ink-soft)">{day.note}</p>
        ) : null}
      </div>
    </li>
  );
}

/** A day of the version before this one that this one dropped, struck through where it sat. */
function RemovedRow({ label, note, last }: { label: string; note: string; last: boolean }) {
  return (
    <li className="ac-reveal grid grid-cols-[44px_1fr] gap-x-3">
      <div className="flex flex-col items-center pt-0.5">
        <span aria-hidden className="tg-num text-[15px] font-bold leading-none text-(--ink-faint)">
          –
        </span>
        {!last ? <span aria-hidden className="mt-1.5 w-px flex-1 bg-(--line)" /> : null}
      </div>
      <div className={last ? "pt-0.5" : "pb-5 pt-0.5"}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-[14.5px] font-semibold leading-snug text-(--ink-faint) line-through">
            {label}
          </span>
          <Mark text="删除" tone={MARK_TONE.removed} />
        </div>
        {note ? (
          <p className="mt-1 text-[13.5px] italic leading-relaxed text-(--ink-faint) line-through">
            {note}
          </p>
        ) : null}
      </div>
    </li>
  );
}

/** The day still being written, sized so the finished days above it do not move. */
function SkeletonRow({ value, last }: { value: number | null; last: boolean }) {
  return (
    <li aria-hidden className="grid grid-cols-[44px_1fr] gap-x-3">
      <div className="flex flex-col items-center pt-0.5">
        {value != null ? (
          <span className="tg-num text-[28px] font-bold leading-none text-(--ink-faint)">
            {value}
          </span>
        ) : (
          <span className="ac-skeleton h-7 w-6 rounded" />
        )}
        {!last ? <span className="mt-1.5 w-px flex-1 bg-(--line)" /> : null}
      </div>
      <div className={last ? "pt-1" : "pb-5 pt-1"}>
        <div className="ac-skeleton h-4 w-2/5 rounded" />
        <div className="ac-skeleton mt-2 h-3 w-3/4 rounded" />
      </div>
    </li>
  );
}

/**
 * What the baseline 团期 costs, which is the only figure this card has: the 同业价 the order
 * would settle at, the 市场价 the customer reads, and which of the two the ERP quoted. A plan
 * opened off no 团期 has none of it, and either way the 定制 difference is still to be made.
 */
function ReferencePrice({ payload }: { payload: ItineraryPayload }) {
  const price = payload.reference_price;
  const parts: string[] = [];
  if (price) {
    if (price.tong_ye_adult != null) parts.push(`同业价 成人 ${formatYuan(price.tong_ye_adult)}`);
    if (price.market_adult != null) parts.push(`市场价 成人 ${formatYuan(price.market_adult)}`);
    const source = quoteSourceLabel(price.quote_source);
    if (source) parts.push(source);
    if (price.party_total != null) {
      parts.push(`${payload.party ?? "全团"}合计 ${formatYuanText(price.party_total)}`);
    }
  }
  return (
    <div className="mt-4 rounded-(--radius) bg-(--well) px-3 py-2.5">
      {parts.length ? (
        <p className="tg-num text-[13px] leading-relaxed text-(--ink-2)">
          <span className="tg-label mr-1.5">参考价（基线团期）</span>
          {parts.join(" · ")}
        </p>
      ) : (
        <p className="text-[13px] text-(--ink-2)">
          <span className="tg-label mr-1.5">参考价（基线团期）</span>参考价待查
        </p>
      )}
      <p className="tg-label mt-1">定制差价待计调报价</p>
    </div>
  );
}

/** The two pieces of text the advisor leaves with: the customer's link and the 计调's copy. */
function Handoff({ shareUrl, text }: { shareUrl?: string; text: string }) {
  const link = useCopy<HTMLInputElement>(shareUrl ?? "");
  const handoff = useCopy<HTMLTextAreaElement>(text);
  return (
    <div className="mt-4 flex flex-col gap-2 border-t border-(--line) pt-3">
      <div className="flex flex-wrap items-center gap-2">
        {shareUrl ? (
          <button type="button" onClick={link.copy} className="chip">
            {link.copied ? "已复制" : "发给客人"}
          </button>
        ) : null}
        {text ? (
          <button type="button" onClick={handoff.copy} className="chip">
            {handoff.copied ? "已复制" : "复制给计调"}
          </button>
        ) : null}
      </div>
      {shareUrl ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="tg-label shrink-0">客人链接</span>
          <input
            ref={link.fieldRef}
            readOnly
            value={shareUrl}
            aria-label="客人链接"
            onFocus={(event) => event.currentTarget.select()}
            className="tg-num min-w-0 flex-1 rounded-(--radius) border border-(--line) bg-(--well) px-2.5 py-1.5 text-[12.5px] text-(--ink-2)"
          />
        </div>
      ) : null}
      {text ? (
        <label className="flex flex-col gap-1">
          <span className="tg-label">给计调的行程</span>
          <textarea
            ref={handoff.fieldRef}
            readOnly
            rows={3}
            value={text}
            onFocus={(event) => event.currentTarget.select()}
            className="w-full resize-y rounded-(--radius) border border-(--line) bg-(--well) px-2.5 py-1.5 text-[12.5px] leading-relaxed text-(--ink-2)"
          />
        </label>
      ) : null}
    </div>
  );
}

export default function ItineraryTimeline({
  payload,
  partial,
}: {
  payload: ItineraryPayload;
  partial?: boolean;
}) {
  const days = payload.days ?? [];
  const removed = payload.removed_days ?? [];
  const numerals = dayNumerals(days);
  const version = payload.version;

  // The dates land in the first frames, so they size the rail before the last day is written.
  const expected = planDaySpan(payload.travel_dates);
  const written = numerals.reduce<number>((high, value) => Math.max(high, value ?? 0), 0);
  const pending =
    expected != null
      ? Array.from({ length: Math.max(0, expected - written) }, (_, i) => written + i + 1)
      : [];
  const skeletons = partial ? Math.max(1, pending.length) : 0;

  // A dropped day is drawn where it sat, which is before the day that now holds its index.
  const removedAt = (index: number) => removed.filter((day) => day.index === index);
  const trailing = removed.filter((day) => day.index >= days.length);

  return (
    <section className="tg-card ac-reveal p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1.5">
        {payload.title ? (
          <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">
            {payload.title}
          </h3>
        ) : (
          <span className="ac-skeleton h-5 w-2/5 rounded" aria-hidden />
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          {version != null ? (
            <span className="tg-num rounded-full bg-(--accent-soft) px-2 py-0.5 text-[11.5px] font-semibold text-(--accent-ink)">
              v{version}
              <span className="ml-1 font-medium">
                {payload.parent_version != null ? `· 基于 v${payload.parent_version}` : "· 基线原样"}
              </span>
            </span>
          ) : null}
          {payload.travel_dates ? (
            <span className="tg-num rounded-full bg-(--well) px-2 py-0.5 text-[11.5px] text-(--ink-2)">
              {payload.travel_dates}
            </span>
          ) : null}
          {payload.party ? (
            <span className="rounded-full bg-(--well) px-2 py-0.5 text-[11.5px] text-(--ink-2)">
              {payload.party}
            </span>
          ) : null}
          {payload.erp_route_id ? (
            <span className="tg-num rounded-full bg-(--ok-soft) px-2 py-0.5 text-[11.5px] font-semibold text-(--ok)">
              已在 ERP 建线 {payload.erp_route_id}
            </span>
          ) : null}
        </div>
      </div>

      {payload.route ? (
        <p className="tg-num mt-1.5 text-[12.5px] leading-snug text-(--ink-soft)">
          <span className="tg-label mr-1">基线线路</span>
          {baselineText(payload.route)}
        </p>
      ) : null}
      {payload.departure ? (
        <p className="tg-num mt-0.5 text-[12.5px] leading-snug text-(--ink-soft)">
          <span className="tg-label mr-1">基线团期</span>
          {departureSpecs(payload.departure)
            .map((spec) => spec.value)
            .join(" · ")}
        </p>
      ) : null}

      <ol className="mt-4">
        {days.map((day, i) => (
          <Fragment key={`${day.label}-${i}`}>
            {removedAt(i).map((gone) => (
              <RemovedRow key={`gone-${gone.index}-${gone.label}`} {...gone} last={false} />
            ))}
            <DayRow
              day={day}
              numeral={numerals[i]}
              first={i === 0}
              last={i === days.length - 1 && !skeletons && !trailing.length}
              delay={i * 60}
            />
          </Fragment>
        ))}
        {trailing.map((gone, i) => (
          <RemovedRow
            key={`gone-${gone.index}-${gone.label}`}
            {...gone}
            last={i === trailing.length - 1 && !skeletons}
          />
        ))}
        {Array.from({ length: skeletons }, (_, i) => (
          <SkeletonRow
            key={`skeleton-${pending[i] ?? "next"}-${i}`}
            value={pending[i] ?? null}
            last={i === skeletons - 1}
          />
        ))}
      </ol>

      {partial ? (
        <p className="tg-label mt-1" aria-live="polite">
          {expected == null
            ? `正在整理第 ${days.length + 1} 天…`
            : days.length < expected
              ? `正在整理第 ${days.length + 1} 天（共 ${expected} 天）…`
              : "正在收尾…"}
        </p>
      ) : null}

      {!partial && payload.summary && version !== 1 ? (
        <p className="mt-3 text-[13.5px] leading-relaxed text-(--ink-2)">
          <span className="tg-label mr-1.5">本次改动</span>
          {payload.summary}
        </p>
      ) : null}

      {!partial ? <ReferencePrice payload={payload} /> : null}
      {!partial && (payload.share_url || payload.handoff_text) ? (
        <Handoff shareUrl={payload.share_url} text={payload.handoff_text ?? ""} />
      ) : null}
    </section>
  );
}
