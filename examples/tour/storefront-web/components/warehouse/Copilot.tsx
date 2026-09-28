"use client";
import {useCallback,useEffect,useMemo,useState,type FormEvent} from "react";
import {WarehouseClient,message,type Organization} from "web-shared/warehouse-client";
import type {Customer,Deal} from "./copilot-types";
import CopilotDeal from "./CopilotDeal";
import CopilotArchive from "./CopilotArchive";
import ShareHistory from "./ShareHistory";
import LegacyCases from "./LegacyCases";
import RoutePreview from "./RoutePreview";
import {stamp} from "@/lib/warehouse";
import "./copilot.css";

export default function Copilot({token,organization,organizations,select,logout}:{token:string;organization:Organization;organizations:Organization[];select:(id:string)=>void;logout:()=>void}) {
  const api=useMemo(()=>new WarehouseClient(token,organization.id),[token,organization.id]);
  const [active,setActive]=useState<string|null>(null),[tab,setTab]=useState("deals");
  const [deals,setDeals]=useState<Deal[]>([]),[customers,setCustomers]=useState<Customer[]>([]),[cursor,setCursor]=useState<string|null>(null);
  const [archiveRoute,setArchiveRoute]=useState<string|null>(null);
  const [query,setQuery]=useState(""),[error,setError]=useState(""),[busy,setBusy]=useState(false),[modal,setModal]=useState<"deal"|"customer"|null>(null);
  const load=useCallback(async(signal?:AbortSignal)=>{
    try {const [d,c]=await Promise.all([api.get<{items:Deal[];next_cursor:string|null}>(`/copilot/deals?query=${encodeURIComponent(query)}`,signal),api.get<{items:Customer[]}>("/copilot/customers?limit=100",signal)]);
      if(!signal?.aborted){setDeals(d.items);setCursor(d.next_cursor);setCustomers(c.items);setError("");}
    } catch(e){if(!signal?.aborted){setError(message(e));setDeals([]);setCustomers([]);}}
  },[api,query]);
  useEffect(()=>{const abort=new AbortController();void load(abort.signal);return()=>abort.abort();},[load]);
  useEffect(()=>{const read=()=>{const id=new URLSearchParams(location.search).get("deal");setActive(id&&/^[0-9a-f-]{36}$/i.test(id)?id:null);};read();window.addEventListener("popstate",read);return()=>window.removeEventListener("popstate",read);},[]);
  function open(id:string|null){setActive(id);history.pushState(null,"",id?`?deal=${id}`:location.pathname);if(!id)void load();}
  async function create(event:FormEvent<HTMLFormElement>){event.preventDefault();const data=new FormData(event.currentTarget);setBusy(true);setError("");try{
    if(modal==="customer")await api.post("/copilot/customers",{request_id:crypto.randomUUID(),customer:{name:String(data.get("name")),contact:String(data.get("contact")||""),note:String(data.get("note")||""),travelers:[]}});
    else {const item=await api.post<Deal>("/copilot/deals",{request_id:crypto.randomUUID(),title:String(data.get("title")),customer_id:data.get("customer")||null});open(item.id);}
    setModal(null);await load();
  }catch(e){setError(message(e));}finally{setBusy(false);}}
  if(active)return <CopilotDeal key={active} api={api} id={active} back={()=>open(null)} />;
  return <div className="cp-app">
    <header className="cp-header"><div className="cp-mark" aria-hidden>旅</div><div className="cp-brand"><b>顾问搭档</b><span>让每一次沟通，更进一步</span></div><button onClick={logout} className="cp-quiet">退出</button></header>
    <main className="cp-home"><section className="cp-hero"><span className="cp-eyebrow">我的专属工作空间</span><h1>客人的下一程，<br/>从这里开始。</h1><p>记住需求，查清细节，把好方案交到客人手上。</p><button className="cp-primary" onClick={()=>setModal("deal")}>＋ 开始一份新跟单</button><div className="cp-hero-lines" aria-hidden><i/><i/><i/></div></section>
      {organizations.length>1&&<label className="cp-org">工作空间<select value={organization.id} onChange={e=>select(e.target.value)}>{organizations.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></label>}
      <div className="cp-section-head"><div className="cp-tabs"><button aria-current={tab==="deals"?"page":undefined} onClick={()=>setTab("deals")}>我的跟单</button><button aria-current={tab==="customers"?"page":undefined} onClick={()=>setTab("customers")}>客户档案</button></div>{tab==="customers"&&<button className="cp-link" onClick={()=>setModal("customer")}>＋ 添加客户</button>}</div>
      {error&&<div role="alert" className="cp-error">{error}<button onClick={()=>void load()}>重新读取</button></div>}
      {tab==="deals"?<><input className="cp-search" aria-label="搜索跟单" placeholder="搜索客户、目的地或跟单名称" value={query} onChange={e=>setQuery(e.target.value)}/><div className="cp-deal-grid">{deals.map(d=><button className="cp-deal-card" key={d.id} onClick={()=>open(d.id)}><div className="cp-card-top"><span className="cp-avatar">{(d.customer_name||d.title).slice(0,1)}</span><span className="cp-tag">{d.brief?.quote_id?"已询价":d.brief?.route_id?"正在选团":"梳理需求"}</span></div><h2>{d.customer_name||d.title}</h2><p>{d.customer_name?d.title:(d.brief?.destinations?.value||[]).join(" · ")||"把客人的需求粘贴进来"}</p><footer><span>v{d.brief_version||0} · {d.updated_at?stamp(d.updated_at):"刚刚创建"}</span><span aria-hidden>↗</span></footer></button>)}</div>{!deals.length&&!error&&<div className="cp-empty"><b>每一位客人，都值得被好好记住。</b><p>新建跟单后，需求、方案和核实回复会保存在这里。</p></div>}{cursor&&<button onClick={async()=>{try{const p=await api.get<{items:Deal[];next_cursor:string|null}>(`/copilot/deals?before=${cursor}&query=${encodeURIComponent(query)}`);setDeals(v=>[...v,...p.items]);setCursor(p.next_cursor);}catch(e){setError(message(e));}}}>加载更多跟单</button>}</>:<div className="cp-customer-grid">{customers.map(c=><article className="cp-panel" key={c.id}><span className="cp-avatar">{c.body.name.slice(0,1)}</span><h2>{c.body.name}</h2><p>{c.body.contact||"未填写联系方式"}</p><p className="cp-muted">{c.body.note||"暂无客户备注"}</p><span className="cp-tag">{c.body.travelers.length} 位已登记旅客</span></article>)}{!customers.length&&<div className="cp-empty">先添加一位客户，再把多次出行跟单关联到同一份档案。</div>}</div>}
      <CopilotArchive api={api} open={open}/><details className="cp-panel"><summary>报价分享管理</summary><ShareHistory api={api}/></details><details className="cp-panel"><summary>已迁入的历史档案</summary><LegacyCases api={api} onCurrent={p=>setArchiveRoute(p.product_id)}/>{archiveRoute&&<RoutePreview api={api} productId={archiveRoute}/>}</details><p className="cp-footnote">客户与成交记录由你独立管理。商户只接收你主动提交的核实内容。</p>
    </main>
    {modal&&<div className="cp-modal-backdrop"><section className="cp-modal" role="dialog" aria-modal="true" aria-label={modal==="deal"?"新建跟单":"添加客户"}><div className="cp-section-head"><h2>{modal==="deal"?"开始新跟单":"添加客户"}</h2><button onClick={()=>setModal(null)} aria-label="关闭">×</button></div><form onSubmit={create}>{modal==="customer"?<><label>客户称呼<input name="name" required maxLength={100} autoFocus/></label><label>联系方式<input name="contact" maxLength={200}/></label><label>备注<textarea name="note" maxLength={2000}/></label></>:<><label>这次旅行怎么称呼<input name="title" placeholder="例如：ACME 家庭的秋日假期" required maxLength={100} autoFocus/></label><label>关联客户<select name="customer"><option value="">暂不关联</option>{customers.map(c=><option value={c.id} key={c.id}>{c.body.name}</option>)}</select></label><p className="cp-muted">一份跟单对应一次出行需求，聊天内容自动保存。</p></>}{error&&<p role="alert" className="cp-error">{error}</p>}<button className="cp-primary" disabled={busy}>{busy?"正在保存…":"创建并继续"}</button></form></section></div>}
  </div>;
}
