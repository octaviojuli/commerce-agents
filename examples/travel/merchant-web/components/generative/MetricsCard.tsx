// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { ChangeChip, formatMoney, formatNumber, formatPeriodLabel, formatRate, GenCard, GenCardHeader, Sparkline, titleCase } from "web-shared";
import type { MetricEntry, MetricsPayload } from "@/lib/types";

const CURRENCY_METRICS = new Set(["sales", "revenue", "average_order_value", "average_booking_value", "average_nightly_rate", "spend"]);
const RATE_METRICS = new Set(["conversion_rate", "occupancy", "occupancy_rate", "cancellation_rate", "return_rate", "click_through_rate"]);

/** The metrics the supplier's tools report, in their words; an unlisted one is humanized. */
const METRIC_LABELS: Record<string, string> = {
  sales: "销售额",
  revenue: "营收",
  orders: "预订数",
  bookings: "预订数",
  average_order_value: "平均预订额",
  average_booking_value: "平均预订额",
  average_nightly_rate: "平均房价",
  spend: "花费",
  conversion_rate: "转化率",
  occupancy: "入住率",
  occupancy_rate: "入住率",
  cancellation_rate: "取消率",
  return_rate: "退款率",
  click_through_rate: "点击率",
  sessions: "访问量",
  visits: "访问量",
  units: "房晚数",
};

function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? titleCase(metric);
}

function metricValue(entry: MetricEntry): string | null {
  if (entry.value == null) return null;
  if (CURRENCY_METRICS.has(entry.metric)) return formatMoney(entry.value, entry.currency ?? "USD", { whole: entry.value >= 1000 });
  if (RATE_METRICS.has(entry.metric)) return formatRate(entry.value);
  return formatNumber(entry.value);
}

export default function MetricsCard({ payload }: { payload: MetricsPayload }) {
  const metrics = payload.metrics ?? [];
  return (
    <GenCard>
      <GenCardHeader title={payload.title ?? "业绩表现"} aside={payload.period ? formatPeriodLabel(payload.period) : null} />
      <div className="mt-2 grid grid-cols-2 border-t border-(--line) [&>*:nth-child(even)]:border-l [&>*:nth-child(n+3)]:border-t [&>*]:border-(--line)">
        {metrics.map((entry, index) => {
          const value = metricValue(entry);
          const points = entry.series?.points?.map((point) => point.value);
          return (
            <div key={`${entry.metric}-${index}`} className="px-3.5 py-3">
              <div className="text-[12px] font-medium text-(--ink-soft)">{metricLabel(entry.metric)}</div>
              <div className="mt-1 flex items-baseline gap-2">
                {value != null ? <span className="text-[20px] font-semibold leading-none tracking-[-0.02em] tabular-nums text-(--ink)">{value}</span> : null}
                <ChangeChip changePct={entry.change_pct} />
              </div>
              {points && points.length > 1 ? <Sparkline points={points} height={34} label={`${metricLabel(entry.metric)}趋势`} className="mt-2" /> : null}
              {entry.note ? <div className="mt-1.5 text-[11.5px] leading-snug text-(--ink-soft)">{entry.note}</div> : null}
            </div>
          );
        })}
      </div>
    </GenCard>
  );
}
