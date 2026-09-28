"use client";

import { useEffect, useState, type FormEvent } from "react";
import { message, type WarehouseClient } from "web-shared/warehouse-client";
import type { Customer, DealDetail } from "./copilot-types";

type Props = {
  api: WarehouseClient;
  detail: DealDetail;
  busy: boolean;
  run: (path: string, body?: Record<string, unknown>) => Promise<any>;
};

export default function CopilotCustomer({ api, detail, busy, run }: Props) {
  const [items, setItems] = useState<Customer[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api.get<{ items: Customer[]; next_cursor: string | null }>(
      `/copilot/customers?query=${encodeURIComponent(query)}`, controller.signal,
    ).then(page => { setItems(page.items); setCursor(page.next_cursor); setError(""); })
      .catch(e => { if (!controller.signal.aborted) setError(message(e)); });
    return () => controller.abort();
  }, [api, query]);
  async function associate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await run(`/copilot/deals/${detail.id}/customer`, { customer_id: data.get("customer") });
  }
  return <details className="cp-panel">
    <summary>{detail.customer ? `关联客户：${detail.customer.body.name}` : "为这份跟单关联客户"}</summary>
    <p className="cp-muted">同一位客户的多次旅行分别建立跟单，客户档案只由你管理。</p>
    {error && <p role="alert" className="cp-error">{error}</p>}
    <input aria-label="查找客户" placeholder="按客户称呼查找" value={query} onChange={e => setQuery(e.target.value)} />
    <form onSubmit={associate}>
      <label>选择已有客户<select name="customer" required key={detail.customer?.id} defaultValue={detail.customer?.id || ""}>
        <option value="">请选择客户</option>
        {items.map(c => <option key={c.id} value={c.id}>{c.body.name}</option>)}
      </select></label>
      <button disabled={busy || !items.length}>保存客户关联</button>
    </form>
    {cursor && <button onClick={async () => {
      try {
        const page = await api.get<{items: Customer[]; next_cursor: string | null}>(`/copilot/customers?query=${encodeURIComponent(query)}&before=${cursor}`);
        setItems(v => [...v, ...page.items]); setCursor(page.next_cursor);
      } catch (e) { setError(message(e)); }
    }}>更多客户</button>}
    <details><summary>直接新建客户并关联</summary><form onSubmit={async event => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const customer = await run("/copilot/customers", { customer: { name: String(data.get("name")), contact: String(data.get("contact") || ""), note: "", travelers: [] } });
      if (customer) await run(`/copilot/deals/${detail.id}/customer`, { customer_id: customer.id });
    }}>
      <label>客户称呼<input name="name" required maxLength={100}/></label>
      <label>联系方式<input name="contact" maxLength={200}/></label>
      <button disabled={busy}>创建并关联</button>
    </form></details>
  </details>;
}
