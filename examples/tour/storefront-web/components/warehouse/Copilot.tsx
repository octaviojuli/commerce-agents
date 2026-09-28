"use client";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import {
  WarehouseClient,
  message,
  type Organization,
} from "web-shared/warehouse-client";
import type { Customer, Deal } from "./copilot-types";
import CopilotDeal from "./CopilotDeal";
import ShareHistory from "./ShareHistory";
import LegacyCases from "./LegacyCases";
import RoutePreview from "./RoutePreview";
import { DealCard, nextStep } from "./CopilotViews";
import { stamp } from "@/lib/warehouse";
import "./copilot.css";

export default function Copilot({
  token,
  organization,
  organizations,
  select,
  logout,
}: {
  token: string;
  organization: Organization;
  organizations: Organization[];
  select: (id: string) => void;
  logout: () => void;
}) {
  const api = useMemo(
    () => new WarehouseClient(token, organization.id),
    [token, organization.id],
  );
  const [active, setActive] = useState<string | null>(null),
    [tab, setTab] = useState("today");
  const [deals, setDeals] = useState<Deal[]>([]),
    [customers, setCustomers] = useState<Customer[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [onlyPending, setOnlyPending] = useState(true);
  const [archiveRoute, setArchiveRoute] = useState<string | null>(null),
    [directions, setDirections] = useState<any[]>([]);
  const [query, setQuery] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [modal, setModal] = useState<"deal" | "customer" | null>(null);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const [d, c] = await Promise.all([
          api.get<{ items: Deal[]; next_cursor: string | null }>(
            "/copilot/deals?query=" + encodeURIComponent(query),
            signal,
          ),
          api.get<{ items: Customer[] }>(
            "/copilot/customers?limit=100",
            signal,
          ),
        ]);
        if (!signal?.aborted) {
          setDeals(d.items);
          setCursor(d.next_cursor);
          setCustomers(c.items);
          setError("");
        }
      } catch (e) {
        if (!signal?.aborted) {
          setError(message(e));
          setDeals([]);
          setCustomers([]);
        }
      }
    },
    [api, query],
  );
  useEffect(() => {
    const abort = new AbortController();
    void load(abort.signal);
    const refresh = () => void load(abort.signal);
    window.addEventListener("focus", refresh);
    const timer = setInterval(refresh, 30000);
    return () => {
      abort.abort();
      window.removeEventListener("focus", refresh);
      clearInterval(timer);
    };
  }, [load]);
  useEffect(() => {
    const read = () => {
      const id = new URLSearchParams(location.search).get("deal");
      setActive(id && /^[0-9a-f-]{36}$/i.test(id) ? id : null);
    };
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  useEffect(() => {
    window.scrollTo({ top: 0 });
    if (tab === "routes")
      api
        .get<{ items: any[] }>("/copilot/directions")
        .then((p) => setDirections(p.items))
        .catch((e) => setError(message(e)));
  }, [tab, api]);
  function open(id: string | null) {
    setActive(id);
    history.pushState(null, "", id ? "?deal=" + id : location.pathname);
    window.scrollTo({ top: 0 });
    if (!id) void load();
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      if (modal === "customer")
        await api.post("/copilot/customers", {
          request_id: crypto.randomUUID(),
          customer: {
            name: String(data.get("name")),
            contact: String(data.get("contact") || ""),
            note: "",
            travelers: [],
          },
        });
      else {
        const item = await api.post<Deal>("/copilot/deals", {
          request_id: crypto.randomUUID(),
          title: String(data.get("title")),
          customer_id: data.get("customer") || null,
        });
        open(item.id);
      }
      setModal(null);
      await load();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const deadlines = deals
    .filter((d) => d.deadline)
    .sort((a, b) => Date.parse(a.deadline!) - Date.parse(b.deadline!));
  const waiting = deals.filter((d) => d.waiting_reply || d.pending_tasks);
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
  }).format(new Date());
  const timepoints = deals.filter((d) => d.departure_date === today);
  const shown = onlyPending
    ? deals.filter((d) => !d.sold || d.pending_tasks || d.waiting_reply)
    : deals;
  if (active)
    return (
      <CopilotDeal
        key={active}
        api={api}
        id={active}
        back={() => open(null)}
        deals={deals}
        openDeal={open}
      />
    );
  return (
    <div className="cp-app">
      <header className="cp-header">
        <div className="cp-mark" aria-hidden>
          旅
        </div>
        <div className="cp-brand">
          <b>
            {
              {
                today: "今日",
                deals: "我的跟单",
                routes: "线路灵感",
                me: "我的",
              }[tab]
            }
          </b>
          <span>顾问搭档 · {organization.name}</span>
        </div>
        <button aria-label="新建跟单" onClick={() => setModal("deal")}>
          ＋ 跟单
        </button>
      </header>
      <main className="cp-home">
        {error && (
          <div role="alert" className="cp-error">
            {error}
            <button onClick={() => void load()}>重新读取</button>
          </div>
        )}
        {tab === "today" && (
          <div className="cp-today">
            <section>
              <div className="cp-section-head">
                <h2>有时限的待办</h2>
                <span className="cp-tag">{deadlines.length} 项</span>
              </div>
              {deadlines.length ? (
                deadlines.map((d) => (
                  <button
                    className="cp-today-item cp-deadline"
                    key={d.id}
                    onClick={() => open(d.id)}
                  >
                    <b>{d.customer_name || d.title}</b>
                    <span>报价将于 {stamp(d.deadline!)} 到期</span>
                    <small>跟进客人，或重新核价 ›</small>
                  </button>
                ))
              ) : (
                <p className="cp-empty-line">暂无即将到期的报价。</p>
              )}
            </section>
            <section>
              <div className="cp-section-head">
                <h2>等你回复</h2>
                <button className="cp-link" onClick={() => setTab("deals")}>
                  全部跟单 ›
                </button>
              </div>
              {waiting.length ? (
                waiting.map((d) => (
                  <button
                    className="cp-today-item"
                    key={d.id}
                    onClick={() => open(d.id)}
                  >
                    <b>
                      {d.customer_name || d.title} · {nextStep(d)}
                    </b>
                    <span>
                      {d.draft?.slice(0, 110) || "打开跟单，核对最新消息。"}
                    </span>
                  </button>
                ))
              ) : (
                <p className="cp-empty-line">暂时没有等待处理的回复。</p>
              )}
            </section>
            <section>
              <h2>今天的时间点</h2>
              {timepoints.slice(0, 3).map((d) => (
                <button
                  className="cp-today-item"
                  key={d.id}
                  onClick={() => open(d.id)}
                >
                  <b>{d.customer_name || d.title}</b>
                  <span>{d.brief.departure_title}</span>
                </button>
              ))}
              {!timepoints.length && (
                <p className="cp-empty-line">团期和行前提醒会显示在这里。</p>
              )}
            </section>
            <section>
              <h2>继续推进</h2>
              <div className="cp-deal-grid">
                {deals
                  .filter((d) => !d.sold)
                  .slice(0, 4)
                  .map((d) => (
                    <DealCard key={d.id} deal={d} open={open} />
                  ))}
              </div>
              {!deals.length && (
                <button className="cp-primary" onClick={() => setModal("deal")}>
                  开始第一份跟单
                </button>
              )}
            </section>
          </div>
        )}
        {tab === "deals" && (
          <>
            <div className="cp-list-tools">
              <input
                aria-label="搜索跟单"
                placeholder="客户、目的地或跟单名称"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <button
                aria-pressed={onlyPending}
                onClick={() => setOnlyPending((v) => !v)}
              >
                {onlyPending ? "待处理" : "全部"}
              </button>
            </div>
            <div className="cp-deal-grid">
              {shown.map((d) => (
                <DealCard key={d.id} deal={d} open={open} />
              ))}
            </div>
            {!shown.length && !error && (
              <p className="cp-empty-line">当前没有待处理跟单。</p>
            )}
            {cursor && (
              <button
                onClick={async () => {
                  try {
                    const p = await api.get<{
                      items: Deal[];
                      next_cursor: string | null;
                    }>(
                      "/copilot/deals?before=" +
                        cursor +
                        "&query=" +
                        encodeURIComponent(query),
                    );
                    setDeals((v) => [...v, ...p.items]);
                    setCursor(p.next_cursor);
                  } catch (e) {
                    setError(message(e));
                  }
                }}
              >
                加载更多跟单
              </button>
            )}
          </>
        )}
        {tab === "routes" && (
          <section>
            <h2>从一个方向开始</h2>
            <p className="cp-muted">按当前在售团期整理，价格在选团期后核实。</p>
            <div className="cp-deal-grid">
              {directions.map((d) => (
                <article className="cp-panel" key={d.id}>
                  <h3>{d.name}</h3>
                  <p>
                    {d.count} 条线路 · {d.days_min}–{d.days_max} 天
                  </p>
                  {d.price_range && <small>{d.price_range.currency} {d.price_range.min}–{d.price_range.max} / 成人起</small>}
                  <p>
                    {d.start} 至 {d.end}
                  </p>
                  <button onClick={() => setModal("deal")}>
                    新建跟单来探索
                  </button>
                </article>
              ))}
            </div>
            {!directions.length && (
              <p className="cp-empty-line">当前暂无可用方向。</p>
            )}
          </section>
        )}
        {tab === "me" && (
          <div className="cp-me">
            <section className="cp-panel">
              <div className="cp-section-head">
                <h2>客户档案</h2>
                <button onClick={() => setModal("customer")}>添加客户</button>
              </div>
              {customers.map((c) => (
                <p className="cp-record" key={c.id}>
                  <b>{c.body.name}</b>
                  <br />
                  <small>
                    {c.body.travelers.length} 位旅客 ·{" "}
                    {c.body.contact || "联系方式未填写"}
                  </small>
                </p>
              ))}
              {!customers.length && (
                <p className="cp-muted">建立客户档案，可关联多次旅行。</p>
              )}
            </section>
            <details className="cp-panel">
              <summary>报价分享管理</summary>
              <ShareHistory api={api} />
            </details>
            <details className="cp-panel">
              <summary>已迁入的历史档案</summary>
              <LegacyCases
                api={api}
                onCurrent={(p) => setArchiveRoute(p.product_id)}
              />
              {archiveRoute && (
                <RoutePreview api={api} productId={archiveRoute} />
              )}
            </details>
            {organizations.length > 1 && (
              <label>
                工作空间
                <select
                  value={organization.id}
                  onChange={(e) => select(e.target.value)}
                >
                  {organizations.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button onClick={logout}>退出登录</button>
            <p className="cp-muted">
              客户与成交记录独立管理。商户只接收主动提交的核实内容。
            </p>
          </div>
        )}
      </main>
      {tab === "today" && (
        <div className="cp-paste-dock">
          <button onClick={() => setModal("deal")}>
            <b>粘贴客人消息</b>
            <span>从一句话开始，让搭档帮你整理 →</span>
          </button>
        </div>
      )}
      <nav className="cp-bottom-nav" aria-label="工作台导航">
        {[
          ["today", "今日"],
          ["deals", "跟单"],
          ["routes", "线路"],
          ["me", "我的"],
        ].map(([key, label]) => (
          <button
            key={key}
            aria-current={key === tab ? "page" : undefined}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      {modal && (
        <div className="cp-modal-backdrop">
          <section
            className="cp-modal"
            role="dialog"
            aria-modal="true"
            aria-label={modal === "deal" ? "新建跟单" : "添加客户"}
          >
            <div className="cp-section-head">
              <h2>{modal === "deal" ? "开始新跟单" : "添加客户"}</h2>
              <button onClick={() => setModal(null)} aria-label="关闭">
                关闭
              </button>
            </div>
            <form onSubmit={create}>
              {modal === "customer" ? (
                <>
                  <label>
                    客户称呼
                    <input name="name" required maxLength={100} autoFocus />
                  </label>
                  <label>
                    联系方式
                    <input name="contact" maxLength={200} />
                  </label>
                </>
              ) : (
                <>
                  <label>
                    客人或同行组称呼
                    <input
                      name="title"
                      placeholder="例如：ACME 一家"
                      required
                      maxLength={100}
                      autoFocus
                    />
                  </label>
                  <label>
                    关联客户
                    <select name="customer">
                      <option value="">暂不关联</option>
                      {customers.map((c) => (
                        <option value={c.id} key={c.id}>
                          {c.body.name}
                        </option>
                      ))}
                    </select>
                  </label>
                </>
              )}
              {error && (
                <p role="alert" className="cp-error">
                  {error}
                </p>
              )}
              <button className="cp-primary" disabled={busy}>
                {busy ? "正在保存…" : "创建并继续"}
              </button>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
