"use client";

import { useEffect, useState } from "react";
import {
  amount,
  bedText,
  cardStyle,
  displayRoute,
  stamp,
  type Quote,
} from "@/lib/warehouse";

const names: Record<string, string> = {
  adult: "成人",
  child: "儿童",
  senior: "老人",
  single_room: "单房差",
};
const missing: Record<string, string> = {
  fees_not_fully_confirmed: "全部费用尚未确认",
  child_seat_policy_unknown: "儿童占座规则待确认",
  child_age_policy_unknown: "儿童年龄规则待确认",
  child_ages_required: "需要补充儿童年龄",
  child_age_outside_price_rule: "儿童年龄不符合该价格规则",
  room_type_not_confirmed: "所选房型尚未确认",
  BUYER_CONTRACT_PRICE_MISSING: "当前工作空间没有适用且有效的价表",
  UNIFORM_PRICE_MISSING: "供应商尚未提供有效的统一结算价表",
  UNIFORM_PRICE_CONFIGURATION_MISSING: "供应商统一结算价接口尚未配置完成",
  CUSTOMER_BINDING_MISSING_OR_NOT_ACCEPTED: "供应商客户映射尚未完成双方确认",
  SOURCE_DEPARTMENT_MISSING: "来源部门信息待补齐",
  SOURCE_PRICE_CONNECTOR_NOT_CONFIGURED: "供应商报价接口尚未配置",
  SOURCE_PRICE_UNAVAILABLE: "供应商报价暂时不可用",
  SOURCE_PRICE_TIMEOUT: "供应商询价超时，请稍后重新询价",
  SOURCE_RATE_LIMITED: "供应商暂时限制访问，请稍后重新询价",
  SOURCE_COOLDOWN_REQUIRES_REVIEW: "供应商要求暂停访问，请联系管理员核实",
  SOURCE_COOLDOWN_UNAVAILABLE: "供应商访问状态暂时无法核实，请稍后重试",
  SOURCE_ACCESS_REVOKED: "供应商访问权限已变更，请重新选择可用线路",
  INVALID_SOURCE_PRICE: "供应商价格格式待核实",
};
function missingLabel(code: string) {
  if (missing[code]) return missing[code];
  const [side, item] = code.split(".");
  if (["market", "settlement"].includes(side))
    return `${side === "market" ? "市场价" : "同行结算价"}：${names[item] ?? "附加费用"}待确认`;
  return `供应商信息待核实（${code}）`;
}

