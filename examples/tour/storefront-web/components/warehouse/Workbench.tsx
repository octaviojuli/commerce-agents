"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Chat, useAgentTurn } from "web-shared";
import {
  WarehouseClient,
  message,
  type Organization,
} from "web-shared/warehouse-client";
import { BRAND } from "@/lib/brand";
import {
  cardStyle,
  inputStyle,
  WarehouseAgentApi,
  type Quote,
  type BriefEnvelope,
  type WorkbenchAction,
} from "@/lib/warehouse";
import dynamic from "next/dynamic";
import ResultCards from "./ResultCards";
import RoutePreview from "./RoutePreview";
import OfferCards from "./OfferCards";
const CatalogSearch = dynamic(() => import("./CatalogSearch"), {
  loading: () => <p role="status">正在加载目录…</p>,
});
import { loginToken, copyText, Sheet, BriefAside, useViewport } from "./mobile";
import { QuoteCard } from "./Quote";
import { useWarehouseHistory } from "./History";
const ShareHistory = dynamic(() => import("./ShareHistory"), {
  loading: () => <p role="status">正在加载分享记录…</p>,
});
const LegacyCases = dynamic(() => import("./LegacyCases"), {
  loading: () => <p role="status">正在加载历史档案…</p>,
});
import BriefPanel, { fieldNames, fieldText } from "./BriefPanel";
import "./workbench.css";
import Copilot from "./Copilot";

export default function WarehouseWorkbench() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState("");
  const clear = useCallback(() => {
    loginToken(null);
    setToken(null);
  }, []);
  useEffect(() => {
    setToken(loginToken());
    setReady(true);
    const expired = () => {
      clear();
      setNotice("登录已失效，请重新登录。");
    };
    window.addEventListener("warehouse-expired", expired);
    return () => window.removeEventListener("warehouse-expired", expired);
  }, [clear]);
  async function logout() {
    const previous = token;
    clear(); // Clear rendered data immediately even if the network is unavailable.
    try {
      await new WarehouseClient(previous ?? "").post("/auth/logout");
    } catch {
      setNotice("本地登录已退出，服务暂未确认注销。请关闭共用设备上的窗口。");
    }
  }
  if (!ready)
    return (
      <main className="p-6" role="status">
        正在恢复登录…
      </main>
    );
  if (token) return <Organizations key={token} token={token} logout={logout} />;
  return (
    <Login
      notice={notice}
      login={(value) => {
        loginToken(value);
        setToken(value);
        setNotice("");
      }}
    />
  );
}

function Login({
  notice,
  login,
}: {
  notice: string;
  login: (token: string) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await new WarehouseClient("").post<{
        access_token: string;
      }>("/auth/login", {
        email: data.get("email"),
        password: data.get("password"),
      });
      login(result.access_token);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="flex min-h-dvh items-center justify-center p-5">
      <section className="w-full max-w-[420px]">
        <p className="text-sm font-semibold text-(--accent)">
          ◆ {BRAND} · 云仓顾问
        </p>
        <h1 className="mt-3 text-3xl font-semibold leading-tight">
          把选团的依据，
          <br />
          放在同一张工作台。
        </h1>
        <p className="mb-6 mt-3 text-sm text-(--ink-soft)">
          查询上线线路、团期库存与同行结算价。
        </p>
        <form className={`${cardStyle} space-y-4`} onSubmit={submit}>
          <h2 className="font-semibold">使用云仓平台账号登录</h2>
          <label className="block text-sm">
            账号邮箱
            <input
              className={`${inputStyle} mt-1`}
              type="email"
              name="email"
              autoComplete="username"
              required
              disabled={busy}
            />
          </label>
          <label className="block text-sm">
            密码
            <input
              className={`${inputStyle} mt-1`}
              type="password"
              name="password"
              autoComplete="current-password"
              required
              disabled={busy}
            />
          </label>
          {(error || notice) && (
            <p role="alert" className="text-sm text-(--danger)">
              {error || notice}
            </p>
          )}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? "正在登录…" : "进入选团工作台"}
          </button>
          <p className="text-xs text-(--ink-soft)">
            账号由平台管理员开通，独立顾问也可使用，无需绑定旅行社。
          </p>
        </form>
      </section>
    </main>
  );
}

