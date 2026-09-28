"use client";

import { useEffect, useState } from "react";
import {
  amount,
  cardStyle,
  stamp,
  type QuoteLine,
  type Party,
} from "@/lib/warehouse";

export type CustomerQuoteData = {
  offer_name?: string;
  service_description?: string;
  product_name: string;
  product_name_origin?: string;
  departure_date: string;
  party: Omit<Party, "child_ages">;
  currency: string | null;
  market_total: string | null;
  known_market_subtotal: string | null;
  market_lines: QuoteLine[];
  included_items: string[] | null;
  complete: boolean;
  snapshot_stale: boolean;
  observed_at: string;
  fresh_until: string;
  local_booking_deadline: string | null;
  share_expires_at?: string;
};
const names: Record<string, string> = {
  adult: "成人",
  child: "儿童",
  senior: "老人",
  single_room: "单房差",
};

export default function CustomerQuote({ quote }: { quote: CustomerQuoteData }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const stale =
    quote.snapshot_stale || now >= new Date(quote.fresh_until).getTime();
  if (
    quote.share_expires_at &&
    now >= new Date(quote.share_expires_at).getTime()
  )
    return <p role="status">分享已到期，请联系顾问。</p>;
  return (
    <article
      className={`${cardStyle} space-y-4`}
      style={{ overflowWrap: "anywhere", fontSize: 15 }}
      aria-label="客户市场报价"
    >
      <p className="text-[15px] font-semibold text-(--accent)">
        旅行报价 · 供行程沟通参考
      </p>
      <h1 className="text-xl font-semibold">{quote.product_name}</h1>
      {quote.product_name_origin === "warehouse_display" && (
        <p className="text-[15px] text-(--ink-soft)">线路名称为云仓展示补充</p>
      )}
      {quote.offer_name && (
        <p className="font-semibold">方案：{quote.offer_name}</p>
      )}
      {quote.service_description && (
        <p className="whitespace-pre-wrap text-[15px]">
          {quote.service_description}
        </p>
      )}
      <p>
        {quote.departure_date} 出发 · {quote.party.adults} 成人 /{" "}
        {quote.party.children} 儿童 / {quote.party.seniors} 老人
      </p>
      <p className="text-[15px] text-(--ink-soft)">
        {quote.party.room_type || "房型待确认"} · {quote.party.single_rooms}{" "}
        间单房
      </p>
      {stale && (
        <p
          role="status"
          className="rounded-xl bg-(--warn-soft) p-3 text-(--warn)"
        >
          这是历史报价，价格已需重新核实，请联系顾问重新询价。
        </p>
      )}
      <div className="rounded-xl bg-(--well) p-4">
        <p className="text-[15px]">
          {quote.complete ? "市场报价" : "费用尚未完整"}
        </p>
        <p className="mt-1 text-2xl font-semibold">
          {amount(quote.market_total, quote.currency)}
        </p>
        {quote.market_total === null &&
          quote.known_market_subtotal !== null && (
            <p className="mt-2 text-[15px]">
              已知部分：{amount(quote.known_market_subtotal, quote.currency)}
              ；不是全包总价。
            </p>
          )}
      </div>
      {!quote.complete && (
        <p className="text-[15px] text-(--warn)">
          部分费用或适用条件尚待核实，具体内容请与顾问确认。
        </p>
      )}
      {quote.market_lines.map((line, i) => (
        <div
          key={i}
          className="flex justify-between gap-4 border-b border-(--line) py-2 text-[15px]"
        >
          <span>
            {line.label || names[line.code] || "费用项目"} × {line.quantity}
          </span>
          <span>{amount(line.total, quote.currency)}</span>
        </div>
      ))}
      {!!quote.included_items?.length && (
        <p className="text-[15px]">
          已确认包含：{quote.included_items.join("、")}
        </p>
      )}
      <div className="space-y-1 text-[15px] text-(--ink-soft)">
        <p>报价查询时间：{stamp(quote.observed_at)}（北京时间）</p>
        <p>最迟重新核价时间：{stamp(quote.fresh_until)}（北京时间）</p>
        {quote.local_booking_deadline && (
          <p>云仓报名截止：{stamp(quote.local_booking_deadline)}（北京时间）</p>
        )}
        <p>
          本报价未占位、未下单、不收款。余位与供应商报名条件须由顾问重新确认。
        </p>
        {quote.share_expires_at && (
          <p>
            链接可查看至：{stamp(quote.share_expires_at)}
            （北京时间）；链接有效不代表报价仍有效。
          </p>
        )}
      </div>
    </article>
  );
}
