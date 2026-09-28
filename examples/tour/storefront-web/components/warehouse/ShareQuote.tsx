"use client";

import { useEffect, useRef, useState } from "react";
import { WarehouseClient, message } from "web-shared/warehouse-client";
import { inputStyle } from "@/lib/warehouse";
import CustomerQuote, { type CustomerQuoteData } from "./CustomerQuote";
import { ShareRecords } from "./ShareHistory";

type Share = {id:string;created_at:string;expires_at:string;revoked_at:string|null;token?:string|null};

export default function ShareQuote({api,quoteId}:{api:WarehouseClient;quoteId:string}) {
  const [open,setOpen]=useState(false), [preview,setPreview]=useState<CustomerQuoteData|null>(null);
  const [revision,setRevision]=useState(0),[hours,setHours]=useState(72),[confirmed,setConfirmed]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[link,setLink]=useState("");
  const [issued,setIssued]=useState("");
  const request=useRef<string|null>(null);
  useEffect(()=>{
    if (!open) return;
    let active=true;
    setBusy(true);setError("");
    setPreview(null);
    api.get<CustomerQuoteData>(`/quotes/${quoteId}/customer-view`)
      .then(view=>{if(active)setPreview(view);})
      .catch(err=>{if(active)setError(message(err));})
      .finally(()=>{if(active)setBusy(false);});
    return ()=>{active=false;};
  },[api,quoteId,open]);
  async function create() {
    setBusy(true);setError("");request.current ??= crypto.randomUUID();
    try {
      const share=await api.post<Share>(`/quotes/${quoteId}/shares`,{request_id:request.current,hours});
      request.current=null;
      if(share.token){setLink(`${window.location.origin}/quote#${share.token}`);setIssued(share.id);}
      else setError("此请求的链接已生成，密钥不会重复显示；如未保存，请撤销记录后重新创建。");
      setRevision(value=>value+1);setConfirmed(false);
    } catch(err){setError(message(err));} finally{setBusy(false);}
  }
  if(!open)return <button className="chip" onClick={()=>setOpen(true)}>客户报价预览与分享</button>;
  return <section className="space-y-3 rounded-xl border border-(--line) p-3" aria-label="客户报价分享">
    <div className="flex justify-between gap-3"><h3 className="font-semibold">核对客户将看到的内容</h3><button className="chip" disabled={busy} onClick={()=>setOpen(false)}>收起</button></div>
    {preview && <CustomerQuote quote={preview}/>}
    <label className="block text-sm">链接查看期限<select className={`${inputStyle} mt-1`} value={hours} disabled={busy} onChange={e=>{setHours(Number(e.target.value));request.current=null;}}><option value={24}>1 天</option><option value={72}>3 天</option><option value={168}>7 天</option></select></label>
    <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={confirmed} disabled={busy||!preview} onChange={e=>setConfirmed(e.target.checked)}/>我已核对以上市场报价，允许持有链接的人查看。报价有效期不会延长。</label>
    <button className="btn-primary" disabled={busy||!confirmed||!preview} onClick={create}>{busy?"处理中…":"创建客户分享链接"}</button>
    {link && <div className="space-y-2"><label className="block text-sm">新链接（请自行交付客户）<input className={`${inputStyle} mt-1`} readOnly value={link} onFocus={e=>e.target.select()}/></label><a className="text-sm underline" href={link} target="_blank" rel="noreferrer">打开客户预览</a><p className="text-xs text-(--ink-soft)">完整链接仅在此次创建后展示，请及时保存。</p></div>}
    {error && <p role="alert" className="text-sm text-(--danger)">{error}</p>}
    <h4 className="text-sm font-semibold">此报价的分享记录</h4>
    <ShareRecords api={api} quoteId={quoteId} revision={revision} onRevoke={id=>{if(id===issued)setLink("");}}/>
  </section>;
}
