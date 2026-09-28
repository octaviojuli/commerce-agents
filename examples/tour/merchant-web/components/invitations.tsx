"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { WarehouseClient, message, type Connection } from "../lib/api";
import { Field, LoadState, Pagination, useData } from "./common";

type Preview = {
  id: string; supplier_name: string; source_name: string;
  valid_from: string; expires_at: string | null; invite_expires_at: string;
};
type Invitation = Preview & {
  supplier_org_id: string; buyer_org_id: string | null; buyer_name: string | null;
  status: "offered" | "requested" | "approved" | "revoked";
  version: number; expired: boolean; grant_id: string | null;
};
type Created = Invitation & { code: string | null; duplicate: boolean };
const labels = { offered: "待采购方申请", requested: "待供应商批准", approved: "已批准", revoked: "已撤销" };
const time = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
const localNow = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 16);

function Terms({ row }: { row: Preview }) {
  return <div className="stack">
    <p><strong>{row.supplier_name} · {row.source_name}</strong></p>
    <p>授权开始：{time(row.valid_from)}<br />授权结束：{row.expires_at ? time(row.expires_at) : "长期有效"}</p>
    <p className="muted">邀请截止：{time(row.invite_expires_at)}（北京时间）</p>
  </div>;
}

// All writes are aborted on navigation; no invitation secret is stored in browser storage.
function useOperation(api: WarehouseClient) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  async function run<T>(path: string, body: unknown, done: (result: T) => void) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller; setBusy(true); setError("");
    try {
      const response = await api.response(path, {
        method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!controller.signal.aborted) done(result);
    } catch (err) { if (!controller.signal.aborted) setError(message(err)); }
    finally {
      if (!controller.signal.aborted) { pending.current = null; setBusy(false); }
    }
  }
  return { busy, error, run };
}

export function Invitations({ api, supplierAdmin, buyerAdmin }: {
  api: WarehouseClient; supplierAdmin: boolean; buyerAdmin: boolean;
}) {
  const [revision, setRevision] = useState(0);
  const [cursors, setCursors] = useState([""]);
  const [selected, setSelected] = useState<Invitation | null>(null);
  const [notice, setNotice] = useState("");
  const state = useData<{ items: Invitation[]; next_cursor: string | null }>(api,
    `/distribution-invitations?limit=25${cursors.at(-1) ? `&after=${cursors.at(-1)}` : ""}`, revision);
  function saved(text: string) {
    setSelected(null); setCursors([""]); setRevision(value => value + 1); setNotice(text);
  }
  return <>
    <p className="muted">供应商发起邀请，采购管理员确认申请，供应商核对采购组织后批准。申请期间尚未开放产品访问。时间均为北京时间。</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    {supplierAdmin && <CreateInvitation api={api} onSaved={saved} />}
    {buyerAdmin && <ClaimInvitation api={api} onSaved={saved} />}
    <section className="panel stack" aria-label="邀请记录">
      <h2>邀请记录</h2><LoadState {...state} />
      {state.data && <>
        <div className="table-wrap"><table className="invitation-table"><thead><tr><th>供应商 / 供应源</th><th>采购组织</th><th>进度</th><th>操作</th></tr></thead><tbody>
          {state.data.items.map(row => <tr key={row.id}>
            <td><strong>{row.supplier_name}</strong><p>{row.source_name}</p></td>
            <td data-label="采购组织">{row.buyer_name ?? "尚未申请"}</td>
            <td data-label="进度">{["offered", "requested"].includes(row.status) && row.expired ? "邀请已过期" : labels[row.status]}<p className="muted">邀请截止 {time(row.invite_expires_at)}</p></td>
            <td><button className="link" onClick={() => setSelected(row)}>查看邀请</button></td>
          </tr>)}
        </tbody></table></div>
        {!state.data.items.length && <p className="empty">当前组织暂无邀请记录。</p>}
        <Pagination next={state.data.next_cursor} previous={cursors.length > 1}
          onNext={() => { setSelected(null); setCursors([...cursors, state.data!.next_cursor!]); }}
          onPrevious={() => { setSelected(null); setCursors(cursors.slice(0, -1)); }} />
      </>}
    </section>
    {selected && <Decision key={`${selected.id}:${selected.version}`} api={api} row={selected}
      writable={supplierAdmin && selected.supplier_org_id === api.organization} onSaved={saved} />}
  </>;
}