function Organizations({
  token,
  logout,
}: {
  token: string;
  logout: () => void;
}) {
  const api = useMemo(() => new WarehouseClient(token), [token]);
  const [organizations, setOrganizations] = useState<Organization[] | null>(
    null,
  );
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const cancel = new AbortController();
    api
      .get<{ items: Organization[] }>("/me/organizations", cancel.signal)
      .then((result) => {
        if (!cancel.signal.aborted)
          setOrganizations(
            result.items.filter((org) =>
              org.roles.some((role) =>
                ["advisor", "buyer_admin"].includes(role),
              ),
            ),
          );
      })
      .catch((err) => {
        if (!cancel.signal.aborted) setError(message(err));
      });
    return () => cancel.abort();
  }, [api]);
  const current =
    organizations?.find((org) => org.id === selected) ?? organizations?.[0];
  if (!current)
    return (
      <main className="mx-auto max-w-lg space-y-4 p-6">
        <h1 className="text-xl font-semibold">云仓顾问工作台</h1>
        <p role={error ? "alert" : "status"}>
          {error ||
            (organizations
              ? "当前账号没有销售顾问权限，请联系管理员。"
              : "正在读取顾问工作空间…")}
        </p>
        <button className="chip" onClick={logout}>
          退出登录
        </button>
      </main>
    );
  return (
    <Copilot
      key={current.id}
      token={token}
      organization={current}
      organizations={organizations!}
      select={setSelected}
      logout={logout}
    />
  );
}

