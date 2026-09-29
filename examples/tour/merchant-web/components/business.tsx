"use client";
import { useEffect, useState, type ReactNode } from "react";
import { WarehouseClient, type Connection, type Listing, message, money } from "../lib/api";
import { Field, LoadState } from "./common";
import { BusinessLink as Link, navigate, scoped, useListPosition } from "./business-navigation";
import { ProductDisplay } from "./product-display";
import { ProductDocuments } from "./documents";
import { type RouteContent } from "./document-content";
import { RouteItinerary, RouteTerms } from "./route-itinerary";
import { RouteEditor, RouteTags } from "./route-editor";
import { SalesEditor } from "./views";
import { OfferManager } from "./offers";

// Permission rechecks keep the current layout while pending, then clear failed reads.
function useData<T>(api:WarehouseClient,path:string,revision=0) {
  const [state,setState]=useState<{api?:WarehouseClient;path?:string;data?:T;error?:string}>({});
  useEffect(()=>{const cancel=new AbortController();
    api.get<T>(path,cancel.signal).then(data=>{if(!cancel.signal.aborted)setState({api,path,data});}).catch(error=>{if(!cancel.signal.aborted)setState({api,path,error:message(error)});});
    return ()=>cancel.abort();
  },[api,path,revision]);
  const current=state.api===api && state.path===path ? state : {};
  return {...current,loading:!current.data && !current.error};
}

