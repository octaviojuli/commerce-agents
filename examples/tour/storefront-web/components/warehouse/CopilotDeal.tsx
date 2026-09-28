"use client";
import {useCallback,useEffect,useRef,useState,type FormEvent} from "react";
import {WarehouseClient,message,warehouseEvents,type WarehouseEvent} from "web-shared/warehouse-client";
import type {DealDetail,Transcript,Inquiry,Turn} from "./copilot-types";
import {recordLabels} from "./copilot-types";
import {type WorkbenchAction,type BriefEnvelope,stamp} from "@/lib/warehouse";
import BriefPanel,{fieldNames,fieldText} from "./BriefPanel";
import ResultCards from "./ResultCards";
import OfferCards from "./OfferCards";
import {QuoteCard} from "./Quote";
import {copyText} from "./mobile";
import CopilotBusiness from "./CopilotBusiness";
import CopilotHistory from "./CopilotHistory";

export default function CopilotDeal({api,id,back}:{api:WarehouseClient;id:string;back:()=>void}){
  const [detail,setDetail]=useState<DealDetail|null>(null),[transcript,setTranscript]=useState<Transcript>({turns:[],busy:false,next_cursor:null});
  const [inquiries,setInquiries]=useState<Inquiry[]>([]),[tab,setTab]=useState("chat"),[error,setError]=useState(""),[notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false),[input,setInput]=useState(""),[live,setLive]=useState<WarehouseEvent[]>([]),[progress,setProgress]=useState("");
  const mounted=useRef(true),sending=useRef(false),controller=useRef<AbortController|null>(null),tail=useRef<HTMLDivElement|null>(null);
  const load=useCallback(async(signal?:AbortSignal)=>{
    try{const d=await api.get<DealDetail>(`/copilot/deals/${id}`,signal); const [t,i]=d.conversation_available===false?[{turns:[],busy:false,next_cursor:null},{items:[]}]:await Promise.all([api.get<Transcript>(`/conversations/${id}`,signal),api.get<{items:Inquiry[]}>(`/copilot/inquiries?deal_id=${id}`,signal)]);
      if(mounted.current&&!signal?.aborted){setDetail(d);setTranscript(t);setInquiries(i.items);if(!t.busy)setProgress("");}
    }catch(e){if(mounted.current&&!signal?.aborted){setError(message(e));setDetail(null);setTranscript({turns:[],busy:false,next_cursor:null});setInquiries([]);}}
  },[api,id]);
  useEffect(()=>{mounted.current=true;const abort=new AbortController();void load(abort.signal);
    const refresh=()=>{if(document.visibilityState==="visible"&&!sending.current)void load(abort.signal);};
    const timer=setInterval(refresh,15000);window.addEventListener("focus",refresh);window.addEventListener("online",refresh);document.addEventListener("visibilitychange",refresh);
    return()=>{mounted.current=false;abort.abort();controller.current?.abort();clearInterval(timer);window.removeEventListener("focus",refresh);window.removeEventListener("online",refresh);document.removeEventListener("visibilitychange",refresh);};
  },[load]);
  useEffect(()=>{if(!transcript.busy||busy)return;const timer=setInterval(()=>void load(),2000);return()=>clearInterval(timer);},[transcript.busy,busy,load]);
  useEffect(()=>{tail.current?.scrollIntoView({block:"end",behavior:"smooth"});},[live.length,progress]);
  const locked=busy||transcript.busy;
  async function run(path:string,body:Record<string,unknown>={}){
    if(!detail||locked)return null;setBusy(true);setError("");setNotice("");
    try{const result=await api.post(path,{request_id:crypto.randomUUID(),expected_version:detail.brief.version,...body});await load();return result;}
    catch(e){setError(message(e));return null;}finally{if(mounted.current)setBusy(false);}
  }
  async function act(action:WorkbenchAction){
    if(!detail||locked)return undefined;const result=await run(`/conversations/${id}/actions`,action);return result?.payload;
  }
  async function save(fields:Record<string,unknown>){
    if(!detail||locked)return false;setBusy(true);setError("");try{await api.response(`/conversations/${id}/brief`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_version:detail.brief.version,fields})});await load();return true;}catch(e){setError(message(e));return false;}finally{setBusy(false);}
  }
  async function send(event:FormEvent){event.preventDefault();if(!input.trim()||locked||detail?.conversation_available===false)return;const value=input;setInput("");setBusy(true);sending.current=true;setError("");setLive([]);setProgress("已收到，正在整理…");
    const abort=new AbortController();controller.current=abort;let complete=false;
    setTranscript(t=>({...t,turns:[...t.turns,{id:"pending",message:value,status:"running",events:[]}]}));
    try{const response=await api.response(`/conversations/${id}/chat`,{method:"POST",signal:abort.signal,headers:{"Content-Type":"application/json","Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({message:value})});if(!response.body)throw new Error("助手暂未返回内容");
      for await(const e of warehouseEvents(response.body)){if(abort.signal.aborted)return;if(e.type==="progress")setProgress(String(e.data.message));else if(e.type==="turn_complete")complete=true;else if(e.type==="error")throw new Error(e.data.message||"本轮未完成");else setLive(v=>[...v,e]);}
      if(!complete)throw new Error("连接中断，正在恢复已保存的会话。");
    }catch(e){if(!abort.signal.aborted)setError(message(e));}
    finally{sending.current=false;if(mounted.current){setBusy(false);await load();setLive([]);setProgress("");}}
  }
  const applied=new Set(detail?.records.filter(r=>r.kind==="adoption").map(r=>r.body.proposal_id));
  function renderEvent(e:WarehouseEvent,key:string){
    if(e.type==="text_delta")return <p className="cp-assistant-text" key={key}>{String(e.data.text)}</p>;
    if(e.type!=="ui"||!detail)return null;
    const p=e.data.payload||{},component=e.data.component;
    const stale=p.brief_version!==undefined&&p.brief_version!==detail.brief.version;
    const disabled=locked||stale;
    if(component==="warehouse_routes"||component==="warehouse_departures")return <div key={key} className="cp-card-stack">{stale&&<p className="cp-muted">此前候选 · 操作前请按当前需求重新检索</p>}<ResultCards api={api} payload={p} departures={component==="warehouse_departures"} historical={stale} busy={disabled} act={act} more={act}/></div>;
    if(component==="warehouse_offers")return <OfferCards key={key} payload={p} busy={disabled} act={act} more={act}/>;
    if(component==="warehouse_quote")return <QuoteCard key={key} quote={{...p,snapshot_stale:stale||p.snapshot_stale}}/>;
    if(component==="copilot_proposal")return <section className="cp-panel cp-change" key={key}><span className="cp-eyebrow">需求有新变化</span><h3>先核对，再更新这份跟单</h3>{Object.entries(p.fields||{}).map(([name,value]:[string,any])=><div className="cp-change-row" key={name}><span>{fieldNames[name]||name}</span><b>{fieldText(name,value.value)}</b><small>{value.source==="inferred"?`推断待确认：${value.hint}`:`原话：${value.evidence}`}</small></div>)}<p>{p.impact}</p>{applied.has(p.proposal_id)?<p className="cp-tag">已处理</p>:<div className="cp-row"><button disabled={disabled} className="cp-primary" onClick={()=>run(`/copilot/deals/${id}/proposals/${p.proposal_id}`,{accept:true})}>采纳并更新</button><button disabled={disabled} onClick={()=>run(`/copilot/deals/${id}/proposals/${p.proposal_id}`,{accept:false})}>保留原需求</button></div>}</section>;
    if(component==="copilot_choice")return <section className="cp-panel cp-choice" key={key}><span className="cp-eyebrow">请你决定</span><h3>{p.title}</h3><button className="cp-primary" disabled={disabled} onClick={()=>act({action:p.action,product_id:p.product_id,...(p.offer_id?{offer_id:p.offer_id}:{})})}>{p.action==="departures"?"确认选线 · 查看团期":p.action==="offers"?"确认团期 · 查看方案":"核对结算价"}</button>{stale&&<small>需求已变化，请重新选择。</small>}</section>;
    if(component==="copilot_reply")return <section className="cp-reply" key={key}><div className="cp-section-head"><span>可发给客人的草稿</span><button disabled={stale} onClick={async()=>{try{await copyText(String(p.to_customer));setNotice("草稿已复制，请核对后自行发给客人。");}catch{setError("复制失败，请长按选择文字复制。");}}}>复制文字</button></div><p>{p.to_customer}</p><small>{stale?"基于旧需求，请重新整理后发送":`基于需求 v${p.brief_version} · 由你确认后发送`}</small></section>;
    if(component==="copilot_facts")return <details className="cp-panel" key={key}><summary>{p.comparison?"查看线路比较依据":"查看本轮依据"} · {(p.facts||[]).length} 项</summary>{(p.notice||[]).map((n:string)=><p key={n} className="cp-muted">{n}</p>)}{(p.facts||[]).map((f:any)=><p key={f.fact_id}>{f.text}{!f.reviewed&&<small> · 待商户核实</small>}</p>)}</details>;
    return null;
  }
  if(!detail)return <div className="cp-app"><header className="cp-header"><button onClick={back}>‹ 返回工作台</button></header><main className="cp-home"><div role={error?"alert":"status"} className="cp-empty">{error||"正在恢复这份跟单…"}</div><button onClick={()=>void load()}>重新读取</button></main></div>;
  const title=detail.customer?.body.name||detail.title;
  return <div className="cp-app cp-deal"><header className="cp-header"><button className="cp-back" onClick={back} aria-label="返回工作台">‹</button><div className="cp-brand"><b>{title}</b><span>{detail.title} · 需求 v{detail.brief.version}</span></div><span className="cp-tag">仅自己可见</span></header>
    <nav className="cp-work-tabs" aria-label="跟单功能">{[["chat","沟通搭档"],["need","需求与记忆"],["quote","方案与报价"],["follow","核实与跟进"]].map(([value,label])=><button key={value} aria-current={tab===value?"page":undefined} onClick={()=>setTab(value)}>{label}{value==="follow"&&inquiries.some(i=>i.status==="replied")&&<i/>}</button>)}</nav>
    <main className={`cp-work cp-${tab}`}>
      {error&&<div role="alert" className="cp-error">{error}<button onClick={()=>{setError("");void load();}}>刷新核对</button></div>}{notice&&<p role="status" className="cp-notice">{notice}</p>}
      {detail.conversation_available===false&&<p className="cp-notice">供采权限已变化，这次会话不能继续使用。客户与线下台账仍可管理，请新建跟单重新选线。</p>}
      {tab==="chat"&&<><div className="cp-stage"><span className="cp-dot"/><b>{detail.brief.body.route_id?"已选择线路":"先理解客人的需求"}</b><button onClick={()=>setTab("need")}>查看需求卡 ↗</button></div>
        {transcript.next_cursor&&<button className="cp-history" onClick={async()=>{try{const p=await api.get<Transcript>(`/conversations/${id}?before=${transcript.next_cursor}`);setTranscript(t=>({...t,turns:[...p.turns,...t.turns],next_cursor:p.next_cursor}));}catch(e){setError(message(e));}}}>查看更早的沟通</button>}
        {!transcript.turns.length&&<section className="cp-welcome"><div className="cp-spark" aria-hidden>✧</div><h2>把客人的话，交给我。</h2><p>直接粘贴微信里的需求。我会帮你梳理、记住变化，<br/>再一起找到适合的线路。</p><div className="cp-example">从“想出去放松几天”开始也可以。</div></section>}
        {transcript.turns.map((t:Turn)=><section className="cp-turn" key={t.id}><p className="cp-human">{t.message}</p>{t.events.map((e,n)=>renderEvent(e,`${t.id}-${n}`))}{t.status==="interrupted"&&<p className="cp-error">本轮未完成，已保留原话，请重新发送。</p>}</section>)}
        <div className="cp-live">{live.map((e,n)=>renderEvent(e,`live-${n}`))}</div>{(progress||transcript.busy)&&<p role="status" className="cp-progress">{progress||"正在恢复服务端处理结果…"}</p>}<div ref={tail}/>
        <form className="cp-composer" onSubmit={send}><label className="sr-only" htmlFor="copilot-message">粘贴客人原话或输入问题</label><textarea id="copilot-message" value={input} onChange={e=>setInput(e.target.value)} maxLength={4000} rows={2} placeholder="粘贴客人原话，或告诉搭档你的问题…" disabled={locked}/><div><small>草稿由你核对后发给客人</small><button className="cp-primary" disabled={locked||!input.trim()||detail.conversation_available===false}>{locked?"正在处理…":"交给搭档 ↑"}</button></div></form></>}
      {tab==="need"&&<><section className="cp-panel"><BriefPanel brief={detail.brief} busy={locked} save={save}/></section><section className="cp-panel"><h2>客人记忆与需求演变</h2>{detail.records.filter(r=>["memory","state","proposal","adoption"].includes(r.kind)).map(r=><details key={r.id} className="cp-record"><summary>{recordLabels[r.kind]} · v{r.brief_version} <small>{stamp(r.created_at)}</small></summary>{r.kind==="state"?<><p>{Object.entries(r.body).filter(([k,v])=>fieldNames[k]&&v?.value!==null).map(([k,v])=>`${fieldNames[k]}：${fieldText(k,v.value)}`).join("；")}</p><button disabled={locked||r.brief_version===detail.brief.version} onClick={()=>run(`/copilot/deals/${id}/versions/${r.id}/revert`)}>按此版本重新开始选线</button></>:<p>{r.body.text||r.body.accepted!==undefined?(r.body.text|| (r.body.accepted?"已采纳需求变化":"保留原需求")):"请在沟通页查看对应的变化卡片。"}</p>}</details>)}</section></>}
      {(tab==="quote"||tab==="follow")&&<CopilotBusiness api={api} detail={detail} tab={tab} inquiries={inquiries} busy={locked} run={run} act={act} refresh={load} notify={setNotice} fail={setError}/>}
      {(tab==="need"||tab==="follow")&&<CopilotHistory key={`${id}-${tab}`} api={api} id={id}/>}
    </main>
  </div>;
}
