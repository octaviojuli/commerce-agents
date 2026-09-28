"use client";

import { useState, type FormEvent } from "react";
import { WarehouseClient, message } from "../lib/api";
import { Field, LoadState, useData } from "./common";

type Display = {
  target_id: string; version: number; display_version: number;
  source_name: string; source_description: string;
  name_override: string | null; description_override: string | null;
};

export function ProductDisplay({ api, id, writable, onProposed }: {
  api: WarehouseClient; id: string; writable: boolean; onProposed: () => void;
}) {
  const state = useData<Display>(api, `/merchant/products/${id}/display`);
  return <section className="panel stack" aria-label="API 线路展示补充">
    <h3>云仓展示补充</h3>
    <p className="notice">可整理名称和介绍，经审批后供采购方查看。上游事实单独保留，后续同步不覆盖补充内容。团期日期、价格、库存和已复核行程通过各自流程维护。</p>
    <LoadState {...state} />
    {state.data && <>
      <h4>上游原始内容</h4>
      <p>{state.data.source_name}</p>
      <p style={{ whiteSpace: "pre-wrap" }}>{state.data.source_description || "上游介绍未提供"}</p>
      {writable && <DisplayForm key={`${id}:${state.data.version}:${state.data.display_version}`} api={api} current={state.data} onProposed={onProposed} />}
    </>}
  </section>;
}

function DisplayForm({ api, current, onProposed }: { api: WarehouseClient; current: Display; onProposed: () => void }) {
  const [inheritName, setInheritName] = useState(current.name_override === null);
  const [inheritDescription, setInheritDescription] = useState(current.description_override === null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await api.post("/merchant/product-display/proposals", {
        target_id: current.target_id, expected_version: current.version, expected_display_version: current.display_version,
        name_override: inheritName ? null : form.get("name"),
        description_override: inheritDescription ? null : form.get("description"),
        note: form.get("note"),
      }); onProposed();
    } catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  return <form className="stack" onSubmit={submit}>
    <fieldset className="stack" disabled={busy}>
      <label className="check"><input type="checkbox" checked={inheritName} onChange={e=>setInheritName(e.target.checked)} />名称沿用上游内容</label>
      <Field label="云仓展示名称"><input className="input" name="name" required={!inheritName} disabled={inheritName} maxLength={300} defaultValue={current.name_override ?? current.source_name} /></Field>
      <label className="check"><input type="checkbox" checked={inheritDescription} onChange={e=>setInheritDescription(e.target.checked)} />介绍沿用上游内容</label>
      <Field label="云仓展示介绍"><textarea className="input" name="description" disabled={inheritDescription} maxLength={10000} rows={6} defaultValue={current.description_override ?? current.source_description} /></Field>
      <p className="muted">勾选“沿用上游内容”可撤销对应补充。取消勾选后留空介绍，表示明确不展示介绍。</p>
      <Field label="展示补充变更说明"><input className="input" name="note" required maxLength={500} /></Field>
      <button className="btn primary">生成展示补充审批预览</button>
    </fieldset>
    {error && <p className="error" role="alert">{error}</p>}
  </form>;
}