type Row = Record<string, any>;
type Page = {items:Row[];total:number;page:number;limit:number};
type Props={api:WarehouseClient;location:string;revision:number;writable:boolean;documents:boolean;orders:boolean;publishable?:boolean;onProposed:()=>void;onPublished?:()=>void};
const label:Record<string,string>={routes:"线路",departures:"团期",orders:"订单"};
const statuses:Record<string,string>={published:"上架",paused:"下架",draft:"草稿",archived:"归档"};
const empty="上游未提供";
function value(v:unknown):string {return v==null || v==="" ? empty : Array.isArray(v) ? v.map(value).join("、") : typeof v==="object" ? Object.values(v as object).map(value).join(" · ") : String(v);}
function stamp(v:string|null|undefined){return v ? new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}).format(new Date(v)) : "尚无记录";}
function Info({items}:{items:[string,ReactNode][]}) {return <dl className="business-facts">{items.map(([name,detail])=><div key={name}><dt>{name}</dt><dd>{detail ?? empty}</dd></div>)}</dl>;}
function Status({text}:{text:string}){return <span className="badge">{text}</span>;}
function numberPrice(amount:unknown,currency?:string) {return amount==null || amount==="" ? "未提供" : currency ? money(Number(amount),currency) : `${amount}（币种待确认）`;}
function pager(data:Page,params:URLSearchParams,base:string,org:string){
  const pages=Math.max(1,Math.ceil(data.total/data.limit));
  const href=(page:number)=>{const next=new URLSearchParams(params);next.set("page",String(page));return scoped(`${base}?${next}`,org);};
  return <div className="row between business-pagination"><span className="muted">共 {data.total} 条 · 第 {data.page} / {pages} 页</span><div className="row">{data.page>1 && <Link className="btn" href={href(data.page-1)}>上一页</Link>}{data.page<pages && <Link className="btn" href={href(data.page+1)}>下一页</Link>}</div></div>;
}
export function Business(props:Props) {
  const [recheck,setRecheck]=useState(0);
  useEffect(()=>{const check=()=>setRecheck(n=>n+1);const timer=setInterval(check,30000);window.addEventListener("focus",check);return ()=>{clearInterval(timer);window.removeEventListener("focus",check);};},[]);
  props={...props,revision:props.revision+recheck};
  const url=new URL(props.location,"https://warehouse.invalid"), [module,id]=url.pathname.split("/").filter(Boolean);
  useEffect(()=>{document.querySelector(".portal-main")?.scrollTo(0,0);},[props.location]);
  if(module==="orders" && !props.orders) return <p className="notice">当前角色没有订单及财务信息读取权限。</p>;
  return id ? <Detail key={props.location} {...props} module={module} id={id} params={url.searchParams}/> : <List key={props.location} {...props} module={module} params={url.searchParams}/>;
}
function List(props:Props & {module:string;params:URLSearchParams}) {
  const {api,module,params,location,revision}=props;
  const sources=useData<{items:Connection[]}>(api,"/merchant/connections",revision);
  if(module==="orders") return <Orders {...props} sources={sources.data?.items ?? []}/>;
  return <CatalogList {...props} sources={sources.data?.items ?? []}/>;
}
function CatalogList({api,module,params,location,revision,sources}:Props & {module:string;params:URLSearchParams;sources:Connection[]}) {
  const query=new URLSearchParams(params);query.delete("org");query.delete("return");
  const state=useData<Page>(api,`/merchant/${module}?${query}`,revision);
  useListPosition(location,!!state.data);
  const href=(path:string)=>scoped(path,api.organization);
  const detail=(row:Row)=>href(`/${module}/${row.id}?return=${encodeURIComponent(location)}`);
  return <>
    <div className="business-intro"><p>{module==="routes" ? "维护线路内容与发布状态，团期价格和库存由独立团期管理。" : "按团号管理出发计划、价格方案与库存观察。"}</p><span className="badge good">{state.data?.total ?? "—"} {module==="routes"?"条线路":"个团期"}</span></div>
    {sources.some(s=>s.capabilities.catalog_selection) && <p className="notice">首批展示范围：30 条线路、200 个团期。散拼分类待业务复核；其余记录已停止展示并保留历史。</p>}
    <form className="panel business-filters" onSubmit={e=>{e.preventDefault();const form=new FormData(e.currentTarget),next=new URLSearchParams(params);next.delete("page");for(const [k,v] of form.entries())v?next.set(k,String(v)):next.delete(k);navigate(href(`/${module}?${next}`));}}>
      <Field label={module==="routes"?"线路编号 / 名称":"团号 / 线路编号 / 名称"}><input className="input" name="query" defaultValue={params.get("query") ?? ""} placeholder="输入编号或名称"/></Field>
      <Field label="数据来源"><select className="input" name="connection_id" defaultValue={params.get("connection_id") ?? ""}><option value="">全部来源</option>{sources.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
      {module==="routes" ? <Field label="发布状态"><select className="input" name="status" defaultValue={params.get("status") ?? ""}><option value="">全部状态</option>{Object.entries(statuses).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></Field> : <><Field label="出发日期起"><input className="input" type="date" name="start" defaultValue={params.get("start") ?? ""}/></Field><Field label="出发日期止"><input className="input" type="date" name="end" defaultValue={params.get("end") ?? ""}/></Field></>}
      <button className="btn primary">查询</button><Link className="btn" href={href(`/${module}`)}>重置</Link>
    </form>
    {params.get("product_id") && <p className="notice row">已限定所选线路的团期 <Link href={href(`/routes/${params.get("product_id")}`)}>查看所属线路</Link><Link href={href("/departures")}>清除线路筛选</Link></p>}
    <LoadState {...state}/>
    {module==="routes" && <FactConflicts api={api} revision={revision}/>}
    {state.data && <section className="panel business-table"><div className="table-wrap"><table><thead><tr>{(module==="routes"?["线路编号 / 标题","出发地 / 天数","参考价格","发布状态","关联团期","更新时间","操作"]:["团号 / 线路","出发 / 返回","销售规则","可售余位观察","库存来源 / 更新时间","操作"]).map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>
      {state.data.items.map(row=><tr key={row.id}>{module==="routes"?<>
        <td><p className="business-code">{row.code}</p><Link id={`record-${row.id}`} href={detail(row)}>{row.name}</Link><p className="muted">{row.source_name}</p></td>
        <td>{value(row.gateway)}<br/>{row.days ?? "—"} 天{(row.days_origin==="warehouse_decision"||row.gateway_origin==="warehouse_decision")&&<p className="muted" title={`上游登记：${row.source_gateway ?? "—"} · ${row.source_days ?? "—"} 天`}>云仓核定</p>}</td><td>{numberPrice(row.reference_price,row.currency)}<p className="muted">上游起价，非成交价</p></td><td><Status text={statuses[row.status] ?? row.status}/></td><td><Link href={href(`/departures?product_id=${row.id}`)}>{row.departure_count} 个团期</Link><p className="muted">最近 {row.next_departure ?? "未排期"}</p></td><td>{stamp(row.observed_at)}</td><td><Link id={`detail-${row.id}`} href={detail(row)}>线路详情 →</Link></td>
      </>:<>
        <td><Link id={`record-${row.id}`} href={detail(row)}>{row.code}</Link><p>{row.route_name}</p><p className="muted">{row.route_code}</p></td><td>{row.depart_date}<br/><span className="muted">{row.return_date}</span></td><td><Status text={row.sales_status_label}/></td><td><strong className="business-number">{row.observed_available ?? "—"}</strong><p className="muted">{!row.inventory_fresh?"已过期，需重新读取":row.observed_available===0?"无可售余位":"以上游观察为准"}</p></td><td>{row.inventory_authority}<p className="muted">{stamp(row.observed_at)}</p></td><td><Link id={`detail-${row.id}`} href={detail(row)}>团期详情 →</Link></td>
      </>}</tr>)}
    </tbody></table></div>{!state.data.items.length && <p className="empty">没有符合条件的{label[module]}。</p>}{pager(state.data,params,`/${module}`,api.organization)}</section>}
  </>;
}
function Detail(props:Props & {module:string;id:string;params:URLSearchParams}) {
  const {api,module,id,params,revision}=props;
  const query=new URLSearchParams();for(const name of ["connection_id","company_id"])if(params.get(name))query.set(name,params.get(name)!);
  const state=useData<Row>(api,`/merchant/${module==="orders"?"external-orders":module}/${id}?${query}`,revision);
  const row=state.data;
  const candidate=params.get("return") ?? `/${module}`;
  const returnUrl=candidate.split("?")[0]===`/${module}` ? scoped(candidate,api.organization) : scoped(`/${module}`,api.organization);
  return <><header className="business-detail-head"><div><Link href={returnUrl}>← 返回{label[module]}列表</Link><p className="eyebrow">{label[module]}详情{module==="orders"?" · B2B 只读":""}</p><h2>{row?.name ?? row?.code ?? row?.order?.orderNo ?? "正在读取…"}</h2>{row?.code && module==="routes" && <p className="business-code">{row.code}</p>}</div>{row && <div className="row">{module==="routes" && <Link className="btn primary" href={scoped(`/departures?product_id=${id}`,api.organization)}>查看关联团期</Link>}{module==="departures" && <><Link className="btn" href={scoped(`/routes/${row.product_id}`,api.organization)}>查看所属线路</Link>{props.orders && <Link className="btn primary" href={scoped(`/orders?connection_id=${row.connection_id}&company_id=${row.company_id ?? ""}&departure_id=${row.external_id}`,api.organization)}>查看已有订单</Link>}</>}</div>}</header><LoadState {...state}/>{row && (module==="routes"?<RouteDetail {...props} row={row}/>:module==="departures"?<DepartureDetail {...props} row={row}/>:<OrderDetail row={row} api={api}/>)}</>;
}
function RouteDetail({api,row,writable,documents,publishable,onProposed,onPublished,revision}:Props & {row:Row}) {
  const listing:Listing={listing_id:row.id,title:row.name,status:row.status,price:null,currency:"XXX",stock:null,attributes:{},long_description:row.description};
  const [tab,setTab]=useState("content");
  const itinerary = row.document ?? (documents ? row.draft_document : null);
  return <><Info items={[["线路编号",row.code],["发布状态",statuses[row.status]],["出发地 / 天数",`${row.gateway ?? "待核实"} · ${row.days ?? "—"} 天${row.days_origin==="warehouse_decision"||row.gateway_origin==="warehouse_decision"?`（云仓核定；上游登记 ${row.source_gateway ?? "—"} · ${row.source_days ?? "—"} 天）`:""}`],["来源",row.source_name],["参考价（上游起价）",numberPrice(row.reference_price,row.currency)],["观察时间",stamp(row.observed_at)]]}/><nav className="business-tabs" aria-label="线路详情分区">{[["content","行程内容"],["terms","费用与须知"],["assets","图片与附件"],["edit","内容维护与记录"]].map(([key,name])=><button className={tab===key?"active":""} key={key} onClick={()=>setTab(key)}>{name}</button>)}</nav>
    {tab==="content" && <>{row.description && <section className="panel"><h2>线路简介</h2><p className="document-prose">{row.description}</p></section>}<RouteTags api={api} id={row.id} writable={writable} onProposed={onProposed}/>{documents ? <RouteEditor api={api} id={row.id} writable={writable} publishable={publishable} onProposed={onProposed} onPublished={onPublished}/> : itinerary ? <RouteItinerary content={itinerary.body as RouteContent} published/> : <p className="notice">尚无已发布行程。</p>}</>}
    {tab==="terms" && <section className="panel stack"><h2>费用与须知</h2>{itinerary ? <>{!row.document && <p className="notice warning">以下费用与规则来自机器解析稿，尚未审核发布，不代表正式承诺。</p>}<RouteTerms content={itinerary.body as RouteContent}/></> : <p className="empty">尚无已复核的费用包含、不含及注意事项；不以团期报价替代线路条款。</p>}</section>}
    {tab==="assets" && <>{row.image_url && /^https:\/\//.test(row.image_url) && <section className="panel"><img className="business-cover" src={row.image_url} alt={`${row.name} · 上游线路图片`} referrerPolicy="no-referrer" loading="lazy"/></section>}{documents ? <ProductDocuments key={`${row.id}:${revision}`} api={api} product={listing} writable={writable} onProposed={onProposed}/> : <p className="notice">当前角色没有文档读取权限。</p>}</>}
    {tab==="edit" && <>{row.connector_type==="excel" && writable && <ExcelContent api={api} row={row} onProposed={onProposed}/>}{row.connector_type!=="excel" && <ProductDisplay api={api} id={row.id} writable={writable} onProposed={onProposed}/>}<section className="panel"><Info items={[["来源版本",row.version],["展示修订版本",row.display_version],["名称来源",row.name_origin==="source"?"上游原始资料":"云仓审批补充"],["介绍来源",row.description_origin==="source"?"来源内容":"云仓审批补充"],["内容发布",row.document?`文档版本 ${row.document.product_version} · ${stamp(row.document.created_at)}`:"尚无复核发布记录"]]}/></section></>}
  </>;
}
function DepartureDetail({api,row,writable,onProposed,orders}:Props & {row:Row}) {
  const [read,setRead]=useState(1),[sales,setSales]=useState(false),[offers,setOffers]=useState(false);
  return <><Info items={[["所属线路",row.route_name],["线路编号",row.route_code],["出发 / 返回",`${row.depart_date} — ${row.return_date}`],["销售规则",row.sales_status_label],["所属部门",row.department_name ?? row.company_id],["库存权威",row.inventory_authority]]}/>
    <section className="panel stack"><div className="row between"><h2>库存明细</h2>{row.connector_type==="tour_b2b" && <button className="btn" onClick={()=>setRead(n=>n+1)}>刷新最新库存与价格</button>}</div><p className="muted">占位、预留、确认分别保留上游原值，不通过可见订单反推余位；取消不作为当前库存余额。</p><details open={row.connector_type==="excel"}><summary>最近一次云仓同步库存 · {stamp(row.observed_at)}</summary><Info items={row.pool_id ? [["总量",row.total],["已售",row.sold],["占位",row.held],["停售量",row.blocked],["可用",row.observed_available]] : [["计划人数",row.plan_guests],["可售余位观察",row.observed_available],["确认",row.confirm_count],["预留",row.reserve_count],["占位",row.placeholder_count],["候补",row.waitlist_count]]}/><p className="notice">观察于 {stamp(row.observed_at)} · {row.inventory_fresh?"观察有效":"库存观察已过期，请读取上游最新信息"}。最低成团人数：{value(row.min_group_size)}；上游成团结论未提供。</p></details>{row.connector_type==="tour_b2b" && <Observation api={api} id={row.id} revision={read}/>}</section>
    <section className="panel stack"><div className="row between"><h2>报价方案与价格矩阵</h2><button className="btn" onClick={()=>setOffers(!offers)}>管理报价方案</button></div><p className="muted">同行价与结算价统一称为“同行结算价”。API 团期在上方实时读取；Excel 团期在下方展示已发布的标准价表。</p>{row.offers.map((offer:Row)=><div key={offer.id}><h3>{offer.name} · {offer.active?"启用":"停用"}</h3>{row.connector_type==="excel" && <PriceMatrix schedule={offer.schedule}/>}</div>)}{!row.offers.length && <p className="empty">尚无报价方案。</p>}{offers && <OfferManager api={api} departure={row.id} writable={writable} onProposed={onProposed} onClose={()=>setOffers(false)}/>}</section>
    <section className="panel stack"><div className="row between"><h2>销售规则</h2>{writable && <button className="btn" onClick={()=>setSales(!sales)}>调整销售规则</button>}</div><p>{row.sales_status_label}</p><p className="muted">云仓报名截止：{row.local_booking_deadline ? new Date(row.local_booking_deadline).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"})+"（北京时间）" : "未设置"}。预留时长不作为报名截止。</p>{sales && <SalesEditor api={api} id={row.id} onProposed={onProposed} onClose={()=>setSales(false)}/>}</section>
  </>;
}
function PriceMatrix({schedule}:{schedule:Row|null}) {return <div className="table-wrap"><table><thead><tr><th>计价项</th><th>市场价</th><th>同行结算价</th></tr></thead><tbody>{[["adult","成人"],["child","儿童"],["senior","老人"],["single_room","单房差"]].map(([key,name])=><tr key={key}><td>{name}</td><td>{numberPrice(schedule?.market?.[key],schedule?.currency)}</td><td>{numberPrice(schedule?.settlement?.[key],schedule?.currency)}</td></tr>)}</tbody></table>{!schedule && <p className="muted">尚无已发布的标准价表，请核对供应源配置。</p>}</div>;}
function Observation({api,id,revision}:{api:WarehouseClient;id:string;revision:number}) {
  const path=`/merchant/departures/${id}/observation`;
  const state=useData<Row>(api,path,revision);
  const [pending,setPending]=useState(true);
  // The request lifecycle is scoped by useData's abort guard. Do not poll the supplier.
  useEffect(()=>setPending(true),[path,revision]);
  useEffect(()=>{if(state.data || state.error)setPending(false);},[state.data,state.error]);
  const price=(info:Row|undefined|null,key:string)=>info?.[key]===0 && key!=="singleRoomDiff" ? "上游返回 0 · 待确认" : numberPrice(info?.[key],info?.currency);
  return <section className="business-observation stack"><h3>上游即时库存与价格</h3>
    <p className="muted">进入团期详情时自动查询一次；同步观察与即时查询分别保留时间，不改写上游库存。</p>
    {pending && <p role="status">正在刷新上游库存与价格…</p>}<LoadState {...state}/>
    {state.error && <p className="notice warning">本次刷新失败；上方同步库存保留原观察时间，不代表本次查询结果。</p>}
    {state.data && <><p className="muted">{pending?"上次成功查询":"查询完成"}：{stamp(state.data.observed_at)}（北京时间）</p><Info items={[["计划人数",state.data.inventory.planGuests],["可售余位",state.data.inventory.availableSeats],["确认",state.data.inventory.confirmCount],["预留",state.data.inventory.reserveCount],["占位",state.data.inventory.placeholderCount],["候补",state.data.inventory.waitlistCount]]}/>
      <div className="table-wrap"><table><thead><tr><th>计价项</th><th>市场价</th><th>同行结算价</th></tr></thead><tbody>{[["adultPrice","成人"],["childPrice","儿童"],["elderPrice","老人"],["singleRoomDiff","单房差"]].map(([key,name])=><tr key={key}><td>{name}</td><td>{price(state.data!.market_prices,key)}</td><td>{state.data!.settlement_status==="ready"?price(state.data!.settlement_prices,key):"待查询"}</td></tr>)}</tbody></table></div>
      {state.data.settlement_status!=="ready" && <p className="notice">{state.data.settlement_status==="type_unconfirmed" ? `上游返回价格类型“${state.data.source_price_type ?? "未提供"}”，暂不能确认为同行结算价。` : state.data.settlement_status==="source_error" ? `同行结算价读取失败（${state.data.settlement_error}），请重试。` : "当前来源尚未配置统一结算价，请联系来源管理员完成配置。"}</p>}
      {state.data.price_scope==="uniform" && <p className="muted">同一团期与报价方案，所有获授权采购客户使用统一同行结算价。</p>}
      <p className="muted">即时查询不代表占位或下单成功。上游零值与缺失分别展示，儿童与老人价格不沿用成人价格。</p>
    </>}
  </section>;
}

function Orders(props:Props & {module:string;params:URLSearchParams;sources:Connection[]}) {
  const {api,params,sources}=props;
  const source=params.get("connection_id") || sources.find(s=>s.connector_type==="tour_b2b" && s.active)?.id;
  return <><p className="notice">B2B 已有订单，只读。按指定部门实时读取；仅代表当前接口账号的可见范围。不会创建订单、占位、取消或扣减库存。</p><form className="panel row" onSubmit={e=>{e.preventDefault();const form=new FormData(e.currentTarget);navigate(scoped(`/orders?connection_id=${form.get("connection_id")}`,api.organization));}}><Field label="订单数据来源"><select className="input" name="connection_id" defaultValue={source} key={source}>{sources.filter(s=>s.connector_type==="tour_b2b" && s.active).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></Field><button className="btn">选择来源</button></form>{source ? <OrderDepartments key={source} {...props} source={source}/> : <p className="empty">尚无可读取订单的 B2B 来源。</p>}</>;
}
function OrderDepartments(props:Props & {params:URLSearchParams;source:string}) {
  const {api,source,params}=props;
  const state=useData<Row>(api,`/merchant/external-orders/departments?connection_id=${source}`,props.revision);
  const company=params.get("company_id") || state.data?.departments[0];
  return <><LoadState {...state}/>{state.data && <><form className="panel business-filters" onSubmit={e=>{e.preventDefault();const form=new FormData(e.currentTarget),next=new URLSearchParams(params);next.set("connection_id",source);next.delete("page");for(const [k,v] of form.entries())v?next.set(k,String(v)):next.delete(k);navigate(scoped(`/orders?${next}`,api.organization));}}><Field label="查询部门"><select className="input" name="company_id" defaultValue={company}>{state.data.departments.map((id:string)=><option key={id} value={id}>部门 {id}</option>)}</select></Field><Field label="订单号"><input className="input" name="query" defaultValue={params.get("query") ?? ""} placeholder="输入订单号"/></Field><button className="btn primary">查询订单</button><Link className="btn" href={scoped(`/orders?connection_id=${source}`,api.organization)}>重置</Link></form><p className="muted">可读取 {state.data.departments.length} 个部门；以下仅显示部门 {company}。结算方式及历史单价缺失时保留未知。</p>{params.get("departure_id") && <p className="notice">已限定所选团期的订单。<Link href={scoped(`/orders?connection_id=${source}&company_id=${company}`,api.organization)}>清除团期筛选</Link></p>}{company ? <OrderList {...props} company={company}/> : <p className="empty">来源没有与已配置范围匹配的可读部门。</p>}</>}</>;
}
function OrderList({api,source,company,params,location,revision}:Props & {source:string;company:string;params:URLSearchParams}) {
  const query=new URLSearchParams({connection_id:source,company_id:company,page:params.get("page") || "1",query:params.get("query") || ""});if(params.get("departure_id"))query.set("departure_id",params.get("departure_id")!);
  const state=useData<Page & Row>(api,`/merchant/external-orders?${query}`,revision);
  useListPosition(location,!!state.data);
  return <><LoadState {...state}/>{state.data && <section className="panel business-table"><div className="table-wrap"><table><thead><tr>{["订单号","下单单位","团号 / 线路","订单总额","业务状态","结算方式","操作"].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{state.data.items.map(row=>{const link=scoped(`/orders/${row.orderId}?connection_id=${source}&company_id=${company}&return=${encodeURIComponent(location)}`,api.organization);return <tr key={row.orderId}><td><Link id={`record-${row.orderId}`} href={link}>{row.orderNo}</Link></td><td>{value(row.customerName)}</td><td>{value(row.periodCode)}<p className="muted">{value(row.routeName)}</p></td><td>{numberPrice(row.totalAmount,row.currency)}</td><td>{value(row.orderStatusText ?? row.orderStatus)}</td><td>{value(row.settlementType)}</td><td><Link id={`detail-${row.orderId}`} href={link}>订单详情 →</Link></td></tr>;})}</tbody></table></div>{!state.data.items.length && <div className="empty"><h3>当前查询范围没有已有订单</h3><p>部门 {company} 的接口返回 0 条记录。其他部门可切换后查询。</p></div>}{pager(state.data,query,"/orders",api.organization)}<p className="muted">读取于 {stamp(state.data.observed_at)} · {state.data.source_name} · 本次读取完成</p></section>}</>;
}
function OrderDetail({row,api}:{row:Row;api:WarehouseClient}) {const o=row.order;return <><section className="panel stack"><h2>订单摘要</h2>{o.warehouse_departure_id ? <div className="row"><Link href={scoped(`/departures/${o.warehouse_departure_id}`,api.organization)}>查看当前团期</Link><Link href={scoped(`/routes/${o.warehouse_route_id}`,api.organization)}>查看当前线路</Link></div> : <p className="muted">历史团期待关联或不在本次展示范围；以下保留订单上游原始信息。</p>}<Info items={[["订单号",o.orderNo],["下单单位",o.customerName],["团号",o.periodCode],["线路",o.routeName],["出发 / 返回",`${value(o.departDate)} / ${value(o.returnDate)}`],["原始业务状态",value(o.orderStatusText ?? o.orderStatus)],["下单时间",value(o.createTime)]]}/></section><section className="panel stack"><h2>人数与成交明细</h2><Info items={[["成人数量",o.adultCount],["儿童数量",o.childCount],["老人数量",o.elderCount],["历史单价",empty],["原始金额",numberPrice(o.originalAmount,o.currency)],["调整金额",numberPrice(o.adjustAmount,o.currency)],["订单总额",numberPrice(o.totalAmount,o.currency)]]}/><p className="muted">接口未提供历史单价时，不用总额反算，也不以当前报价补齐。</p></section><section className="panel stack"><h2>收款与结算</h2><Info items={[["结算方式",value(o.settlementType)],["已收",numberPrice(o.receivedAmount,o.currency)],["未收",numberPrice(o.unreceivedAmount,o.currency)],["已退",numberPrice(o.refundedAmount,o.currency)],["来源 / 部门",`${row.source_name} / ${row.department}`],["本次观察时间",stamp(row.observed_at)]]}/></section></>;}

function ExcelContent({api,row,onProposed}:{api:WarehouseClient;row:Row;onProposed:()=>void}) {
  const [error,setError]=useState(""),[busy,setBusy]=useState(false);
  return <form className="panel stack" onSubmit={async e=>{e.preventDefault();setBusy(true);setError("");const data=new FormData(e.currentTarget);try{await api.post("/merchant/content/proposals",{listing_id:row.id,title:data.get("title"),long_description:data.get("description"),note:data.get("note")||null});onProposed();}catch(e){setError(message(e));}finally{setBusy(false);}}}>
    <h2>维护 Excel 线路内容</h2><Field label="线路标题"><input name="title" className="input" defaultValue={row.name} required maxLength={300}/></Field><Field label="线路介绍"><textarea name="description" className="input" defaultValue={row.description ?? ""} maxLength={10000}/></Field><Field label="修改说明"><input name="note" className="input" maxLength={200}/></Field>{error && <p role="alert" className="notice error">{error}</p>}<button className="btn primary" disabled={busy}>{busy?"提交中…":"生成修改预览"}</button>
  </form>;
}

function FactConflicts({api,revision}:{api:WarehouseClient;revision:number}) {
  const state=useData<{items:Row[]}>(api,"/merchant/product-fact-conflicts",revision);
  const items=state.data?.items ?? [];
  if(!items.length) return null;
  const sheet=()=>["线路编号\t线路\t字段\t上游当前值\t云仓核定值\t状态\t依据",...items.map(i=>[i.code,i.name,i.label,i.upstream,i.decided,i.state==="stale"?"上游已改为其他值":"上游未更正",(i.basis as Row[]).map(b=>`${b.source}:${b.value}`).join("；")].join("\t"))].join("\n");
  return <details className="panel"><summary>上游数据待更正 · {items.length} 项</summary><p className="muted">以下线路的天数或出发口岸，云仓与上游登记不一致。云仓不改上游数据；请把清单交给上游或 ERP 侧更正，更正后核定自动失效。</p><div className="table-wrap"><table><thead><tr><th>线路</th><th>字段</th><th>上游当前值</th><th>云仓核定值</th><th>状态</th></tr></thead><tbody>{items.map(i=><tr key={`${i.product_id}:${i.field}`}><td>{i.code}<p className="muted">{i.name}</p></td><td>{i.label}</td><td>{String(i.upstream ?? "—")}</td><td>{String(i.decided)}</td><td>{i.state==="stale"?"上游已改为其他值，核定已失效":"上游未更正"}</td></tr>)}</tbody></table></div><button className="btn" onClick={()=>navigator.clipboard?.writeText(sheet())}>复制清单（可粘贴到表格）</button></details>;
}
