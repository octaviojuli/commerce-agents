"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { WarehouseClient, message } from "../lib/api";
import { Field, LoadState, Pagination, useData } from "./common";

type Source = {id:string;name:string;connector_type:string;active:boolean;version:number;capabilities:Record<string,unknown>};

export function Sources({api,writable,onSaved}:{api:WarehouseClient;writable:boolean;onSaved:(notice:string)=>void}) {
  const [cursors,setCursors]=useState([""]);
  const [selected,setSelected]=useState<Source|null>(null);
  const state=useData<{items:Source[];next_cursor:string|null}>(api,`/merchant/sources?limit=25${cursors.at(-1)?`&after=${cursors.at(-1)}`:""}`);
  return <>
    <p className="muted">每个供应源保留独立的线路、团期与库存归属。没有系统的供应商可创建 Excel 供应源，再上传团期；采购方访问须另行授权。</p>
    {writable&&<SourceForm api={api} onSaved={onSaved}/>}
    <LoadState {...state}/>
    {state.data&&<section className="panel stack"><div className="table-wrap"><table><thead><tr><th>供应源</th><th>库存归属</th><th>状态</th><th>操作</th></tr></thead><tbody>
      {state.data.items.map(row=><tr key={row.id}><td><strong>{row.name}</strong><p>{row.connector_type==="excel"?"Excel 导入":"系统接口"}</p></td><td>{row.capabilities.inventory_owner==="warehouse"?"云仓托管":"上游系统"}</td><td>{row.active?"已启用":"已停用"}</td><td>{row.connector_type==="excel"?<button className="link" onClick={()=>setSelected(row)}>{writable?"维护供应源":"查看供应源"}</button>:<span className="muted">由接入运维维护</span>}</td></tr>)}
    </tbody></table></div>{!state.data.items.length&&<p className="empty">当前组织还没有供应源。</p>}
      <Pagination next={state.data.next_cursor} previous={cursors.length>1} onNext={()=>{setSelected(null);setCursors([...cursors,state.data!.next_cursor!]);}} onPrevious={()=>{setSelected(null);setCursors(cursors.slice(0,-1));}}/>
    </section>}
    {selected&&<SourceForm key={`${selected.id}:${selected.version}`} api={api} source={selected} writable={writable} onSaved={onSaved}/>}
  </>;
}

function SourceForm({api,source,writable=true,onSaved}:{api:WarehouseClient;source?:Source;writable?:boolean;onSaved:(notice:string)=>void}) {
  const [name,setName]=useState(source?.name??"");
  const [active,setActive]=useState(source?.active??true);
  const [note,setNote]=useState("");
  const [confirmed,setConfirmed]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const request=useRef<{body:string;id:string}|null>(null);
  const pending=useRef<AbortController|null>(null);
  useEffect(()=>()=>pending.current?.abort(),[]);
  const changed=!source||name.trim()!==source.name||active!==source.active;
  async function submit(event:FormEvent) {
    event.preventDefault();
    if(!writable||!confirmed||!changed||busy) return;
    setBusy(true);setError("");
    const controller=new AbortController();pending.current=controller;
    const body={name:name.trim(),note:note.trim()};
    const key=JSON.stringify(body);
    if(!request.current||request.current.body!==key) request.current={body:key,id:crypto.randomUUID()};
    try {
      const response=await api.response(source?`/merchant/sources/${source.id}/control`:"/merchant/sources",{
        method:"POST",signal:controller.signal,headers:{"Content-Type":"application/json"},
        body:JSON.stringify(source?{...body,active,version:source.version}:{...body,request_id:request.current.id}),
      });
      const result=await response.json();
      if(!controller.signal.aborted) onSaved(source?"供应源已更新，历史线路和库存记录保留。":result.duplicate?"此前创建请求已完成，已读取现有供应源。":"Excel 供应源已创建，可进入 Excel 导入上传团期。尚未向采购方开放。");
    } catch(err) {if(!controller.signal.aborted) setError(message(err));}
    finally {if(!controller.signal.aborted) setBusy(false);}
  }
  return <section className="panel stack" aria-label={source?"供应源维护":"新建 Excel 供应源"}>
    <h2>{source?`${source.name} · 维护` : "新建 Excel 供应源"}</h2>
    <p className="muted">{source?`当前版本 ${source.version}。启停会更新关联授权版本，已有报价和审批预览需重新核对。`:"此供应源库存由云仓管理。线下成交后也须及时登记已售数量，避免实际销售与库存不一致。"}</p>
    <form onSubmit={submit} className="stack"><fieldset disabled={!writable||busy} className="stack" style={{border:0,padding:0,margin:0}}>
      <Field label="供应源名称"><input className="input" required maxLength={100} value={name} onChange={e=>{setName(e.target.value);setConfirmed(false);}}/></Field>
      {source&&<Field label="供应源状态"><select className="input" value={active?"enabled":"disabled"} onChange={e=>{setActive(e.target.value==="enabled");setConfirmed(false);}}><option value="enabled">启用</option><option value="disabled">停用</option></select></Field>}
      {source&&!active&&<p className="notice warning">停用后暂停此来源的 Excel 导入、库存登记及采购方查询。历史数据保留，恢复前请核对线下业务变化。</p>}
      {writable&&<><Field label="操作说明"><textarea className="input" required maxLength={500} value={note} onChange={e=>{setNote(e.target.value);setConfirmed(false);}}/></Field>
        <label className="row"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{source?"我已核对供应源、状态及业务影响":"我已确认由云仓统一管理此来源库存"}</label>
        <button className="btn primary" disabled={!confirmed||!changed||!name.trim()||!note.trim()}>{busy?"正在保存…":source?"确认保存供应源":"确认创建供应源"}</button></>}
    </fieldset>{error&&<p role="alert" className="notice error">{error}</p>}</form>
  </section>;
}
