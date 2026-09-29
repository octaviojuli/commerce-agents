"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { WarehouseClient, message } from "../lib/api";
import { Field, LoadState, Pagination, useData } from "./common";

type Grant = {
  id:string; connection_id:string; buyer_org_id:string; buyer_name:string;
  source_name:string; source_active:boolean; connector_type:string;
  active:boolean; effective:boolean; valid_from:string; expires_at:string|null; version:number;
};
const localInput = (value:string) => new Date(new Date(value).getTime()+8*3600000).toISOString().slice(0,16);
const displayTime = (value:string) => new Date(value).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"});

export function Grants({api,writable,onRefresh}:{api:WarehouseClient;writable:boolean;onRefresh:()=>void}) {
  const [cursors,setCursors]=useState<string[]>([""]);
  const [selected,setSelected]=useState<Grant|null>(null);
  const state=useData<{items:Grant[];next_cursor:string|null}>(api,`/merchant/grants?limit=25${cursors.at(-1)?`&after=${cursors.at(-1)}`:""}`);
  return <>
    <p className="muted">按供应源管理已有采购组织的访问授权。停用后，该采购组织不能继续查询该来源产品、报价和文档；已下载的文件不会被收回。时间均为北京时间。</p>
    <LoadState {...state}/>
    {state.data && <section className="panel stack">
      <div className="table-wrap"><table><thead><tr><th>采购组织 / 供应源</th><th>状态与有效期</th><th>操作</th></tr></thead><tbody>
        {state.data.items.map(row=><tr key={row.id}><td><strong>{row.buyer_name}</strong><p>{row.source_name}</p><small>{row.connector_type==="excel"?"Excel 托管库存":"系统接口来源"}</small></td>
          <td>{row.effective?"当前有效":row.active?"当前未生效":"已停用"}<p>{displayTime(row.valid_from)} 起</p><small>{row.expires_at?`${displayTime(row.expires_at)} 止`:"长期有效"}</small>{!row.source_active&&<p>供应源已停用</p>}</td>
          <td><button className="link" onClick={()=>setSelected(row)}>{writable?"管理授权":"查看授权"}</button></td></tr>)}
      </tbody></table></div>
      {!state.data.items.length&&<p className="empty">当前供应组织没有已有分销授权。</p>}
      <Pagination next={state.data.next_cursor} previous={cursors.length>1} onNext={()=>{setSelected(null);setCursors([...cursors,state.data!.next_cursor!]);}} onPrevious={()=>{setSelected(null);setCursors(cursors.slice(0,-1));}}/>
    </section>}
    {selected&&<GrantEditor key={`${selected.id}:${selected.version}`} api={api} grant={selected} writable={writable} onSaved={onRefresh}/>}
    <p className="muted">新增采购关系请进入“供采邀请”，Excel 来源可在“供应源管理”创建。系统接口接入仍由平台办理；此页不修改 ERP 凭据、采购客户映射或协议价格。</p>
  </>;
}

function GrantEditor({api,grant,writable,onSaved}:{api:WarehouseClient;grant:Grant;writable:boolean;onSaved:()=>void}) {
  const [active,setActive]=useState(grant.active);
  const [from,setFrom]=useState(localInput(grant.valid_from));
  const [until,setUntil]=useState(grant.expires_at?localInput(grant.expires_at):"");
  const [permanent,setPermanent]=useState(!grant.expires_at);
  const [note,setNote]=useState("");
  const [confirmed,setConfirmed]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const pending=useRef<AbortController|null>(null);
  useEffect(()=>()=>pending.current?.abort(),[]);
  const start=from===localInput(grant.valid_from)?grant.valid_from:`${from}:00+08:00`;
  const end=permanent?null:grant.expires_at&&until===localInput(grant.expires_at)?grant.expires_at:`${until}:00+08:00`;
  const changed=active!==grant.active || start!==grant.valid_from || end!==grant.expires_at;
  async function save(event:FormEvent) {
    event.preventDefault();
    if(!writable || !confirmed || !changed || busy) return;
    setBusy(true);setError("");
    const controller=new AbortController();pending.current=controller;
    try {
      await api.response(`/merchant/grants/${grant.id}/control`,{
        method:"POST",signal:controller.signal,headers:{"Content-Type":"application/json"},
        body:JSON.stringify({version:grant.version,active,valid_from:start,expires_at:end,note}),
      });
      if(!controller.signal.aborted) onSaved();
    } catch(err) {if(!controller.signal.aborted) setError(message(err));}
    finally {if(!controller.signal.aborted) setBusy(false);}
  }
  return <section className="panel details stack" aria-label="分销授权核对">
    <h2>{grant.buyer_name} · {grant.source_name}</h2>
    <p className="muted">当前授权版本 {grant.version}。修改会记录管理员身份、修改前后内容和操作说明。</p>
    <form onSubmit={save} className="stack">
      <fieldset disabled={!writable||busy} className="stack" style={{border:0,padding:0,margin:0}}>
        <Field label="授权状态"><select className="input" value={active?"enabled":"disabled"} onChange={e=>{setActive(e.target.value==="enabled");setConfirmed(false);}}><option value="enabled">启用</option><option value="disabled">停用</option></select></Field>
        <Field label="开始时间（北京时间）"><input className="input" type="datetime-local" required value={from} onChange={e=>{setFrom(e.target.value);setConfirmed(false);}}/></Field>
        <label className="row"><input type="checkbox" checked={permanent} onChange={e=>{setPermanent(e.target.checked);setConfirmed(false);}}/>长期有效</label>
        {!permanent&&<Field label="结束时间（北京时间）"><input className="input" type="datetime-local" required value={until} onChange={e=>{setUntil(e.target.value);setConfirmed(false);}}/></Field>}
        {writable&&<>
          <p className="notice warning">{active?"启用或延长后，采购方可在有效期内访问该供应源授权的数据。已有报价需按最新授权重新核验。":"停用会立即阻止采购方继续读取该供应源的数据。历史记录保留，可由管理员重新启用。"}</p>
          <Field label="操作说明"><textarea className="input" required maxLength={500} value={note} onChange={e=>{setNote(e.target.value);setConfirmed(false);}}/></Field>
          <label className="row"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已核对采购组织、供应源、状态及有效期</label>
          <button className="btn primary" disabled={!confirmed||!changed||!note.trim()}>{busy?"正在保存…":"确认保存授权"}</button>
        </>}
      </fieldset>
      {error&&<p className="notice error" role="alert">{error}</p>}
    </form>
  </section>;
}
