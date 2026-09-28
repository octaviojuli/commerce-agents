"use client";
import RoutePreview from "./RoutePreview";
import type {WarehouseClient} from "web-shared/warehouse-client";
import { useState } from "react";
import type { WarehouseProduct, WorkbenchAction } from "@/lib/warehouse";

type Result = {
  items?: WarehouseProduct[];
  next_cursor?: string | null;
  out_of_window_total?: number;
  product_id?: string;
  query?: string;
  filters?: Record<string, string>;
};
export default function ResultCards({
  payload,
  api,
  departures,
  historical,
  busy,
  act,
  more,
}: {
  payload: Result;
  api: WarehouseClient;
  departures: boolean;
  historical: boolean;
  busy: boolean;
  act: (a: WorkbenchAction) => unknown;
  more: (a: WorkbenchAction) => Promise<Result | undefined>;
}) {
  const [extra, setExtra] = useState<WarehouseProduct[]>([]),
    [cursor, setCursor] = useState<string | null | undefined>(undefined),
    [count, setCount] = useState(3),
    [loading, setLoading] = useState(false),
    [open, setOpen] = useState(false);
  const all = [...(payload.items ?? []), ...extra].filter(
    (p, i, a) => a.findIndex((v) => v.product_id === p.product_id) === i,
  );
  const next = cursor === undefined ? payload.next_cursor : cursor;
  const inside = all.filter(
      (p) => p.attributes.capacity_match !== "out_of_window",
    ),
    outside = all.filter(
      (p) => p.attributes.capacity_match === "out_of_window",
    );
  const shown = departures ? inside : all.slice(0, count);
  async function load() {
    if (!departures && count < all.length) {
      setCount((v) => v + 3);
      return;
    }
    if (!next) return;
    setLoading(true);
    try {
      const r = await more({
        action: departures ? "departures" : "search_routes",
        product_id: payload.product_id,
        after: next,
        query: payload.query,
        filters: payload.filters,
      });
      if (r) {
        setExtra((v) => [...v, ...(r.items ?? [])]);
        setCursor(r.next_cursor ?? null);
        setCount((v) => v + 3);
      }
    } finally {
      setLoading(false);
    }
  }
  const card = (p: WarehouseProduct) => {
    let reasons: { criterion: string; verdict: string; text: string }[] = [];
    try {
      reasons = JSON.parse(p.attributes.match_reasons ?? "[]");
    } catch {}
    const good = reasons.filter((r) => r.verdict === "ok"),
      bad = reasons.filter((r) => r.verdict === "conflict");
    return (
      <article key={p.product_id} className="aw-result aw-product-card">
        {!departures && <RoutePreview api={api} productId={p.product_id}/>}
        {departures ? (
          <>
            <h3>
              {p.attributes.depart_date} →{" "}
              {p.attributes.return_date === "None"
                ? "返回待确认"
                : p.attributes.return_date}
            </h3>
            <p>
              {p.option_values?.团期} · {p.attributes.route_days || "待确认"} 天
            </p>
            <p
              className={
                p.attributes.capacity_match === "ok" ? "aw-ok" : "aw-warn"
              }
            >
              {historical
                ? "历史团期，人数与余位请重新核实"
                : ({
                    ok: "可满足本次人数",
                    short: "不足本次人数",
                    unknown: "能否满足人数待确认",
                    out_of_window: "需求时间范围外",
                  }[p.attributes.capacity_match] ?? "余位待核实")}
            </p>
            {p.attributes.can_quote === "false" && (
              <p className="aw-warn">{p.attributes.sales_status_label}</p>
            )}
            <p>
              报名截止：
              {p.attributes.local_booking_deadline ||
                p.attributes.booking_deadline ||
                "上游未提供"}
            </p>
          </>
        ) : (
          <>
            <h3>{p.title}</h3>
            <p>
              {p.attributes.days || "待确认"} 天 ·{" "}
              {p.attributes.depart_city || "出发地待确认"}
            </p>
            {reasons.some((r) => r.criterion === "destinations" && r.verdict === "unknown") && (
              <p className="aw-warn">目的地信息匹配，具体行程覆盖待核对</p>
            )}
            {reasons.length > 0 && (
              <details className="aw-match">
                <summary>
                  {good.length} 项匹配
                  {bad.length
                    ? ` · ${bad.length} 项冲突（${bad.map((r) => ({ window: "日期", depart_city: "出发地", no_shopping: "购物" })[r.criterion] ?? r.criterion).join("、")}）`
                    : ""}
                  {reasons.length - good.length - bad.length > 0
                    ? ` · ${reasons.length - good.length - bad.length} 项待确认`
                    : ""}
                </summary>
                <ul>
                  {reasons.map((r) => (
                    <li
                      key={r.criterion}
                      className={
                        r.verdict === "ok"
                          ? "aw-ok"
                          : r.verdict === "conflict"
                            ? "aw-conflict"
                            : "aw-warn"
                      }
                    >
                      {r.text}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
        <button
          className="aw-primary"
          disabled={
            busy ||
            loading ||
            (departures && p.attributes.can_quote === "false")
          }
          onClick={() =>
            act({
              action: departures ? "quote" : "departures",
              product_id: p.product_id,
            })
          }
        >
          {departures ? "询价" : "查看团期"}
        </button>
        {departures && (
          <details className="aw-card-more">
            <summary>更多操作</summary>
            <button
              disabled={busy}
              onClick={() =>
                act({ action: "offers", product_id: p.product_id })
              }
            >
              查看报价方案
            </button>
          </details>
        )}
      </article>
    );
  };
  return (
    <div className="aw-results">
      {all.length === 0 && <p>当前条件下暂未找到结果，可以调整条件再查。</p>}
      {shown.map(card)}
      {departures && (payload.out_of_window_total ?? outside.length) > 0 && (
        <details
          className="aw-outside"
          open={open}
          onToggle={(e) => setOpen(e.currentTarget.open)}
        >
          <summary>
            其他日期 · {payload.out_of_window_total ?? outside.length} 个团期
          </summary>
          {outside.map(card)}
          {next && (
            <button disabled={busy || loading} onClick={load}>
              {loading ? "正在读取…" : "继续查看其他团期"}
            </button>
          )}
        </details>
      )}
      {((!departures && (count < all.length || next)) ||
        (departures && next && !outside.length)) && (
        <button disabled={busy || loading} onClick={load}>
          {loading ? "正在读取…" : departures ? "查看更多团期" : "查看更多线路"}
        </button>
      )}
    </div>
  );
}
