"use client";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  WarehouseClient,
  message,
  warehouseEvents,
  type WarehouseEvent,
} from "web-shared/warehouse-client";
import type {
  Deal,
  DealDetail,
  Transcript,
  Inquiry,
  Turn,
} from "./copilot-types";
import { type WorkbenchAction, stamp } from "@/lib/warehouse";
import BriefPanel, { fieldNames } from "./BriefPanel";
import CopilotBusiness from "./CopilotBusiness";
import CopilotConversation from "./CopilotConversation";
import CopilotMemory from "./CopilotMemory";
import {
  ComparisonCard,
  DealCard,
  StageStrip,
  stageOf,
  stripCodes,
  stages,
} from "./CopilotViews";
import { Sheet, useMobile, useViewport } from "./mobile";

// What the status bar says the deal needs next, by stage.
const stageNext = [
  "",
  "选一条线路",
  "选团期",
  "核对确认单",
  "报价可分享",
  "已成交 · 收证件与行前",
];

export default function CopilotDeal({
  api,
  id,
  back,
  deals = [],
  openDeal,
}: {
  api: WarehouseClient;
  id: string;
  back: () => void;
  deals?: Deal[];
  openDeal?: (id: string) => void;
}) {
  const [detail, setDetail] = useState<DealDetail | null>(null),
    [transcript, setTranscript] = useState<Transcript>({
      turns: [],
      busy: false,
      next_cursor: null,
    });
  const [inquiries, setInquiries] = useState<Inquiry[]>([]),
    [tab, setTab] = useState("need"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false),
    [input, setInput] = useState(""),
    [live, setLive] = useState<WarehouseEvent[]>([]),
    [progress, setProgress] = useState("");
  const [sheet, setSheet] = useState(false),
    [comparison, setComparison] = useState<any>(null);
  const mobile = useMobile();
  useViewport();
  const scroll = useRef<HTMLDivElement | null>(null),
    side = useRef<HTMLDivElement | null>(null);
  const mounted = useRef(true),
    sending = useRef(false),
    controller = useRef<AbortController | null>(null),
    tail = useRef<HTMLDivElement | null>(null);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const d = await api.get<DealDetail>(`/copilot/deals/${id}`, signal);
        const [t, i] =
          d.conversation_available === false
            ? [{ turns: [], busy: false, next_cursor: null }, { items: [] }]
            : await Promise.all([
                api.get<Transcript>(`/conversations/${id}`, signal),
                api.get<{ items: Inquiry[] }>(
                  `/copilot/inquiries?deal_id=${id}`,
                  signal,
                ),
              ]);
        if (mounted.current && !signal?.aborted) {
          setDetail(d);
          setTranscript(t);
          setInquiries(i.items);
          if (!t.busy) setProgress("");
        }
      } catch (e) {
        if (mounted.current && !signal?.aborted) {
          setError(message(e));
          setDetail(null);
          setTranscript({ turns: [], busy: false, next_cursor: null });
          setInquiries([]);
        }
      }
    },
    [api, id],
  );
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    void load(abort.signal);
    const refresh = () => {
      if (document.visibilityState === "visible" && !sending.current)
        void load(abort.signal);
    };
    const timer = setInterval(refresh, 15000);
    window.addEventListener("focus", refresh);
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      mounted.current = false;
      abort.abort();
      controller.current?.abort();
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("online", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load]);
  useEffect(() => {
    if (!transcript.busy || busy) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [transcript.busy, busy, load]);
  useEffect(() => {
    scroll.current?.scrollTo({
      top: scroll.current.scrollHeight,
      behavior: "smooth",
    });
  }, [live.length, progress]);
  const locked = busy || transcript.busy;
  async function run(path: string, body: Record<string, unknown> = {}) {
    if (!detail || locked) return null;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.post(path, {
        request_id: crypto.randomUUID(),
        expected_version: detail.brief.version,
        ...body,
      });
      await load();
      return result;
    } catch (e) {
      setError(message(e));
      return null;
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function act(action: WorkbenchAction) {
    if (!detail || locked) return undefined;
    const result = await run(`/conversations/${id}/actions`, action);
    requestAnimationFrame(() =>
      scroll.current?.scrollTo({
        top: scroll.current.scrollHeight,
        behavior: "smooth",
      }),
    );
    return result?.payload;
  }
  async function save(fields: Record<string, unknown>) {
    if (!detail || locked) return false;
    setBusy(true);
    setError("");
    try {
      await api.response(`/conversations/${id}/brief`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_version: detail.brief.version,
          fields,
        }),
      });
      await load();
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!input.trim() || locked || detail?.conversation_available === false)
      return;
    const value = input;
    setInput("");
    setBusy(true);
    sending.current = true;
    setError("");
    setLive([]);
    setProgress("已收到，正在整理…");
    const abort = new AbortController();
    controller.current = abort;
    let complete = false;
    setTranscript((t) => ({
      ...t,
      turns: [
        ...t.turns,
        { id: "pending", message: value, status: "running", events: [] },
      ],
    }));
    try {
      const response = await api.response(`/conversations/${id}/chat`, {
        method: "POST",
        signal: abort.signal,
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ message: value }),
      });
      if (!response.body) throw new Error("助手暂未返回内容");
      for await (const e of warehouseEvents(response.body)) {
        if (abort.signal.aborted) return;
        if (e.type === "progress") setProgress(String(e.data.message));
        else if (e.type === "turn_complete") complete = true;
        else if (e.type === "error")
          throw new Error(e.data.message || "本轮未完成");
        else setLive((v) => [...v, e]);
      }
      if (!complete) throw new Error("连接中断，正在恢复已保存的会话。");
    } catch (e) {
      if (!abort.signal.aborted) setError(message(e));
    } finally {
      sending.current = false;
      if (mounted.current) {
        setBusy(false);
        await load();
        setLive([]);
        setProgress("");
      }
    }
  }
  if (!detail)
    return (
      <div className="cp-app">
        <header className="cp-header">
          <button onClick={back}>‹ 返回</button>
        </header>
        <main className="cp-home">
          <p role={error ? "alert" : "status"}>{error || "正在恢复跟单…"}</p>
          <button onClick={() => void load()}>重新读取</button>
        </main>
      </div>
    );
  const b = detail.brief.body,
    hasRoutes = transcript.turns.some((t) =>
      t.events.some(
        (e) => e.type === "ui" && e.data.component === "warehouse_routes",
      ),
    ),
    stage = stageOf(b, Boolean(detail.ledger?.sale), hasRoutes);
  const missing = [
    ...new Set(
      detail.brief.readiness.quote.missing.map(
        (k: string) => fieldNames[k] || k,
      ),
    ),
  ];
  function openPanel(name: string) {
    setTab(name);
    setSheet(true);
    side.current?.scrollTo({ top: 0 });
  }
  async function compare(ids: string[]) {
    const value = await run("/copilot/deals/" + id + "/compare", {
      product_ids: ids,
    });
    if (value) {
      setComparison(value);
      openPanel("compare");
    }
  }
  function chip(action: string, label: string) {
    if (action === "search_routes")
      return void act({ action: "search_routes" });
    if (action === "list_departures" && b.route_id)
      return void act({ action: "departures", product_id: b.route_id });
    if (
      [
        "quote",
        "share_quote",
        "recheck_price",
        "build_confirmation",
        "record_confirmation",
      ].includes(action)
    )
      return openPanel("quote");
    if (["collect_documents", "predeparture_task", "add_note"].includes(action))
      return openPanel("follow");
    if (action === "accept_changes") return openPanel("need");
    setInput(
      action.startsWith("ask:")
        ? action.slice(4)
        : action === "present_directions"
          ? "我也没想好，你推荐吧"
          : label,
    );
    document.getElementById("copilot-message")?.focus();
  }
  const content = (
    <>
      {tab === "need" ? (
        <>
          <BriefPanel brief={detail.brief} busy={locked} save={save} />
          <CopilotMemory detail={detail} busy={locked} run={run} />
        </>
      ) : tab === "compare" && comparison ? (
        <ComparisonCard
          data={comparison}
          busy={locked}
          save={() =>
            void run("/copilot/deals/" + id + "/plans", {
              product_ids: comparison.routes.map((r: any) => r.product_id),
            })
          }
        />
      ) : (
        <CopilotBusiness
          api={api}
          detail={detail}
          tab={tab}
          inquiries={inquiries}
          busy={locked}
          run={run}
          act={act}
          refresh={load}
          notify={setNotice}
          fail={setError}
        />
      )}
    </>
  );
  const panelNav = (
    <div className="cp-panel-nav">
      {[
        ["need", "需求记忆"],
        ["quote", "方案报价"],
        ["follow", "核实跟进"],
      ].map(([key, label]) => (
        <button
          key={key}
          aria-current={tab === key ? "page" : undefined}
          onClick={() => {
            setTab(key);
            side.current?.scrollTo({ top: 0 });
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
  const assistant = (events: WarehouseEvent[], turnId?: string) => (
    <CopilotConversation
      events={events}
      turnId={turnId}
      detail={detail}
      locked={locked}
      api={api}
      run={run}
      act={act}
      compare={compare}
      chip={chip}
      notify={setNotice}
    />
  );
  return (
    <div className="cp-app cp-deal">
      <div className="cp-fixed-top">
        <header className="cp-header">
          <button className="cp-back" onClick={back} aria-label="返回工作台">
            ‹
          </button>
          <div className="cp-brand">
            <b>{detail.customer?.body.name || detail.title}</b>
            <span>
              {stages[stage]} ·{" "}
              {detail.updated_at ? stamp(detail.updated_at) : "等待第一条消息"}
            </span>
          </div>
          <button onClick={() => openPanel("follow")} aria-label="更多跟单功能">
            更多
          </button>
        </header>
        <div className="cp-brief-status">
          <button onClick={() => openPanel("need")}>
            <b>v{detail.brief.version}</b>
            <span>
              {(
                b.destinations?.value ||
                b.destination_regions?.value ||
                b.themes?.value ||
                []
              ).join("·") || "方向待定"}{" "}
              ·{" "}
              {stage === 0
                ? detail.brief.readiness.search.ready
                  ? "可找线"
                  : "先补方向和时间"
                : stageNext[stage]}
            </span>
            <small>
              {missing.length
                ? "报价缺 " + missing.length + " 项"
                : "报价条件齐"}{" "}
              ›
            </small>
          </button>
          <StageStrip stage={stage} />
        </div>
      </div>
      <div className="cp-workspace">
        <aside className="cp-sidebar">
          <h2>我的跟单</h2>
          {deals.map((d) => (
            <DealCard
              key={d.id}
              deal={d}
              active={d.id === id}
              open={openDeal || (() => back())}
            />
          ))}
          <button onClick={back}>返回今日</button>
        </aside>
        <section className="cp-chat-column">
          <main className="cp-chat-scroll" ref={scroll}>
            {error && (
              <div role="alert" className="cp-error">
                {error}
                <button
                  onClick={() => {
                    setError("");
                    void load();
                  }}
                >
                  刷新核对
                </button>
              </div>
            )}
            {notice && (
              <p role="status" className="cp-notice">
                {notice}
              </p>
            )}
            {detail.conversation_available === false && (
              <p className="cp-notice">
                供采权限已变化，请新建跟单重新选线。客户与线下台账仍可管理。
              </p>
            )}
            {transcript.next_cursor && (
              <button
                className="cp-history"
                onClick={async () => {
                  try {
                    const p = await api.get<Transcript>(
                      "/conversations/" +
                        id +
                        "?before=" +
                        transcript.next_cursor,
                    );
                    setTranscript((t) => ({
                      ...t,
                      turns: [...p.turns, ...t.turns],
                      next_cursor: p.next_cursor,
                    }));
                  } catch (e) {
                    setError(message(e));
                  }
                }}
              >
                更早的沟通
              </button>
            )}
            {!transcript.turns.length && (
              <section className="cp-welcome">
                <span aria-hidden>✧</span>
                <h2>从客人的一句话开始</h2>
                <p>粘贴微信原话，搭档会帮你整理需求和下一步。</p>
              </section>
            )}
            {transcript.turns.map((t: Turn) => (
              <section className="cp-turn" key={t.id}>
                {t.message.startsWith("▸") ? (
                  <div className="cp-human cp-operation">
                    <small>顾问操作</small>
                    <p>{stripCodes(t.message.replace(/^▸\s*/, ""))}</p>
                  </div>
                ) : (
                  <div className="cp-human">
                    <small>客人 · 从微信粘贴</small>
                    <p>{t.message}</p>
                  </div>
                )}
                {assistant(t.events, t.id)}
                {t.status === "interrupted" && (
                  <p className="cp-error">本轮未完成，原话已保留，请重试。</p>
                )}
              </section>
            ))}
            {assistant(live)}
            {(progress || transcript.busy) && (
              <p role="status" className="cp-progress">
                {progress || "正在恢复处理结果…"}
              </p>
            )}
            <div ref={tail} />
          </main>
          <form className="cp-composer" onSubmit={send}>
            <label className="sr-only" htmlFor="copilot-message">
              粘贴客人原话或输入问题
            </label>
            <textarea
              id="copilot-message"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              maxLength={4000}
              rows={1}
              placeholder="粘贴客人原话…"
              disabled={locked}
            />
            <button
              className="cp-primary"
              aria-label="发送给搭档"
              disabled={
                locked ||
                !input.trim() ||
                detail.conversation_available === false
              }
            >
              {locked ? "处理中" : "发送 ↑"}
            </button>
          </form>
        </section>
        {!mobile && (
          <aside className="cp-side-panel" ref={side}>
            {panelNav}
            {content}
          </aside>
        )}
      </div>
      {mobile && sheet && (
        <Sheet
          title={
            tab === "need"
              ? "需求与记忆"
              : tab === "quote"
                ? "方案与报价"
                : tab === "compare"
                  ? "线路比较"
                  : "核实与跟进"
          }
          close={() => setSheet(false)}
        >
          {panelNav}
          {content}
        </Sheet>
      )}
    </div>
  );
}