function CreateInvitation({ api, onSaved }: { api: WarehouseClient; onSaved: (text: string) => void }) {
  const sources = useData<{ items: Connection[] }>(api, "/merchant/connections");
  const [source, setSource] = useState("");
  const [from, setFrom] = useState(localNow);
  const [until, setUntil] = useState("");
  const [permanent, setPermanent] = useState(false);
  const [hours, setHours] = useState("72");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const request = useRef<{ body: string; id: string } | null>(null);
  const operation = useOperation(api);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!confirmed || created || !source) return;
    const body = { connection_id: source, valid_from: `${from}:00+08:00`, expires_at: permanent ? null : `${until}:00+08:00`, invite_hours: Number(hours), note: note.trim() };
    const key = JSON.stringify(body);
    if (!request.current || request.current.body !== key) request.current = { body: key, id: crypto.randomUUID() };
    await operation.run<Created>("/distribution-invitations", { ...body, request_id: request.current.id }, result => {
      setCreated(result); setConfirmed(false);
      onSaved("邀请已创建。请由管理员将邀请码交给预期采购方，收到申请后再核对组织并批准。");
    });
  }
  return <section className="panel stack" aria-label="创建分销邀请">
    <h2>邀请采购方</h2>
    {created ? <>
      <Terms row={created} />
      {created.code ? <>
        <p className="notice warning">邀请码仅在此显示一次。离开或刷新后无法找回，请妥善交付给预期采购方；遗失时撤销此邀请后重新创建。</p>
        <Field label="邀请码"><input className="input" readOnly value={created.code} onFocus={event => event.target.select()} autoComplete="off" spellCheck={false} /></Field>
      </> : <p className="notice warning">此前创建请求已成功，但邀请码无法再次显示。如未保存，请在邀请记录中撤销后重新创建。</p>}
      <button className="btn" onClick={() => { setCreated(null); setNote(""); request.current = null; }}>已保存，创建另一份邀请</button>
    </> : <form className="stack" onSubmit={submit} onChange={() => setConfirmed(false)}>
      <LoadState {...sources} />
      <fieldset className="stack" disabled={operation.busy} style={{ border: 0, padding: 0, margin: 0 }}>
        <Field label="邀请供应源"><select className="input" value={source} required onChange={event => setSource(event.target.value)}>
          <option value="">请选择供应源</option>{sources.data?.items.filter(row => row.active).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></Field>
        <div className="form-grid">
          <Field label="授权开始（北京时间）"><input className="input" type="datetime-local" required value={from} onChange={event => setFrom(event.target.value)} /></Field>
          <Field label="邀请有效小时数"><input className="input" type="number" min={1} max={168} step={1} required value={hours} onChange={event => setHours(event.target.value)} /></Field>
        </div>
        <label className="row"><input type="checkbox" checked={permanent} onChange={event => setPermanent(event.target.checked)} />授权长期有效</label>
        {!permanent && <Field label="授权结束（北京时间）"><input className="input" type="datetime-local" required min={from} value={until} onChange={event => setUntil(event.target.value)} /></Field>}
        <Field label="邀请说明（仅供应方内部留档）"><textarea className="input" required maxLength={500} value={note} onChange={event => setNote(event.target.value)} /></Field>
      </fieldset>
      <label className="row"><input type="checkbox" checked={confirmed} disabled={operation.busy} onChange={event => { event.stopPropagation(); setConfirmed(event.target.checked); }} />我已核对供应源和授权有效期，批准前将再次确认采购组织</label>
      <button className="btn primary" disabled={operation.busy || !confirmed || !source || !note.trim()}>{operation.busy ? "正在创建…" : "确认创建邀请"}</button>
    </form>}
    {operation.error && <p className="notice error" role="alert">{operation.error}</p>}
  </section>;
}

