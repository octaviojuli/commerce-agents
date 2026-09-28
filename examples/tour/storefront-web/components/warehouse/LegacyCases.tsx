"use client";

import { useEffect, useRef, useState } from "react";
import { WarehouseClient, message } from "web-shared/warehouse-client";
import { cardStyle, inputStyle, stamp, type Page, type WarehouseProduct } from "@/lib/warehouse";

type Summary = { id: string; title: string; imported_at: string };
type PlanVersion = {
  version: number;
  parent_version: number | null;
  title: string;
  travel_dates: string | null;
  party: string | null;
  created_at: string;
  days: { label: string; note: string; request: boolean }[];
  diff: { kind: "same" | "changed" | "added" | "removed"; label: string; note: string }[];
  reference_price: {
    tong_ye_adult: number | null; market_adult: number | null;
    party_total: number | null; quote_source: string;
  } | null;
};
type Plan = { plan_id: string; route_id: number; route_name: string; versions: PlanVersion[] };
type Archive = {
  id: string;
  imported_at: string;
  body: {
    connection_id: string; created_at: string; updated_at: string;
    messages: { role: "user" | "assistant"; text: string }[];
    plans: Plan[];
  };
};

function ArchivedPlan({ archive, api, onCurrent }: {
  archive: Archive; api: WarehouseClient; onCurrent: (product: WarehouseProduct) => void;
}) {
  const [chosen, setChosen] = useState("");
  const plan = archive.body.plans.find(item => item.plan_id === chosen) ?? archive.body.plans[0];
  return <section className="space-y-4" aria-label="存档方案">
    <h3 className="text-lg font-semibold">存档方案</h3>
    {!plan ? <p className="text-sm text-(--ink-soft)">这份档案没有保存的方案。</p> : <>
      <label className="block text-sm">选择方案
        <select className={`${inputStyle} mt-1`} value={plan.plan_id}
          onChange={event => setChosen(event.target.value)}>
          {archive.body.plans.map(item => <option key={item.plan_id} value={item.plan_id}>{item.route_name} · {item.plan_id}</option>)}
        </select>
      </label>
      <PlanDetails key={plan.plan_id} archiveId={archive.id} connection={archive.body.connection_id}
        plan={plan} api={api} onCurrent={onCurrent}/>
    </>}
  </section>;
}

