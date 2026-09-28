"use client";
import { useState } from "react";
import { message, type WarehouseClient } from "web-shared/warehouse-client";
import { stamp } from "@/lib/warehouse";
import { recordLabels, type RecordItem } from "./copilot-types";
import { fieldNames, fieldText } from "./BriefPanel";

export default function CopilotHistory({api, id}: {api: WarehouseClient; id: string}) {
  const [items, setItems] = useState<RecordItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  async function load() {
    try {
      const page = await api.get<{items: RecordItem[]; next_cursor: string | null}>(`/copilot/deals/${id}/history${cursor ? `?before=${cursor}` : ""}`);
      setItems(v => [...v, ...page.items]); setCursor(page.next_cursor); setLoaded(true);
    } catch(e) {setError(message(e));}
  }
  return <details className="cp-panel"><summary>完整跟单档案</summary>
    <p className="cp-muted">按时间查阅保留的原始版本。历史内容供回顾，发送前须重新核对。</p>
    {error && <p role="alert">{error}</p>}
    {items.map(r => <details key={r.id} className="cp-record"><summary>{recordLabels[r.kind] || "材料核对"} · v{r.brief_version} · {stamp(r.created_at)}</summary>
      {r.kind === "state" ? <p>{Object.entries(r.body).filter(([k,v]) => fieldNames[k] && v?.value !== null).map(([k,v]) => `${fieldNames[k]}：${fieldText(k,v.value)}`).join("；")}</p> :
        <p className="cp-pre">{r.body.text || r.body.answer || r.body.evidence || (r.body.amount ? `${r.body.category === "refund" ? "退款" : "收款"} ${r.body.currency} ${r.body.amount} · ${r.body.reference}` : r.body.sales_total ? `销售总价 ${r.body.currency} ${r.body.sales_total} · 结算 ${r.body.settlement_total || "见对应报价"}` : "此记录的处理结果已保留。证件信息请在旅客与材料中核对。")}</p>}
    </details>)}
    {(!loaded || cursor) && <button onClick={() => void load()}>{loaded ? "更早的记录" : "读取档案"}</button>}
  </details>;
}
