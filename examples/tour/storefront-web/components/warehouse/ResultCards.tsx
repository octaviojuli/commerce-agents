"use client";
import RoutePreview from "./RoutePreview";
import type { WarehouseClient } from "web-shared/warehouse-client";
import { useState } from "react";
import type { WarehouseProduct, WorkbenchAction } from "@/lib/warehouse";
import { displayRoute } from "./CopilotViews";
type Result = {
  items?: WarehouseProduct[];
  next_cursor?: string | null;
  out_of_window_total?: number;
  product_id?: string;
  query?: string;
  filters?: Record<string, string>;
  funnel?: any;
};
export default function ResultCards({
  payload,
  api,
  departures,
  historical,
  busy,
  act,
  more,
  compare,
}: {
  payload: Result;
  api: WarehouseClient;
  departures: boolean;
  historical: boolean;
  busy: boolean;
  act: (a: WorkbenchAction) => unknown;
  more: (a: WorkbenchAction) => Promise<Result | undefined>;
  compare?: (ids: string[]) => void;
}) {
  const [extra, setExtra] = useState<WarehouseProduct[]>([]),
    [cursor, setCursor] = useState<string | null | undefined>(),
    [loading, setLoading] = useState(false),
    [selected, setSelected] = useState<string[]>([]);
  const all = [...(payload.items || []), ...extra].filter(
      (p, i, a) => a.findIndex((v) => v.product_id === p.product_id) === i,
    ),
    next = cursor === undefined ? payload.next_cursor : cursor;
  async function load() {
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
        setExtra((v) => [...v, ...(r.items || [])]);
        setCursor(r.next_cursor ?? null);
      }
    } finally {
      setLoading(false);
    }
  }
  function card(p: WarehouseProduct) {
    let reasons: { criterion: string; verdict: string; text: string }[] = [];
    try {
      reasons = JSON.parse(p.attributes.match_reasons || "[]");
    } catch {}
    const ordered = [
      ...reasons.filter((r) => r.verdict === "conflict"),
      ...reasons.filter((r) => r.verdict === "ok"),
      ...reasons.filter((r) => r.verdict === "unknown"),
    ].slice(0, 2);
    return (
      <article
        key={p.product_id}
        className={
          "aw-result aw-product-card " +
          (departures ? "cp-departure-card" : "cp-route-card")
        }
      >
        {departures ? (
          <>
            <h3>{p.attributes.depart_date}</h3>
            <p>
              {p.attributes.return_date === "None"
                ? "返回日期待确认"
                : p.attributes.return_date + " 返回"}{" "}
              · {p.attributes.route_days || "—"} 天
            </p>
            <p className="cp-muted">
              {historical
                ? "历史团期，请重新核实"
                : {
                    ok: "可满足本次人数",
                    short: "不足本次人数",
                    unknown: "能否满足人数待确认",
                    out_of_window: "需求窗口之外，可作为备选",
                  }[p.attributes.capacity_match] || "可售状态待核实"}
            </p>
            {p.attributes.can_quote === "false" && (
              <p className="aw-conflict">{p.attributes.sales_status_label}</p>
            )}
            <div className="cp-row">
              <button
                className="aw-primary"
                disabled={busy || loading || p.attributes.can_quote === "false"}
                onClick={() =>
                  act({ action: "offers", product_id: p.product_id })
                }
              >
                选择此团期
              </button>
              <button
                disabled={busy || p.attributes.can_quote === "false"}
                onClick={() =>
                  act({ action: "quote", product_id: p.product_id })
                }
              >
                询价
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="cp-route-cover" aria-hidden>
              {p.attributes.depart_city || "旅行"}{" "}
              <span>{p.attributes.days || "—"} DAYS</span>
            </div>
            <h3>{displayRoute(p.title)}</h3>
            <small className="cp-route-meta">
              {p.title.match(/^[A-Z]{1,5}\d{1,5}/)?.[0] ||
                p.product_id.slice(0, 11)}{" "}
              · {p.attributes.days || "—"} 天 ·{" "}
              {p.attributes.depart_city || "出发地待核实"}
            </small>
            <div className="cp-route-stats">
              <span>
                酒店 <b>{p.attributes.hotel_count ? `${p.attributes.hotel_count} 处已知` : "待核实"}</b>
              </span>
              <span>
                含餐 <b>{p.attributes.meal_count ? `${p.attributes.meal_count} 餐${p.attributes.meal_partial === "true" ? "已知" : ""}` : "待核实"}</b>
              </span>
              <span>
                购物{" "}
                <b>
                  {p.attributes.shopping_count === "0" || reasons.find((r) => r.criterion === "no_shopping")
                    ?.verdict === "ok"
                    ? "0 店"
                    : "待核实"}
                </b>
              </span>
            </div>
            <div className="cp-match-lines">
              {ordered.map((r) => (
                <p
                  key={r.criterion}
                  className={
                    r.verdict === "conflict"
                      ? "aw-conflict"
                      : r.verdict === "ok"
                        ? "aw-ok"
                        : "cp-muted"
                  }
                >
                  {r.verdict === "conflict" ? "冲突 · " : ""}
                  {r.text}
                </p>
              ))}
            </div>
            <small className="cp-route-date">
              {p.attributes.next_departure
                ? "窗口内 " + p.attributes.next_departure + " 起有团"
                : "团期待查看"}{" "}
              · 报名待核实
            </small>
            <p className="cp-price-line">
              客人价 <b>待选团期核价</b>
              <small>同业价与毛利待核算</small>
            </p>
            {compare && (
              <label className="cp-compare-check">
                <input
                  type="checkbox"
                  aria-label={"比较 " + displayRoute(p.title)}
                  checked={selected.includes(p.product_id)}
                  disabled={
                    busy ||
                    (!selected.includes(p.product_id) && selected.length >= 3)
                  }
                  onChange={(e) =>
                    setSelected((v) =>
                      e.target.checked
                        ? [...v, p.product_id]
                        : v.filter((id) => id !== p.product_id),
                    )
                  }
                />
                加入比较
              </label>
            )}
            <div className="cp-route-actions">
              <button
                className="aw-primary"
                disabled={busy || loading}
                onClick={() =>
                  act({ action: "departures", product_id: p.product_id })
                }
              >
                看团期
              </button>
              <RoutePreview api={api} productId={p.product_id} />
            </div>
          </>
        )}
      </article>
    );
  }
  const inside = all.filter(
      (p) => p.attributes.capacity_match !== "out_of_window",
    ),
    outside = all.filter(
      (p) => p.attributes.capacity_match === "out_of_window",
    );
  return (
    <section className="cp-results">
      {payload.funnel && (
        <details className="cp-funnel">
          <summary>
            条件漏斗 ·{" "}
            {payload.funnel.stages.map((s: any) => s.count).join(" → ")} 条
          </summary>
          <p>必须满足：{payload.funnel.hard.join("、")}</p>
          <p>尽量满足：{payload.funnel.soft.join("、")}</p>
          {payload.funnel.stages.map((s: any) => (
            <p key={s.label}>
              {s.label} · {s.count} 条
            </p>
          ))}
          {payload.funnel.relaxations
            .filter((r: any) => r.additional > 0)
            .map((r: any) => (
              <p key={r.field}>
                放宽{r.label}可多 {r.additional} 条；由你在需求单决定。
              </p>
            ))}
        </details>
      )}
      {!all.length && (
        <p className="cp-empty-line">
          没有满足当前范围和在售日期的线路，请查看条件漏斗。
        </p>
      )}
      <div className={departures ? "cp-departures" : "cp-route-carousel"}>
        {(departures ? inside : all).map(card)}
      </div>
      {departures && outside.length > 0 && (
        <details>
          <summary>
            其他日期 · {payload.out_of_window_total ?? outside.length} 个
          </summary>
          {outside.map(card)}
        </details>
      )}
      {!departures && compare && all.length > 1 && (
        <button
          disabled={busy || selected.length < 2}
          onClick={() => compare(selected)}
        >
          比较选中的 {selected.length} 条线路
        </button>
      )}
      {next && (
        <button disabled={busy || loading} onClick={load}>
          {loading ? "读取中…" : "查看更多"}
        </button>
      )}
    </section>
  );
}
