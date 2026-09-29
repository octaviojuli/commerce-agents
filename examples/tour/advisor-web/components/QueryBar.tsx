"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { QueryContext } from "@/lib/types";

export default function QueryBar({ deal, query, onDone }: { deal: string; query: QueryContext | null; onDone: () => void | Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!query) return null;
  async function resolve(adopt: boolean) {
    setBusy(true);
    setError("");
    try {
      await api.post(`/deals/${deal}/query/resolve`, { query_id: query!.id, adopt });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      await onDone();
      setBusy(false);
    }
  }
  return <aside className="query-bar" aria-label="本次查询条件">
    <b>本次查询</b><span>{query.conditions.join(" · ")}</span>
    <small>{query.notice}。记入需求后将重新检查选线和报价。</small>
    <div className="row" style={{ gap: 8 }}>
      <button type="button" className="b sm b-br" disabled={busy} onClick={() => resolve(true)}>记入需求</button>
      <button type="button" className="b sm b-soft" disabled={busy} onClick={() => resolve(false)}>撤销并重查</button>
    </div>
    {error && <span role="alert">{error}</span>}
  </aside>;
}