function PlanDetails({ archiveId, connection, plan, api, onCurrent }: {
  archiveId: string; connection: string; plan: Plan;
  api: WarehouseClient; onCurrent: (product: WarehouseProduct) => void;
}) {
  const [number, setNumber] = useState(plan.versions.at(-1)?.version ?? 1);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const version = plan.versions.find(item => item.version === number);
  async function current() {
    pending.current?.abort();
    const cancel = new AbortController();
    pending.current = cancel;
    setChecking(true); setError("");
    try {
      if (!Number.isSafeInteger(plan.route_id)) throw new Error("旧线路编号无法安全核对，请联系管理员。");
      // Revalidate the historical grant as well as the current catalog grant. A newly
      // issued grant must not make an already-open, revoked archive actionable.
      await api.get(`/advisor/legacy-cases/${archiveId}`, cancel.signal);
      const response = await api.response("/advisor/legacy-references/resolve", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: cancel.signal,
        body: JSON.stringify({ connection_id: connection, reference: `RT-${plan.route_id}` }),
      });
      const resolved: { warehouse_id: string } = await response.json();
      const product = await api.get<WarehouseProduct>(
        `/advisor/products/${encodeURIComponent(resolved.warehouse_id)}`, cancel.signal,
      );
      if (!cancel.signal.aborted) onCurrent(product);
    } catch (err) { if (!cancel.signal.aborted) setError(message(err)); }
    finally { if (!cancel.signal.aborted) setChecking(false); }
  }
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <button className="chip" onClick={current} disabled={checking}>{checking ? "正在核对线路…" : "核对当前线路"}</button>
      <p className="text-xs text-(--ink-soft)">重新读取当前可见线路，不沿用历史价格或库存。</p>
    </div>
    {error && <p role="alert" className="text-sm text-(--danger)">{error}</p>}
    {!version ? <p className="text-sm">原方案未保存版本内容。</p> : <>
      <label className="block text-sm">查看版本
        <select className={`${inputStyle} mt-1`} value={number} onChange={event => setNumber(Number(event.target.value))}>
          {plan.versions.map(item => <option key={item.version} value={item.version}>
            v{item.version} · {stamp(item.created_at)}
          </option>)}
        </select>
      </label>
      <div className="space-y-2">
        <h4 className="font-semibold">{version.title}</h4>
        <p className="text-sm text-(--ink-soft)">{version.parent_version === null ? "首个存档版本" : `基于 v${version.parent_version} 修改`} · {stamp(version.created_at)}（北京时间）</p>
        {(version.travel_dates || version.party) && <p className="text-sm">{version.travel_dates || "出行日期未记录"} · {version.party || "人数未记录"}</p>}
      </div>
      <ol className="space-y-3" aria-label="历史行程">
        {version.days.map((day, index) => <li key={index} className="rounded-xl border border-(--line) p-3">
          <p className="font-semibold">{day.label}</p><p className="mt-1 whitespace-pre-wrap text-sm">{day.note}</p>
          {day.request && <p className="mt-2 text-xs text-(--accent)">原记录：待计调确认</p>}
        </li>)}
      </ol>
      {version.parent_version !== null && <section className="space-y-2 rounded-xl bg-(--canvas) p-3" aria-label="版本差异">
        <h5 className="text-sm font-semibold">相对 v{version.parent_version} 的变化</h5>
        {!version.diff.some(item => item.kind !== "same") && <p className="text-sm">没有记录内容变化。</p>}
        {version.diff.filter(item => item.kind !== "same").map((item, index) => <p key={index} className="whitespace-pre-wrap text-sm">
          {{ changed: "修改", added: "新增", removed: "删除", same: "相同" }[item.kind]} · {item.label}：{item.note}
        </p>)}
      </section>}
      <section className="space-y-2 rounded-xl border border-(--line) p-3" aria-label="历史参考价格">
        <h5 className="text-sm font-semibold">历史参考价格 · 仅供内部回顾</h5>
        {version.reference_price ? <>
          <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
            <div><dt className="text-(--ink-soft)">成人市场参考价</dt><dd>{version.reference_price.market_adult ?? "未记录"}</dd></div>
            <div><dt className="text-(--ink-soft)">成人同业参考价</dt><dd>{version.reference_price.tong_ye_adult ?? "未记录"}</dd></div>
            <div><dt className="text-(--ink-soft)">团队参考总额</dt><dd>{version.reference_price.party_total ?? "未记录"}</dd></div>
          </dl>
          <p className="text-xs text-(--ink-soft)">旧记录来源：{version.reference_price.quote_source || "未记录"}。币种未记录，金额须重新核实。</p>
        </> : <p className="text-sm text-(--ink-soft)">此版本没有保存参考价格。</p>}
      </section>
    </>}
  </div>;
}

