"use client";

import { useEffect, useState } from "react";
import { WarehouseClient, message } from "web-shared/warehouse-client";
import { cardStyle, inputStyle, stamp, type Page } from "@/lib/warehouse";

type ShareRecord = {
  id: string;
  quote_id: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  product_name: string | null;
  departure_date: string | null;
  quote_readable: boolean;
};

export function ShareRecords({ api, quoteId, revision = 0, onRevoke }: {
  api: WarehouseClient;
  quoteId?: string;
  revision?: number;
  onRevoke?: (id: string) => void;
}) {
  const [page, setPage] = useState<Page<ShareRecord>>({ items: [], next_cursor: null });
  const [cursors, setCursors] = useState<string[]>([]);
  const [filter, setFilter] = useState("all");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);
  const before = cursors.at(-1);
  useEffect(() => { setCursors([]); setConfirm(null); }, [revision]);
  useEffect(() => {
    const cancel = new AbortController();
    const params = new URLSearchParams({ limit: "25", status: filter });
    if (quoteId) params.set("quote_id", quoteId);
    if (before) params.set("before", before);
    setLoading(true); setError(""); setConfirm(null);
    api.get<Page<ShareRecord>>(`/quote-shares?${params}`, cancel.signal)
      .then(result => { if (!cancel.signal.aborted) setPage(result); })
      .catch(err => {
        if (!cancel.signal.aborted) {
          setPage({ items: [], next_cursor: null }); setError(message(err));
        }
      })
      .finally(() => { if (!cancel.signal.aborted) setLoading(false); });
    return () => cancel.abort();
  }, [api, quoteId, before, filter, refresh, revision]);
  async function revoke(id: string) {
    setSaving(true); setError(""); setNotice("");
    try {
      await api.post(`/quote-shares/${id}/revoke`, {});
      onRevoke?.(id);
      setConfirm(null); setCursors([]); setRefresh(value => value + 1);
      setNotice("链接已撤销，后续访问将被拒绝。");
    } catch (err) { setError(message(err)); }
    finally { setSaving(false); }
  }
  const busy = loading || saving;
  return <section className="space-y-3" aria-label="分享记录">
    <div className="flex flex-wrap items-end gap-2">
      <label className="text-sm">链接状态<select className={`${inputStyle} mt-1`} value={filter} disabled={busy}
        onChange={event => { setFilter(event.target.value); setCursors([]); setNotice(""); }}>
        <option value="all">全部记录</option><option value="active">期限内未撤销</option>
        <option value="revoked">已撤销</option><option value="expired">已到期</option>
      </select></label>
      <button className="chip" disabled={busy} onClick={() => { setCursors([]); setRefresh(value => value + 1); }}>刷新记录</button>
    </div>
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {error && <p role="alert" className="text-sm text-(--danger)">{error}</p>}
    {loading ? <p role="status" className="text-sm">正在读取分享记录…</p> : <>
      {!page.items.length && !error && <p className="text-sm text-(--ink-soft)">当前条件下没有分享记录。</p>}
      {page.items.map(row => <article key={row.id} className={`${cardStyle} space-y-2`}>
        <h3 className="break-words font-semibold">{row.quote_readable ? row.product_name || "历史报价" : "原报价当前不可读取"}</h3>
        {row.departure_date && <p className="text-sm">{row.departure_date} 出发</p>}
        <p className="text-xs text-(--ink-soft)">分享编号：{row.id.slice(0, 8)}</p>
        <p className="text-sm">{stamp(row.created_at)} 创建 · 查看期限至 {stamp(row.expires_at)}（北京时间）</p>
        <p className="text-sm">{row.revoked_at ? "已撤销" : new Date(row.expires_at).getTime() <= Date.now() ? "已到期" : "期限内未撤销"}
          {!row.quote_readable && " · 当前无权读取报价内容，仍可撤销分享"}</p>
        {!row.revoked_at && (confirm === row.id ? <div className="space-y-2">
          <p className="text-sm">确认撤销这条分享？客户将无法继续打开此链接。</p>
          <div className="flex flex-wrap gap-2"><button className="btn-primary" disabled={busy} onClick={() => revoke(row.id)}>确认撤销</button>
            <button className="chip" disabled={busy} onClick={() => setConfirm(null)}>取消</button></div>
        </div> : <button className="chip" disabled={busy} onClick={() => setConfirm(row.id)}>撤销链接</button>)}
      </article>)}
    </>}
    <nav aria-label="分享记录分页" className="flex items-center justify-between gap-2 text-sm">
      <button className="chip" disabled={busy || !cursors.length} onClick={() => setCursors(values => values.slice(0, -1))}>上一页</button>
      <span>第 {cursors.length + 1} 页</span>
      <button className="chip" disabled={busy || !page.next_cursor} onClick={() => setCursors(values => [...values, page.next_cursor!])}>下一页</button>
    </nav>
  </section>;
}

export default function ShareHistory({ api }: { api: WarehouseClient }) {
  return <div className="panel-scroll h-full overflow-y-auto"><div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
    <div><h1 className="text-2xl font-semibold">我的报价分享</h1>
      <p className="mt-2 text-sm text-(--ink-soft)">查看当前工作空间中由你创建的分享。链接期限不代表报价仍有效。</p>
      <p className="mt-2 text-sm text-(--ink-soft)">完整链接仅创建时显示；如已遗失，请撤销旧链接，重新询价后创建。</p></div>
    <ShareRecords api={api}/>
  </div></div>;
}