function ClaimInvitation({ api, onSaved }: { api: WarehouseClient; onSaved: (text: string) => void }) {
  const [code, setCode] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const operation = useOperation(api);
  async function inspect(event: FormEvent) {
    event.preventDefault(); setPreview(null); setConfirmed(false);
    await operation.run<Preview>("/distribution-invitations/preview", { code: code.trim() }, setPreview);
  }
  return <section className="panel stack" aria-label="申请供应商邀请">
    <h2>接受供应商邀请</h2>
    <form className="stack" onSubmit={inspect}>
      <Field label="供应商提供的邀请码"><input className="input" value={code} required pattern="[A-Za-z0-9_\-]{43}" autoComplete="off" spellCheck={false} disabled={operation.busy}
        onChange={event => { setCode(event.target.value.trim()); setPreview(null); setConfirmed(false); }} /></Field>
      <button className="btn" disabled={operation.busy || !/^[A-Za-z0-9_-]{43}$/.test(code)}>{operation.busy ? "正在核对…" : "查看邀请内容"}</button>
    </form>
    {preview && <div className="stack">
      <Terms row={preview} />
      <label className="row"><input type="checkbox" checked={confirmed} disabled={operation.busy} onChange={event => setConfirmed(event.target.checked)} />我已核对供应商、供应源和有效期，代表当前采购组织申请加入</label>
      <button className="btn primary" disabled={!confirmed || operation.busy} onClick={() => operation.run<Invitation>("/distribution-invitations/claim", { code }, () => {
        setCode(""); setPreview(null); setConfirmed(false); onSaved("申请已提交，待供应商核对并批准后开放访问。");
      })}>确认提交加入申请</button>
    </div>}
    {operation.error && <p className="notice error" role="alert">{operation.error}</p>}
  </section>;
}

function Decision({ api, row, writable, onSaved }: {
  api: WarehouseClient; row: Invitation; writable: boolean; onSaved: (text: string) => void;
}) {
  const [action, setAction] = useState("revoke");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const operation = useOperation(api);
  const pending = ["offered", "requested"].includes(row.status);
  const mayApprove = row.status === "requested" && !row.expired && (!row.expires_at || new Date(row.expires_at).getTime() > Date.now());
  return <section className="panel details stack" aria-label="邀请核对">
    <h2>核对邀请</h2><Terms row={row} />
    <p>采购组织：<strong>{row.buyer_name ?? "尚未申请"}</strong></p>
    {row.buyer_org_id && <p className="muted" style={{ overflowWrap: "anywhere" }}>采购组织编号：{row.buyer_org_id}</p>}
    <p>进度：{labels[row.status]}</p>
    {row.status === "approved" && <p className="notice">邀请已批准。当前访问仍受分销授权、来源状态和有效期约束；供应方可在“分销授权”维护。</p>}
    {pending && row.expired && <p className="notice warning">邀请已过期，不能继续批准。供应商可撤销后重新创建。</p>}
    {writable && pending && <form className="stack" onSubmit={event => {
      event.preventDefault(); if (!confirmed || (action === "approve" && !mayApprove)) return;
      void operation.run<Invitation>(`/distribution-invitations/${row.id}/decision`, { version: row.version, action, note: note.trim() }, result => {
        onSaved(result.status === "approved" ? "已批准采购组织加入，按约定有效期开放供应源访问。" : "邀请已撤销，不再接受申请或批准。");
      });
    }}>
      <fieldset className="stack" disabled={operation.busy} style={{ border: 0, padding: 0, margin: 0 }}>
        <Field label="处理方式"><select className="input" value={action} onChange={event => { setAction(event.target.value); setConfirmed(false); }}>
          <option value="revoke">撤销邀请</option>{mayApprove && <option value="approve">批准采购组织加入</option>}
        </select></Field>
        <Field label="处理说明"><textarea className="input" required maxLength={500} value={note} onChange={event => { setNote(event.target.value); setConfirmed(false); }} /></Field>
        <label className="row"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我已核对采购组织身份、供应源、有效期和本次处理方式</label>
        <button className="btn primary" disabled={!confirmed || !note.trim()}>{operation.busy ? "正在提交…" : action === "approve" ? "确认批准加入" : "确认撤销邀请"}</button>
      </fieldset>
      {operation.error && <p className="notice error" role="alert">{operation.error}</p>}
    </form>}
  </section>;
}
