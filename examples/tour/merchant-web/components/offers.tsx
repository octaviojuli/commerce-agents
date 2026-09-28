"use client";

import { useState, type FormEvent } from "react";
import { WarehouseClient, message } from "../lib/api";
import { Field, LoadState, Pagination, useData } from "./common";

type Offer = {
  id: string; departure_id: string; code: string; name: string;
  service_description: string; active: boolean; version: number; inventory_pool_id: string | null;
};
export function OfferManager({ api, departure, writable, onProposed, onClose }: {
  api: WarehouseClient; departure: string; writable: boolean; onProposed: () => void; onClose: () => void;
}) {
  const [cursors, setCursors] = useState([""]);
  const [selected, setSelected] = useState<Offer | "new" | null>(null);
  const state = useData<{ items: Offer[]; next_cursor: string | null }>(api,
    `/merchant/departures/${departure}/offers?limit=25${cursors.at(-1) ? `&after=${cursors.at(-1)}` : ""}`);
  const managed = state.data?.items.some(row => row.inventory_pool_id != null);
  return <section className="panel stack" aria-label="团期报价方案">
    <div className="row between"><h3>团期报价方案</h3><button className="btn" onClick={onClose}>收起方案</button></div>
    <p className="notice">同一团期的方案共享名额。新增方案不增加库存；需在“价表与等级”单独维护该方案价格。顾问询价时须明确选择方案。</p>
    <LoadState {...state} />
    {state.data && <>
      {!managed && <p className="muted">API 来源的方案由上游适配器维护；Excel 团期须先完成库存交接。</p>}
      {writable && managed && <button className="btn" onClick={() => setSelected("new")}>新增报价方案</button>}
      {state.data.items.map(row => <article className="panel stack" key={row.id}>
        <strong>{row.name} · {row.code} · {row.active ? "已启用" : "已停用"}</strong>
        <p style={{whiteSpace:"pre-wrap"}}>{row.service_description || "尚未补充服务说明"}</p>
        {writable && row.inventory_pool_id && <button className="btn" onClick={() => setSelected(row)}>编辑报价方案</button>}
      </article>)}
      <Pagination next={state.data.next_cursor} previous={cursors.length>1} onPrevious={() => {setCursors(cursors.slice(0,-1));setSelected(null);}} onNext={() => {setCursors([...cursors,state.data!.next_cursor!]);setSelected(null);}} />
    </>}
    {selected && writable && <OfferForm key={selected === "new" ? "new" : selected.id} api={api} departure={departure} saved={selected === "new" ? null : selected} onProposed={onProposed} onClose={() => setSelected(null)} />}
  </section>;
}
function OfferForm({ api, departure, saved, onProposed, onClose }: {
  api: WarehouseClient; departure: string; saved: Offer | null; onProposed: () => void; onClose: () => void;
}) {
  const [identifier] = useState(() => saved?.id ?? crypto.randomUUID());
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data=new FormData(event.currentTarget);setBusy(true);setError("");
    try { await api.post("/merchant/offers/proposals",{
      offer_id:identifier,departure_id:departure,expected_version:saved?.version ?? 0,
      code:saved?.code ?? String(data.get("code") ?? "").trim(),name:String(data.get("name") ?? "").trim(),
      service_description:String(data.get("description") ?? "").trim(),active:data.get("active")==="on",note:String(data.get("note") ?? "").trim(),
    });onProposed(); } catch(e) {setError(message(e));} finally {setBusy(false);}
  }
  return <form className="stack" onSubmit={submit}>
    <h4>{saved ? "编辑已有方案" : "新增报价方案"}</h4>
    <fieldset className="stack" disabled={busy} style={{border:0,padding:0,margin:0}}>
      {saved ? <p>方案编号：{saved.code}，所属团期与库存池保持不变。</p> : <Field label="方案编号"><input className="input" name="code" required maxLength={80} /></Field>}
      <Field label="方案名称"><input className="input" name="name" required maxLength={200} defaultValue={saved?.name ?? ""} /></Field>
      <Field label="服务标准与方案说明"><textarea className="input" name="description" rows={5} maxLength={10000} defaultValue={saved?.service_description ?? ""} /></Field>
      <Field label="方案变更说明"><input className="input" name="note" required maxLength={500} /></Field>
      <label className="row"><input type="checkbox" name="active" defaultChecked={saved?.active ?? true} />启用此报价方案</label>
      <p className="muted">停用后顾问不能新询价，既有报价及分享不能继续作为有效报价；不会扣减或增加库存。</p>
      <div className="row"><button className="btn primary">{busy ? "生成中…" : "生成方案审批预览"}</button><button type="button" className="btn" onClick={onClose}>取消</button></div>
    </fieldset>
    {error && <p className="notice error" role="alert">{error}</p>}
  </form>;
}