export function QuoteCard({
  quote,
  historical = false,
}: {
  quote: Quote;
  historical?: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const stale =
    historical ||
    (quote.quote_expired ?? quote.snapshot_stale) ||
    now >= new Date(quote.quote_valid_until || quote.fresh_until).getTime();
  return (
    <article className={`${cardStyle} space-y-4`} aria-label="云仓报价结果">
      <div>
        <p className="text-xs font-semibold text-(--accent)">
          报价快照 · 顾问内部使用
        </p>
        {quote.product_name_origin === "warehouse_display" && (
          <p className="text-xs text-(--ink-soft)">线路名称为云仓展示补充</p>
        )}
        {quote.product_name && (
          <p className="mt-2 font-semibold">
            {displayRoute(quote.product_name)}
          </p>
        )}
        {quote.offer_name && (
          <p className="text-sm font-semibold">方案：{quote.offer_name}</p>
        )}
        {quote.service_description && (
          <p className="whitespace-pre-wrap text-sm">
            {quote.service_description}
          </p>
        )}
        {quote.source_name && (
          <p className="text-xs text-(--ink-soft)">
            {quote.source_name} · {quote.departure_code}
          </p>
        )}
        <h3 className="mt-1 text-lg font-semibold">
          {quote.departure_date} 出发
        </h3>
        <p className="text-xs text-(--ink-soft)">
          团期业务时区：
          {quote.business_timezone === "Asia/Shanghai"
            ? "北京时间（Asia/Shanghai）"
            : (quote.business_timezone ?? "历史快照未记录，请重新询价")}
        </p>
        <p className="text-sm text-(--ink-soft)">
          {quote.party.adults} 成人 / {quote.party.children} 儿童 /{" "}
          {quote.party.seniors} 老人 · {quote.party.single_rooms} 间单房
        </p>
      </div>
      {quote.party.rooms && (
        <p className="text-sm text-(--ink-soft)">
          房型：双人房 {quote.party.rooms.doubles} 间 / 双床房{" "}
          {quote.party.rooms.twins} 间 / 单人房 {quote.party.rooms.singles} 间 ·
          {bedText(quote.party.rooms, "儿童占床不适用或待确认")}
        </p>
      )}
      {stale && (
        <p
          role="status"
          className="rounded-xl bg-(--warn-soft) p-3 text-sm text-(--warn)"
        >
          历史报价，仅供回顾；请重新询价后再向客户确认。
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        {(
          [
            ["市场价", quote.market_total, quote.known_market_subtotal],
            [
              "同行结算价",
              quote.settlement_total,
              quote.known_settlement_subtotal,
            ],
          ] as const
        ).map(([label, total, subtotal]) => (
          <div key={label} className="rounded-xl bg-(--well) p-3">
            <p className="text-xs text-(--ink-soft)">{label}</p>
            <p className="mt-1 font-semibold tabular-nums">
              {amount(total, quote.currency)}
            </p>
            {total === null && subtotal !== null && (
              <p className="mt-1 text-xs">
                已知部分 {amount(subtotal, quote.currency)}
              </p>
            )}
          </div>
        ))}
      </div>
      {!quote.complete && (
        <div className="rounded-xl bg-(--warn-soft) p-3 text-sm text-(--warn)">
          <p className="font-semibold">费用尚未完整，不能作为全包总价。</p>
          <ul className="mt-2 list-inside list-disc">
            {quote.missing_items.map((item) => (
              <li key={item}>{missingLabel(item)}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="space-y-2 text-sm">
        {quote.market_lines.map((line, i) => (
          <div
            key={`${line.code}-${i}`}
            className="flex justify-between gap-2 border-b border-(--line) pb-2"
          >
            <span>
              {line.label ?? names[line.code] ?? line.code} × {line.quantity}
            </span>
            <span className="text-right">
              市场 {amount(line.total, quote.currency)}
              <br />
              <span className="text-(--ink-soft)">
                同行结算{" "}
                {amount(
                  quote.settlement_lines[i]?.total ?? null,
                  quote.currency,
                )}
              </span>
            </span>
          </div>
        ))}
      </div>
      {!!quote.included_items?.length && (
        <p className="text-sm">已确认包含：{quote.included_items.join("、")}</p>
      )}
      <p className="text-sm">
        {stale || !quote.availability || quote.availability === "unknown"
          ? "库存待确认"
          : quote.availability === "available"
            ? "有位"
            : "无位"}
        {!stale && quote.capacity_sufficient === false
          ? "，不足以满足本次人数"
          : ""}
      </p>
      <div className="space-y-1 text-xs text-(--ink-soft)">
        <p>查询时间：{stamp(quote.observed_at)}</p>
        <p>
          价格{" "}
          {Math.max(
            0,
            Math.floor((now - new Date(quote.observed_at).getTime()) / 60000),
          )}{" "}
          分钟前核实
        </p>
        <p>
          报价单有效至：{stamp(quote.quote_valid_until || quote.fresh_until)}
        </p>
        <p>来源价格有效期：{stamp(quote.expires_at)}</p>
        {quote.local_booking_deadline && (
          <p>云仓报名截止：{stamp(quote.local_booking_deadline)}</p>
        )}
        <p>以上均为北京时间。报价未占位，库存仍可能变化。</p>
      </div>
    </article>
  );
}