export default function LegacyCases({ api, onCurrent }: {
  api: WarehouseClient; onCurrent: (product: WarehouseProduct) => void;
}) {
  const [page, setPage] = useState<Page<Summary>>({ items: [], next_cursor: null });
  const [cursors, setCursors] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Archive | null>(null);
  const [listing, setListing] = useState(true);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const listHeading = useRef<HTMLHeadingElement>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const before = cursors.at(-1);
  useEffect(() => {
    const reread = () => { if (document.visibilityState === "visible") setRefresh(value => value + 1); };
    const timer = window.setInterval(reread, 30_000);
    window.addEventListener("focus", reread);
    document.addEventListener("visibilitychange", reread);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", reread); document.removeEventListener("visibilitychange", reread); };
  }, []);
  useEffect(() => {
    const cancel = new AbortController();
    setListing(true); setListError("");
    const params = new URLSearchParams({ limit: "25" });
    if (before) params.set("before", before);
    api.get<Page<Summary>>(`/advisor/legacy-cases?${params}`, cancel.signal)
      .then(result => { if (!cancel.signal.aborted) setPage(result); })
      .catch(err => { if (!cancel.signal.aborted) { setPage({ items: [], next_cursor: null }); setListError(message(err)); } })
      .finally(() => { if (!cancel.signal.aborted) setListing(false); });
    return () => cancel.abort();
  }, [api, before, refresh]);
  useEffect(() => {
    const cancel = new AbortController();
    setDetail(previous => previous?.id === selected ? previous : null);
    setDetailError(""); setLoading(Boolean(selected));
    if (selected) api.get<Archive>(`/advisor/legacy-cases/${selected}`, cancel.signal)
      .then(result => { if (!cancel.signal.aborted) setDetail(result); })
      .catch(err => { if (!cancel.signal.aborted) { setDetail(null); setDetailError(message(err)); } })
      .finally(() => { if (!cancel.signal.aborted) setLoading(false); });
    return () => cancel.abort();
  }, [api, selected, refresh]);
  const focusOnce = useRef("");
  useEffect(() => {
    if (detail?.id === selected && focusOnce.current !== selected) {
      focusOnce.current = selected;
      detailHeading.current?.focus({ preventScroll: true });
      detailHeading.current?.scrollIntoView({ block: "start" });
    }
  }, [detail, selected]);
  function navigate(next: string[]) { setCursors(next); setSelected(""); setDetail(null); setDetailError(""); }
  const active = detail?.id === selected ? detail : null;
  return <div className="panel-scroll h-full overflow-y-auto"><div className="mx-auto max-w-6xl space-y-5 px-4 py-6 [overflow-wrap:anywhere]">
    <header><h1 className="text-2xl font-semibold">历史档案</h1>
      <p className="mt-2 text-sm text-(--ink-soft)">查看已迁入当前工作空间、归属于你的旧会话与方案。历史内容仅供回顾，使用前请重新核实。</p>
      <p className="mt-2 text-sm text-(--ink-soft)">此处仅供内部查看。档案不会自动加入助手会话，也不会恢复旧分享链接。</p></header>
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <section className="min-w-0 space-y-3" aria-label="历史档案列表">
        <div className="flex items-center justify-between gap-2"><h2 ref={listHeading} tabIndex={-1} className="font-semibold">我的旧案例</h2>
          <button className="chip" disabled={listing || loading} onClick={() => { navigate([]); setRefresh(value => value + 1); }}>刷新档案</button></div>
        {listError && <p role="alert" className="text-sm text-(--danger)">{listError}</p>}
        <div className="min-h-5">{listing && <p role="status" className="text-sm">正在读取档案列表…</p>}</div>
        <div className={`space-y-3 ${listing ? "invisible" : ""}`} aria-hidden={listing}>
          {!page.items.length && !listError && <p className={`${cardStyle} text-sm text-(--ink-soft)`}>当前没有可读取的历史档案。尚未迁入或授权已变化的案例不会显示。</p>}
          {page.items.map(item => <button key={item.id} className={`${cardStyle} block w-full text-left ${selected === item.id ? "ring-2 ring-(--accent)" : ""}`}
            aria-pressed={selected === item.id} onClick={() => {
              focusOnce.current = "";
              if (selected === item.id) setRefresh(value => value + 1);
              else setSelected(item.id);
            }}>
            <span className="block text-sm font-semibold">{item.title}</span><span className="mt-1 block text-xs text-(--ink-soft)">{stamp(item.imported_at)} 迁入</span>
          </button>)}
        </div>
        <nav aria-label="历史档案分页" className="flex items-center justify-between gap-2 text-sm">
          <button className="chip" disabled={listing || !cursors.length} onClick={() => navigate(cursors.slice(0, -1))}>上一页档案</button>
          <span>第 {cursors.length + 1} 页</span>
          <button className="chip" disabled={listing || !page.next_cursor} onClick={() => navigate([...cursors, page.next_cursor!])}>下一页档案</button>
        </nav>
      </section>
      <section className="min-w-0 space-y-4" aria-label="历史档案详情">
        <h2 ref={detailHeading} tabIndex={-1} className="font-semibold">案例内容</h2>
        <div className="min-h-5">{loading && <p role="status" className="text-sm">正在读取历史案例…</p>}</div>
        {detailError && <p role="alert" className="text-sm text-(--danger)">{detailError}</p>}
        {!selected && <p className="text-sm text-(--ink-soft)">选择一个旧案例，查看原对话与方案版本。</p>}
        {active && <div aria-hidden={loading} className={`${cardStyle} space-y-6 ${loading ? "invisible" : ""}`}>
          <p className="font-semibold">{page.items.find(item => item.id === active.id)?.title || "历史案例"}</p>
          <p className="text-xs text-(--ink-soft)">原会话创建于 {stamp(active.body.created_at)}，更新于 {stamp(active.body.updated_at)}（北京时间）。</p>
          <ArchivedPlan key={active.id} archive={active} api={api} onCurrent={onCurrent}/>
          <section className="space-y-3" aria-label="原会话文本"><h3 className="text-lg font-semibold">原会话文本</h3>
            {!active.body.messages.length && <p className="text-sm text-(--ink-soft)">原记录没有可展示的对话文本。</p>}
            {active.body.messages.map((item, index) => <article key={index} className="rounded-xl border border-(--line) p-3">
              <p className="text-xs font-semibold text-(--accent)">{item.role === "user" ? "顾问" : "助手"}</p>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">{item.text}</p>
            </article>)}
          </section>
          <button className="chip" onClick={() => {
            listHeading.current?.focus({ preventScroll: true }); listHeading.current?.scrollIntoView({ block: "start" });
          }}>返回档案列表</button>
        </div>}
      </section>
    </div>
  </div></div>;
}
