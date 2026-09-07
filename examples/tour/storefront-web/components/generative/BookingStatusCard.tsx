// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** `present_order_status`: one 报名单 and where it stands. */

import { dateLabel, formatYuan } from "@/lib/format";
import type { OrderStatusPayload } from "@/lib/types";

// The shared order pipeline's statuses in 旅行社 words.
const STATUS_LABELS: Record<string, string> = {
  processing: "待确认",
  shipped: "已确认",
  out_for_delivery: "已确认",
  delivered: "已完成",
  return_initiated: "已申请退款",
  cancelled: "已取消",
  delayed: "有变动",
};

const ALERT_STATUSES = new Set(["delayed", "cancelled", "return_initiated"]);

export default function BookingStatusCard({ payload }: { payload: OrderStatusPayload }) {
  const order = payload.order;
  const status = order?.status ?? "processing";
  const alert = ALERT_STATUSES.has(status);
  return (
    <section className="tg-card ac-reveal p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="tg-label">报名单</div>
          <h3 className="tg-num mt-0.5 text-[17px] font-semibold text-(--ink)">{payload.order_id}</h3>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-1 text-[12px] font-semibold ${
            alert ? "bg-(--danger-soft) text-(--danger)" : "bg-(--ok-soft) text-(--ok)"
          }`}
        >
          {STATUS_LABELS[status] ?? status}
        </span>
      </div>

      <p className="mt-3 text-[14.5px] leading-relaxed text-(--ink)">{payload.summary}</p>

      {order ? (
        <div className="mt-3 rounded-(--radius) bg-(--well) p-3.5">
          {order.items.map((item) => (
            <div
              key={item.product_id}
              className="flex items-baseline justify-between gap-3 py-0.5 text-[14px] text-(--ink)"
            >
              <span className="truncate">
                {item.title}
                {item.quantity > 1 ? ` × ${item.quantity} 人` : ""}
              </span>
              <span className="tg-num shrink-0 text-(--ink-soft)">
                {formatYuan(item.price * item.quantity)}
              </span>
            </div>
          ))}
          <div className="mt-1.5 flex items-baseline justify-between border-t border-(--line) pt-1.5">
            <span className="tg-label">合计</span>
            <span className="tg-num text-[16px] font-bold text-(--ink)">
              {formatYuan(order.total)}
            </span>
          </div>
          {/* The ERP's own status word and departure date, or a bare date from another backend. */}
          {order.estimated_delivery ? (
            <div className="tg-label mt-1.5">
              {dateLabel(order.estimated_delivery)
                ? `出发 · ${dateLabel(order.estimated_delivery)}`
                : order.estimated_delivery}
            </div>
          ) : null}
        </div>
      ) : null}

      {payload.next_step ? (
        <p className="mt-3 text-[14px] font-semibold text-(--ink)">
          <span aria-hidden className="text-(--accent)">
            →{" "}
          </span>
          {payload.next_step}
        </p>
      ) : null}
    </section>
  );
}
