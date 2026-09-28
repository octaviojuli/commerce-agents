"use client";
import type { WarehouseEvent } from "web-shared/warehouse-client";
import type { DealDetail } from "./copilot-types";
import { fieldNames, fieldText } from "./BriefPanel";
import ResultCards from "./ResultCards";
import OfferCards from "./OfferCards";
import { QuoteCard } from "./Quote";
import { ComparisonCard } from "./CopilotViews";
import { copyText } from "./mobile";

export default function CopilotConversation({
  events,
  turnId,
  detail,
  locked,
  api,
  run,
  act,
  compare,
  chip,
  notify,
}: {
  events: WarehouseEvent[];
  turnId?: string;
  detail: DealDetail;
  locked: boolean;
  api: any;
  run: (path: string, body?: any) => Promise<any>;
  act: (value: any) => Promise<any>;
  compare: (ids: string[]) => void;
  chip: (action: string, label: string) => void;
  notify: (s: string) => void;
}) {
  const prefix = "/copilot/deals/" + detail.id;
  const reply = events.find(
    (e) => e.type === "ui" && e.data.component === "copilot_reply",
  );
  const resolved =
    reply?.data.payload?.next_action === "accept_changes" &&
    !Object.keys(detail.pending_fields || {}).length;
  const conclusion = resolved
    ? "需求变化已处理，可继续下一步。"
    : reply?.data.payload?.to_advisor ||
      events
        .filter((e) => e.type === "text_delta")
        .map((e) => e.data.text)
        .join("");
  function card(e: WarehouseEvent, n: number) {
    if (e.type !== "ui") return null;
    const p = e.data.payload || {},
      kind = e.data.component,
      stale =
        p.brief_version !== undefined &&
        p.brief_version !== detail.brief.version,
      disabled = locked || stale;
    if (kind === "warehouse_routes" || kind === "warehouse_departures")
      return (
        <div key={n} className="cp-card-stack">
          {stale && <p className="cp-muted">需求已更新，请重新检索</p>}
          <ResultCards
            api={api}
            payload={p}
            departures={kind === "warehouse_departures"}
            historical={stale}
            busy={disabled}
            act={act}
            more={act}
            compare={compare}
          />
        </div>
      );
    if (kind === "warehouse_offers")
      return (
        <OfferCards key={n} payload={p} busy={disabled} act={act} more={act} />
      );
    if (kind === "warehouse_quote")
      return (
        <QuoteCard
          key={n}
          quote={{ ...p, quote_expired: stale || p.quote_expired }}
        />
      );
    if (kind === "copilot_directions")
      return (
        <section className="cp-directions" key={n}>
          {(p.items || []).map((d: any) => (
            <article className="cp-panel" key={d.id}>
              <span className="cp-route-cover" aria-hidden>
                {d.name.slice(0, 2)}
              </span>
              <h3>{d.name}</h3>
              <p>
                {d.days_min}–{d.days_max} 天 · {d.count} 条在售线路
              </p>
              {d.price_range && <p>{d.price_range.currency} {d.price_range.min}–{d.price_range.max} / 成人起</p>}
              <small>{d.price_note}</small>
              <button
                className="cp-primary"
                disabled={disabled}
                onClick={async () => {
                  if (await run(prefix + "/directions/" + d.id))
                    notify("已记为探索方向，可以按当前在售日期找线。");
                }}
              >
                就看这个方向
              </button>
            </article>
          ))}
          {!p.items?.length && <p>当前暂无在售方向。</p>}
        </section>
      );
    if (kind === "copilot_proposal") {
      const remaining = detail.pending_fields?.[p.proposal_id] || [];
      if (!remaining.length)
        return (
          <div className="cp-resolved" key={n}>
            v{p.brief_version} → v{detail.brief.version}：
            {Object.keys(p.fields || {})
              .map((k) => fieldNames[k] || k)
              .join("、")}{" "}
            · 已处理{" "}
            <button
              disabled={locked || !detail.brief.readiness.search.ready}
              onClick={() => act({ action: "search_routes" })}
            >
              按 v{detail.brief.version} 重新找线
            </button>
          </div>
        );
      return (
        <section className="cp-panel cp-change" key={n}>
          <span className="cp-eyebrow">需求变化 · 逐项决定</span>
          {Object.entries(p.fields || {})
            .filter(([name]) => remaining.includes(name))
            .map(([name, v]: [string, any]) => (
              <div className="cp-change-row" key={name}>
                <b>{fieldNames[name] || name}</b>
                <div>
                  <del>{fieldText(name, p.previous?.[name]?.value)}</del>
                  <strong>{fieldText(name, v.value)}</strong>
                </div>
                <small>
                  {v.source === "inferred"
                    ? "推断：" + v.hint
                    : "原话：" + v.evidence}
                </small>
                <div className="cp-row">
                  <button
                    disabled={locked}
                    onClick={() =>
                      run(prefix + "/proposals/" + p.proposal_id, {
                        accept: true,
                        fields: [name],
                      })
                    }
                  >
                    采纳此项
                  </button>
                  <button
                    disabled={locked}
                    onClick={() =>
                      run(prefix + "/proposals/" + p.proposal_id, {
                        accept: false,
                        fields: [name],
                      })
                    }
                  >
                    保留原值
                  </button>
                </div>
              </div>
            ))}
          <div className="cp-impact">
            {Array.isArray(p.impact)
              ? p.impact.map((i: any, j: number) => <p key={j}>{i.text}</p>)
              : p.impact}
          </div>
          <button
            className="cp-primary"
            disabled={locked}
            onClick={() =>
              run(prefix + "/proposals/" + p.proposal_id, {
                accept: true,
                fields: remaining,
              })
            }
          >
            采纳以上 {remaining.length} 项
          </button>
        </section>
      );
    }
    if (kind === "copilot_choice")
      return (
        <section className="cp-panel" key={n}>
          <span className="cp-eyebrow">请你决定</span>
          <h3>{p.title}</h3>
          <button
            className="cp-primary"
            disabled={disabled}
            onClick={() =>
              act({
                action: p.action,
                product_id: p.product_id,
                ...(p.offer_id ? { offer_id: p.offer_id } : {}),
              })
            }
          >
            {p.action === "departures"
              ? "确认选线 · 看团期"
              : p.action === "offers"
                ? "确认团期 · 看方案"
                : "核对结算价"}
          </button>
          {stale && <small>需求已变化，请重新选择。</small>}
        </section>
      );
    if (kind === "copilot_comparison")
      return (
        <ComparisonCard
          key={n}
          data={p}
          busy={locked}
          save={() =>
            void run(prefix + "/plans", {
              product_ids: p.routes.map((r: any) => r.product_id),
            })
          }
        />
      );
    if (kind === "copilot_facts")
      return (
        <details className="cp-panel" key={n}>
          <summary>本轮依据 · {(p.facts || []).length} 项</summary>
          {(p.notice || []).map((s: string) => (
            <p className="cp-muted" key={s}>
              {s}
            </p>
          ))}
          {(p.facts || []).map((f: any) => (
            <p key={f.fact_id}>
              {f.text}
              {!f.reviewed && <small> · 待核实</small>}
            </p>
          ))}
        </details>
      );
    return null;
  }
  const p = reply?.data.payload,
    stale = p?.brief_version !== detail.brief.version;
  return (
    <div className="cp-assistant">
      {conclusion && (
        <div className="cp-assistant-heading">
          <span aria-hidden>✧</span>
          <div>
            <small>顾问搭档</small>
            <p>{conclusion}</p>
            {p?.degraded?.length > 0 && <small>本轮理解不完整，可重试</small>}
          </div>
        </div>
      )}
      {events.filter((e) => e !== reply).map(card)}
      {p && !resolved && (
        <>
          <section className="cp-reply">
            <div className="cp-section-head">
              <b>建议回复</b>
              <div className="cp-row">
                {p.simplified && <span className="cp-tag">已简化</span>}
                <button
                  disabled={stale}
                  onClick={async () =>
                    notify(
                      (await copyText(p.to_customer))
                        ? "已复制，核对后自行发送。"
                        : "复制失败，请长按文字复制。",
                    )
                  }
                >
                  复制文字
                </button>
              </div>
            </div>
            <p>{p.to_customer}</p>
            <footer>
              <small>
                {stale
                  ? "基于旧需求，请重新整理"
                  : "需求 v" + p.brief_version + " · 核对后发送"}
              </small>
              {turnId && turnId !== "pending" && (
                <button
                  disabled={locked || stale}
                  onClick={() => run(prefix + "/sent", { turn_id: turnId })}
                >
                  标记已发
                </button>
              )}
            </footer>
          </section>
          {!stale && (
            <div className="cp-next">
              <span>接下来</span>
              <div>
                {(p.chips || [])
                  .filter((c: any) =>
                    (p.allowed_actions || []).includes(c.action),
                  )
                  .map((c: any) => (
                    <button
                      disabled={locked}
                      key={c.action+c.label}
                      onClick={() => chip(c.action, c.label)}
                    >
                      {c.label}
                    </button>
                  ))}
              </div>
              {p.customer_may_ask?.length > 0 && (
                <>
                  <span>客人可能会问</span>
                  <div>
                    {p.customer_may_ask.map((q: string) => (
                      <button
                        disabled={locked}
                        key={q}
                        onClick={() => chip("ask:" + q, q)}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
