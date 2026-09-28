"use client";
import { useEffect, useState } from "react";
import { message, type WarehouseClient } from "web-shared/warehouse-client";
import { stamp } from "@/lib/warehouse";

type Page = {items: {id: string; title: string | null; resumable: boolean; created_at: string}[]; next_cursor: string | null};
export default function CopilotArchive({api, open}: {api: WarehouseClient; open: (id: string) => void}) {
  const [page, setPage] = useState<Page>({items: [], next_cursor: null});
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api.get<Page>("/conversations?role=advisor", controller.signal).then(setPage)
      .catch(e => {if (!controller.signal.aborted) setError(message(e));});
    return () => controller.abort();
  }, [api]);
  return <section className="cp-panel"><h2>历史会话</h2><p className="cp-muted">已有会话可继续作为跟单使用。过期报价仍需重新核对。</p>
    {error && <p role="alert">{error}</p>}
    {page.items.map(item => <button key={item.id} className="cp-record" disabled={!item.resumable} onClick={() => open(item.id)}>
      {item.title || "历史顾问会话"} · {stamp(item.created_at)}{!item.resumable && " · 权限已变化，不能继续"}
    </button>)}
    {!page.items.length && !error && <p>暂无历史会话。</p>}
    {page.next_cursor && <button onClick={async () => {
      try {const next = await api.get<Page>(`/conversations?role=advisor&before=${page.next_cursor}`);setPage(p => ({items: [...p.items, ...next.items], next_cursor: next.next_cursor}));}
      catch (e) {setError(message(e));}
    }}>更早的会话</button>}
  </section>;
}