function Workspace({
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
  const agent = useMemo(() => new WarehouseAgentApi(api), [api]);
  const liveChat = useAgentTurn(agent, {
    sessionId: organization.id,
    unreachable: "云仓服务暂不可用，请稍后重试。",
  });
  const history = useWarehouseHistory(agent, liveChat);
  const chat = history.chat;
  const [brief, setBrief] = useState<BriefEnvelope | null>(null),
    [error, setError] = useState("");
  const [working, setWorking] = useState(false),
    [input, setInput] = useState("");
  const [mobile, setMobile] = useState("chat"),
    [drawer, setDrawer] = useState<"catalog" | "archives" | "shares" | null>(
      null,
    );
  const [sessions, setSessions] = useState(false),
    [link, setLink] = useState("");
  const [note, setNote] = useState(""),
    [copied, setCopied] = useState(false),
    [catalogQuery, setCatalogQuery] = useState("");
  const noteDraft = useRef({ conversation: "", dirty: false });
  const busy = chat.busy || working;
  useViewport();
  const [manualCopy, setManualCopy] = useState("");
  const [shareCopied, setShareCopied] = useState(false);
  const [sessionQuery, setSessionQuery] = useState("");
  useEffect(() => () => agent.dispose(), [agent]);
  const currentId = agent.conversation;
  const briefConversation = useRef<string | null>(null);
  useEffect(() => {
    const cancel = new AbortController();
    if (agent.shareToken) {
      setLink(`${location.origin}/quote#${agent.shareToken}`);
      agent.shareToken = "";
    }
    if (briefConversation.current !== currentId) {
      briefConversation.current = currentId;
      setBrief(null);
    }
    if (currentId)
      api
        .get<BriefEnvelope>(`/conversations/${currentId}/brief`, cancel.signal)
        .then((b) => {
          if (!cancel.signal.aborted) {
            setBrief(b);
            if (noteDraft.current.conversation !== currentId) {
              noteDraft.current = { conversation: currentId, dirty: false };
            }
            if (!noteDraft.current.dirty) setNote(b.body.offline_hold_note);
          }
        })
        .catch((e) => {
          if (!cancel.signal.aborted) setError(message(e));
        });
    return () => cancel.abort();
  }, [api, currentId, liveChat.completed, history.loading, history.revision]);
  async function ensure() {
    if (!agent.conversation) {
      const c = await api.post<{ id: string }>("/conversations", {
        role: "advisor",
      });
      agent.conversation = c.id;
      history.refresh();
    }
    return api.get<BriefEnvelope>(`/conversations/${agent.conversation}/brief`);
  }
  async function save(
    fields: Record<string, unknown>,
    expectedVersion?: number,
  ) {
    setWorking(true);
    setError("");
    try {
      const b = brief ?? (await ensure());
      const r = await api.response(
        `/conversations/${agent.conversation}/brief`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_version: expectedVersion ?? b.version,
            fields,
          }),
        },
      );
      setBrief(await r.json());
      setLink("");
      history.refresh();
      return true;
    } catch (e) {
      setError(message(e));
      if (agent.conversation)
        setBrief(await api.get(`/conversations/${agent.conversation}/brief`));
      return false;
    } finally {
      setWorking(false);
    }
  }
  async function act(command: WorkbenchAction, inline = false) {
    if (busy) return;
    setWorking(true);
    setError("");
    try {
      const b = brief ?? (await ensure());
      const result = await api.post<{
        brief: BriefEnvelope;
        payload: Record<string, any>;
      }>(`/conversations/${agent.conversation}/actions`, {
        ...command,
        expected_version: b.version,
        request_id: crypto.randomUUID(),
      });
      if (!inline) await history.load(agent.conversation);
      if (!inline)
        liveChat.setItems((items) =>
          items.map((item, i) =>
            item.kind === "assistant" && i === items.length - 1
              ? { ...item, turn: 0 }
              : item,
          ),
        );
      setBrief(result.brief);
      if (command.action === "offline_hold_recorded") {
        noteDraft.current.dirty = false;
        setNote(result.brief.body.offline_hold_note);
      }
      if (!inline) {
        setDrawer(null);
        setMobile("chat");
      }
      if (command.action === "quote" || command.action === "departures")
        setLink("");
      if (result.payload?.token) {
        const url = `${location.origin}/quote#${result.payload.token}`;
        setLink(url);
        setMobile("brief");
        const text = `您好，这是本次旅行方案与报价，价格和余位以再次核实为准：\n${url}`;
        const ok = await copyText(text);
        setShareCopied(ok);
        if (!ok) setManualCopy(text);
      }
      if (
        command.action === "customer_confirmed" ||
        command.action === "offline_hold_recorded"
      )
        setMobile("brief");
      history.refresh();
      return result.payload;
    } catch (e) {
      setError(message(e));
      if (agent.conversation)
        setBrief(await api.get(`/conversations/${agent.conversation}/brief`));
    } finally {
      setWorking(false);
    }
  }
  async function send(e: FormEvent) {
    e.preventDefault();
    if (!input.trim() || busy) return;
    const text = input.trim();
    setInput("");
    setError("");
    await chat.send(text);
  }
  function reset() {
    history.reset();
    setBrief(null);
    setError("");
    setLink("");
    setInput("");
    noteDraft.current = { conversation: "", dirty: false };
    setNote("");
    setSessions(false);
    setDrawer(null);
    setMobile("chat");
  }
  const b = brief?.body;
  const quote = b?.quote;
  useEffect(() => {
    if (!quote || brief?.quote_stale) return;
    const delay = new Date(quote.fresh_until).getTime() - Date.now();
    if (!Number.isFinite(delay)) return;
    const timer = setTimeout(
      () =>
        setBrief((old) =>
          old
            ? {
                ...old,
                quote_stale: true,
                allowed_actions: old.allowed_actions.filter(
                  (a) =>
                    ![
                      "share_quote",
                      "customer_confirmed",
                      "offline_hold_recorded",
                    ].includes(a),
                ),
              }
            : old,
        ),
      Math.max(0, delay),
    );
    return () => clearTimeout(timer);
  }, [quote, brief?.quote_stale]);
  const steps = [
    ["need", "需求"],
    ["select", "选线"],
    ["departure", "团期"],
    ["quote", "报价"],
    ["shared", "发给客人"],
  ];
  const index = steps.findIndex((s) => s[0] === brief?.stage);
  const allowed = (action: string) => !!brief?.allowed_actions.includes(action);
  async function copy() {
    if (!quote) return;
    try {
      const text = [
        `线路：${quote.product_name ?? "待确认"}`,
        `团号：${quote.departure_code ?? "待确认"}`,
        `出发日期：${quote.departure_date}`,
        `人数：${quote.party.adults}成人 / ${quote.party.children}儿童 / ${quote.party.seniors}老人`,
        `儿童年龄：${quote.party.child_ages.join("、") || "无"}`,
        `房型：${fieldText("rooms", (quote as any).rooms ?? quote.party.rooms)}`,
        `报价有效期：${quote.fresh_until}`,
        "请线下核实价格、余位和占位结果；系统未占位。",
      ].join("\n");
      const ok = await copyText(text);
      setCopied(ok);
      if (!ok) setManualCopy(text);
    } catch {
      setError("复制失败，请手动复制卡片中的信息。");
    }
  }
  return (
    <main className={`aw ${sessions ? "aw-session-page" : ""}`}>
      <header className="aw-header">
        <strong>
          ◆ {BRAND}
          <span> · 云仓顾问</span>
        </strong>
        <div>
          {organizations.length > 1 && (
            <select
              aria-label="工作空间"
              value={organization.id}
              onChange={(e) => select(e.target.value)}
            >
              {organizations.map((org) => (
                <option key={org.id} value={org.id}>
                  {org.name}
                </option>
              ))}
            </select>
          )}
          <span className="aw-header-note">一位客人，一张需求单</span>
          <button onClick={logout}>退出登录</button>
        </div>
      </header>
      <nav className="aw-stages" aria-label="销售阶段">
        {steps.map(([id, label], i) => (
          <span
            key={id}
            aria-current={i === (index < 0 ? 0 : index) ? "step" : undefined}
            className={
              i === (index < 0 ? 0 : index)
                ? "current"
                : i < index
                  ? "done"
                  : ""
            }
          >
            <b>{i < index ? "✓" : i + 1}</b>
            {label}
            <em>›</em>
          </span>
        ))}
        <span className="aw-offline-step">⑥ 占位（一期线下）</span>
      </nav>
      <div className="aw-mobile-top">
        <button
          onClick={() => setSessions((v) => !v)}
          aria-label={sessions ? "返回对话" : "返回会话列表"}
        >
          {sessions ? "返回" : "‹ 会话"}
        </button>
        <strong>{sessions ? "客人会话" : (brief?.title ?? "新客人")}</strong>
        <details>
          <summary aria-label="更多功能">•••</summary>
          <div>
            <button onClick={() => setDrawer("catalog")}>浏览目录</button>
            <button onClick={() => setDrawer("archives")}>历史档案</button>
            <button onClick={logout}>退出登录</button>
          </div>
        </details>
      </div>
      {!sessions && (
        <button
          className={`aw-compact-brief ${brief?.quote_stale ? "aw-summary-stale" : ""}`}
          onClick={() => setMobile("brief")}
        >
          <span className="aw-summary-tags">
            <i>
              {b?.party_total?.value ? `${b.party_total.value} 人` : "人数待补"}
            </i>
            <i>{b?.destinations?.value?.join("、") || "目的地待补"}</i>
            <i>
              {b?.window?.value
                ? `${b.window.value.start}—${b.window.value.end}`
                : "时间待补"}
            </i>
            {b?.days?.value && (
              <i>
                {b.days.value.min}–{b.days.value.max} 天
              </i>
            )}
            {b?.depart_city?.value && <i>{b.depart_city.value}</i>}
          </span>
          <b
            className={brief?.readiness.quote.inferred.length ? "aw-warn" : ""}
          >
            {brief?.quote_stale
              ? "报价失效"
              : brief?.readiness.quote.ready
                ? `可询价${brief.readiness.quote.inferred.length ? "·" + brief.readiness.quote.inferred.map((k) => fieldNames[k] ?? k).join("、") + "待确认" : ""}`
                : brief?.readiness.search.ready
                  ? "待补：" +
                    brief.readiness.quote.missing
                      .map((k) => fieldNames[k] ?? k)
                      .slice(0, 2)
                      .join("、")
                  : "待补需求"}{" "}
            ⌃
          </b>
        </button>
      )}
      <div className="aw-layout">
        <aside
          className={`aw-sessions ${sessions ? "visible" : ""}`}
          aria-label="客人会话"
        >
          <div className="aw-section-head">
            <h2>客人会话</h2>
            <button aria-label="刷新会话" onClick={history.refresh}>
              ↻
            </button>
          </div>
          <label className="aw-session-search">
            搜索客人会话
            <input
              value={sessionQuery}
              maxLength={120}
              placeholder="目的地或时间"
              onChange={(e) => {
                setSessionQuery(e.target.value);
                history.search(e.target.value);
              }}
            />
          </label>
          <button className="aw-new" disabled={busy} onClick={reset}>
            ＋ 新客人
          </button>
          {!currentId && (
            <div className="aw-session selected">
              <b>新客人</b>
              <small>刚开始</small>
            </div>
          )}
          {history.listing && (
            <div
              className="aw-skeleton"
              role="status"
              aria-label="正在加载会话"
            />
          )}
          {history.page.items.map((row) => (
            <button
              className={`aw-session ${currentId === row.id ? "selected" : ""}`}
              key={row.id}
              disabled={busy || !row.resumable}
              onClick={() => {
                setBrief(null);
                setLink("");
                setSessions(false);
                void history.load(row.id);
              }}
            >
              <b>{row.resumable ? row.title || "新客人需求" : "授权已变化"}</b>
              <small>
                {row.offline_status === "offline_hold_recorded"
                  ? "已记录线下占位"
                  : row.offline_status === "customer_confirmed"
                    ? "客人已确认 · 待线下占位"
                    : row.shared
                      ? "已发给客人 · 待线下占位"
                      : ({
                          need: "待补需求",
                          select: "选线中",
                          departure: "团期已选",
                          quote: "已询价",
                          shared: "已分享",
                        }[row.stage ?? "need"] ?? "继续跟进")}
              </small>
            </button>
          ))}
          {history.page.next_cursor && (
            <button
              disabled={busy || history.listing}
              onClick={history.moreConversations}
            >
              更多会话
            </button>
          )}
          <div className="aw-session-bottom">
            <button onClick={() => setDrawer("archives")}>历史档案 ↗</button>
            <p>历史内容仅供回顾，价格与余位请重新核实。</p>
          </div>
        </aside>
        <section
          className={`aw-chat ${mobile === "chat" ? "mobile-active" : ""}`}
          aria-label="对话"
        >
          <div className="aw-chat-toolbar">
            <span>{brief?.title ?? "新客人"}</span>
            <div>
              {history.cursor && (
                <button
                  disabled={busy}
                  onClick={() => history.load(currentId, true)}
                >
                  更早对话
                </button>
              )}
              <button
                className="aw-tablet-sessions"
                onClick={() => setSessions((v) => !v)}
              >
                客人会话
              </button>
              <button disabled={busy} onClick={() => setDrawer("catalog")}>
                浏览目录
              </button>
            </div>
          </div>
          {(error || history.error) && (
            <p role="alert" className="aw-error">
              {error || history.error}
            </p>
          )}
          {history.remoteBusy && (
            <p className="aw-brief-event" role="status">
              {history.recovering
                ? "正在恢复回答…"
                : "服务端仍在处理，可稍后刷新查看"}
              <button onClick={() => history.recover()}>刷新状态</button>
            </p>
          )}
          {brief?.quote_stale && (
            <p className="aw-warning" role="status">
              需求已变化或报价已过期，请重新询价。
              <button
                disabled={busy || !allowed("quote")}
                onClick={() =>
                  void act({
                    action: "quote",
                    product_id: b?.departure_id ?? undefined,
                    offer_id: b?.offer_id ?? undefined,
                  })
                }
              >
                重新询价
              </button>
            </p>
          )}
          <div className="aw-transcript">
            {working && (
              <div
                className="aw-skeleton"
                role="status"
                aria-label="正在查询"
              />
            )}
            <Chat
              chat={{
                ...chat,
                items: chat.items.map((item) =>
                  item.kind === "assistant"
                    ? { ...item, suggestions: [] }
                    : item,
                ),
              }}
              home={
                <div className="aw-home">
                  <p>云仓顾问助手</p>
                  <h1>客人想去哪里？</h1>
                  <div>
                    把客人的原话发给我。我会整理成需求单，缺什么再问你；目的地和时间有了就开始找线路。
                  </div>
                  <button onClick={() => setMobile("brief")}>
                    也可以先填写需求单 →
                  </button>
                </div>
              }
              renderBlock={({ block, status }, item) => {
                if (status !== "final") return null;
                const payload = block.payload as Record<string, any>;
                const historical = item.turn < 0;
                if (block.component === "warehouse_brief")
                  return (
                    <>
                      <p className="aw-brief-event">
                        ✓ 需求单已更新 · 推断项请在需求单中确认
                      </p>
                      {payload.conflicts?.map((c: any) => (
                        <ConflictCard
                          key={`${payload.version}-${c.field}`}
                          conflict={c}
                          version={payload.version}
                          currentVersion={brief?.version}
                          busy={busy}
                          save={save}
                        />
                      ))}
                    </>
                  );
                if (block.component === "warehouse_quote")
                  return (
                    <>
                      <QuoteCard
                        quote={payload as Quote}
                        historical={
                          Boolean(
                            brief &&
                              (payload.quote_brief_version ?? 0) <
                                brief.body.quote_fields_version,
                          ) || payload.quote_id !== b?.quote_id
                        }
                      />
                      {payload.inferred?.length > 0 && (
                        <p className="aw-warning">
                          推断·待确认：
                          {payload.inferred
                            .map((k: string) => fieldNames[k] ?? k)
                            .join("、")}
                        </p>
                      )}
                      {allowed("share_quote") &&
                        payload.quote_id === b?.quote_id && (
                          <button
                            className="aw-primary"
                            disabled={busy || brief?.quote_stale}
                            onClick={() => void act({ action: "share_quote" })}
                          >
                            复制发给客人
                          </button>
                        )}
                    </>
                  );
                if (block.component === "warehouse_share")
                  return (
                    <p className="aw-brief-event">
                      ✓ 已生成对客分享 · 展开需求单查看链接与线下跟进
                    </p>
                  );
                if (block.component === "warehouse_offline")
                  return (
                    <p className="aw-brief-event">
                      ✓{" "}
                      {payload.status === "customer_confirmed"
                        ? "客人已确认"
                        : "线下占位备注已记录"}{" "}
                      · 系统未占位
                    </p>
                  );
                if (block.component === "warehouse_offers")
                  return (
                    <OfferCards
                      key={`${item.turn}-${block.component}`}
                      payload={payload}
                      busy={busy}
                      act={act}
                      more={(c) => act(c, true)}
                    />
                  );
                const items =
                  block.component === "products"
                    ? (payload.items ?? []).map((r: any) => r.product)
                    : payload.items;
                if(block.component === "warehouse_itinerary")return <div className="aw-result"><p>{payload.message ?? "已读取该线路的已发布行程内容。"}</p>{payload.product_id&&<RoutePreview api={api} productId={payload.product_id}/>}</div>;
                if (items)
                  return (
                    <ResultCards
                      api={api}
                      key={`${item.turn}-${block.component}`}
                      payload={{ ...payload, items }}
                      departures={block.component === "warehouse_departures"}
                      historical={historical}
                      busy={busy}
                      act={act}
                      more={(command) => act(command, true)}
                    />
                  );
                return null;
              }}
            />
          </div>
          {!busy && (
            <div className="aw-suggestions" aria-label="建议回复">
              {[...chat.items].reverse().find((i) => i.kind === "assistant")
                ?.kind === "assistant" &&
                (() => {
                  const item = [...chat.items]
                    .reverse()
                    .find((i) => i.kind === "assistant");
                  return item?.kind === "assistant"
                    ? item.suggestions.map((t) => (
                        <button key={t} onClick={() => void chat.send(t)}>
                          {t}
                        </button>
                      ))
                    : null;
                })()}
            </div>
          )}
          <form className="aw-composer" onSubmit={send}>
            <label className="sr-only" htmlFor="advisor-message">
              客人需求
            </label>
            <textarea
              id="advisor-message"
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 144)}px`;
              }}
              placeholder="描述目的地、出发时间与人数…"
              disabled={busy}
              rows={2}
              onKeyDown={(e) => {
                if (
                  !matchMedia("(max-width:767px)").matches &&
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <button className="aw-primary" disabled={busy || !input.trim()}>
              {busy ? "处理中…" : "发送"}
            </button>
          </form>
        </section>
        <BriefAside open={mobile === "brief"} close={() => setMobile("chat")}>
          {currentId && !brief && (
            <div
              className="aw-skeleton"
              role="status"
              aria-label="正在读取需求单"
            />
          )}
          <BriefPanel brief={brief} busy={busy} save={save} />
          <section className="aw-selection">
            <div className="aw-section-head">
              <h2>当前选择</h2>
            </div>
            <p>线路：{quote?.product_name ?? b?.route_title ?? "尚未选择"}</p>
            <p>
              团期：{quote?.departure_date ?? b?.departure_title ?? "尚未选择"}
            </p>
            <p>
              报价：
              {quote
                ? brief?.quote_stale
                  ? "失效，需要重新询价"
                  : `市场价 ${quote.currency ?? ""} ${quote.market_total ?? "待确认"}`
                : "尚未询价"}
            </p>
            {brief?.quote_stale && (
              <p className="aw-warning">
                需求已变化，已有报价失效，需要重新询价。
              </p>
            )}
            {allowed("search_routes") && (
              <button
                disabled={busy}
                onClick={() => void act({ action: "search_routes" })}
              >
                按需求找线路
              </button>
            )}
            {allowed("share_quote") && (
              <button
                className="aw-primary"
                disabled={busy}
                onClick={() => void act({ action: "share_quote" })}
              >
                复制发给客人
              </button>
            )}
            {link && (
              <div className="aw-link">
                <label>
                  对客链接（请自行发送）
                  <input
                    aria-label="对客报价链接"
                    readOnly
                    value={link}
                    onFocus={(e) => e.target.select()}
                  />
                </label>
                <button
                  onClick={async () => {
                    const text = `您好，这是本次旅行方案与报价，价格和余位以再次核实为准：\n${link}`;
                    const ok = await copyText(text);
                    setShareCopied(ok);
                    if (!ok) setManualCopy(text);
                  }}
                >
                  {shareCopied ? "已复制，可粘贴到微信" : "复制发给客人"}
                </button>
                <button
                  onClick={async () => {
                    try {
                      if (navigator.share)
                        await navigator.share({
                          title: "旅行方案与报价",
                          text: "请查看旅行方案，价格与余位以再次核实为准。",
                          url: link,
                        });
                      else if (!(await copyText(link))) setManualCopy(link);
                    } catch {}
                  }}
                >
                  系统分享
                </button>
                <a href={link} target="_blank" rel="noreferrer">
                  打开客户预览 ↗
                </a>
              </div>
            )}
            {b?.quote_id && (
              <button onClick={() => setDrawer("shares")}>管理分享记录</button>
            )}
          </section>
          {b?.share_token && quote && !brief?.quote_stale && (
            <section className="aw-offline">
              <span className="aw-eyebrow">下一步 · 线下跟进</span>
              <h2>线下占位</h2>
              <p>将团期和客人需求交给计调。这里记录跟进状态，系统未占位。</p>
              <button disabled={busy} onClick={copy}>
                {copied ? "已复制 ✓" : "复制给计调"}
              </button>
              <button
                disabled={busy}
                onClick={() => void act({ action: "customer_confirmed" })}
              >
                {b.offline_status !== "none"
                  ? "✓ 客人已确认"
                  : "标记客人已确认"}
              </button>
              <label>
                线下占位备注
                <textarea
                  value={note}
                  onChange={(e) => {
                    noteDraft.current.dirty = true;
                    setNote(e.target.value);
                  }}
                  rows={3}
                  maxLength={2000}
                  placeholder="记录计调反馈、线下占位编号等"
                />
              </label>
              <button
                className="aw-primary"
                disabled={busy || !note.trim()}
                onClick={() =>
                  void act({ action: "offline_hold_recorded", note })
                }
              >
                保存线下备注
              </button>
            </section>
          )}
        </BriefAside>
      </div>
      {manualCopy && (
        <Sheet title="请长按复制" close={() => setManualCopy("")}>
          <p>自动复制不可用，请长按下方文字复制。</p>
          <textarea
            className="aw-manual-copy"
            readOnly
            value={manualCopy}
            onFocus={(e) => e.target.select()}
          />
        </Sheet>
      )}
      {drawer && (
        <Sheet
          title={
            drawer === "catalog"
              ? "浏览目录"
              : drawer === "archives"
                ? "历史档案"
                : "分享记录"
          }
          close={() => setDrawer(null)}
          wide
        >
          {drawer === "archives" ? (
            <LegacyCases
              api={api}
              onCurrent={(product) => {
                setCatalogQuery(product.title);
                setDrawer("catalog");
              }}
            />
          ) : drawer === "shares" ? (
            <ShareHistory api={api} />
          ) : (
            <CatalogSearch
              value={catalogQuery}
              change={setCatalogQuery}
              busy={busy}
              ready={allowed("search_routes")}
              submit={() =>
                void act({
                  action: "search_routes",
                  ...(catalogQuery ? { query: catalogQuery } : {}),
                })
              }
            />
          )}
        </Sheet>
      )}
    </main>
  );
}

function ConflictCard({
  conflict,
  version,
  currentVersion,
  busy,
  save,
}: {
  conflict: any;
  version: number;
  currentVersion?: number;
  busy: boolean;
  save: (fields: Record<string, unknown>, version?: number) => Promise<boolean>;
}) {
  const [resolved, setResolved] = useState(false);
  if (resolved) return <p className="aw-brief-event">需求冲突已处理</p>;
  return (
    <section className="aw-warning">
      <b>{fieldNames[conflict.field] ?? conflict.field}有不同解释</b>
      <p>顾问已确认：{fieldText(conflict.field, conflict.current)}</p>
      <p>模型新建议：{fieldText(conflict.field, conflict.proposed)}</p>
      <button onClick={() => setResolved(true)}>保持我的修改</button>
      <button
        disabled={busy || currentVersion !== version}
        onClick={async () => {
          if (await save({ [conflict.field]: conflict.proposed }, version))
            setResolved(true);
        }}
      >
        采用新建议
      </button>
      {currentVersion !== version && (
        <p>需求已再次更新，请在需求单中核对后修改。</p>
      )}
    </section>
  );
}
