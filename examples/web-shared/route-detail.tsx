"use client";

import { useState } from "react";
import styles from "./route-detail.module.css";

type Row = Record<string, any>;
export type KitContent = Row & { schema: "route-kit/1"; title: string; days: Row[] };
export const isRouteKit = (value: any): value is KitContent => value?.schema === "route-kit/1";
export function tidy(value: unknown): string {
  return String(value??"")
  .replace(/<([^<>]{1,40})>/g,"「$1」")
  // "13:30 -- 15:50" is a range; "餐---牛排餐" is a label and its content; "A——B" is a route.
  .replace(/(\d)\s*(?:-{2,}|—{1,}|－{2,}|~{2,})\s*(?=\d)/g,"$1–")
  .replace(/\s*(?:-{3,}|—{3,}|－{3,})\s*/g,"：")
  .replace(/\s*(?:-{2}|—{2}|－{2})\s*/g,"—")
  .replace(/([㐀-鿿」）])\s*,\s*(?=[㐀-鿿「（\d])/g,"$1，")
  .replace(/([㐀-鿿」）])\s*;\s*/g,"$1；")
  .replace(/([㐀-鿿])\s*:\s*(?=\S)/g,"$1：")
  .replace(/\(([^()]*[㐀-鿿][^()]*)\)/g,"（$1）")
  .replace(/[；;，,、：:\s]+$/,"").replace(/^[：:，,、\s]+/,"").replace(/\s{2,}/g," ").trim();
}
const text = (x:any) => tidy(typeof x === "string" ? x : x?.text ?? x?.raw ?? "");
const labels:Record<string,string> = {poi:"景点",group:"观光段",activity:"活动",package:"自费套餐",inside:"入内",outside:"外观",passing:"途经",drive_by:"乘车游览",distant_view:"远眺",walk:"步行游览",included:"已含",excluded:"不含",free_entry:"免费入场",gift:"赠送",optional_paid:"另付费",package_item:"套餐内项目",recommended_not_included:"推荐·不含",shopping:"购物",optional:"自费",recommend:"推荐·不含",transport:"参考交通",free:"自由活动",meet:"集合",photo_spot:"拍照点",notice:"提醒",hotel:"酒店",ship:"船上",flight:"飞机上",train:"列车上",home:"返程到家",self:"自理",not_applicable:"不适用"};
const known = (value:string) => value && value!=="unknown" ? labels[value] ?? value : "";
function Lines({items}:{items:any[]}) { return <>{(items??[]).map((x,i)=><p key={i}>{text(x)}</p>)}</>; }
function Node({item,review,source,onSource}:{item:Row;review:boolean;source:Map<number,Row>;onSource:(ids:number[])=>void}) {
  const cites = item.cite??[];
  const image = cites.some((id:number)=>source.get(id)?.origin==="image");
  return <li className={styles.node}><div className={styles.nodeHead}><h4>{tidy(item.name)}</h4><span>{tidy(item.clock_text)}</span></div>
    <div className={styles.badges}>{[known(item.type),known(item.visit_mode),known(item.ticket),item.type==="recommend"?"推荐·不含":known(item.inclusion),item.highlight?"特别安排":""].filter(Boolean).filter((x,i,a)=>a.indexOf(x)===i).map(x=><span key={x}>{x}</span>)}</div>
    {item.description&&<p>{tidy(item.description)}</p>}{item.duration_text&&<p>参考时长：{tidy(item.duration_text)}</p>}
    {item.transport&&<div className={styles.reference}><p>{[item.transport.from_place,item.transport.to_place].filter(Boolean).join(" → ")}</p><p>{[item.transport.service_no,item.transport.times_text,item.transport.distance_text,item.transport.duration_text].filter(Boolean).map(tidy).join(" · ")}</p>{item.transport.arrival_day_offset!=null&&<small>到达跨日：{item.transport.arrival_day_offset} 天</small>}</div>}
    {!!item.includes?.length&&<p>包含：{item.includes.map(tidy).join("、")}</p>}{item.price_text&&<p>附件费用说明：{tidy(item.price_text)}</p>}{item.min_participants!=null&&<p>该项目最低人数：{item.min_participants}</p>}{item.booking_note&&<p>{tidy(item.booking_note)}</p>}
    {!!item.children?.length&&<ul className={styles.children}>{item.children.map((n:Row,i:number)=><Node key={n.node_id??i} item={n} review={review} source={source} onSource={onSource}/>)}</ul>}
    {item.alternative&&<p className={styles.notice}>替代安排：{tidy(item.alternative.condition)} {tidy(item.alternative.text)}</p>}{item.disclaimer&&<p className={styles.notice}>{tidy(item.disclaimer.text)}</p>}{item.extra_cost_note&&<p className={styles.notice}>{text(item.extra_cost_note)}</p>}
    {review&&<div className={styles.review}><span>整理描述 · 以原文核对为准</span>{image&&<span>图片转写</span>}<button onClick={()=>onSource(cites)}>对照原文</button></div>}
  </li>;
}
export function RouteDetail({content,review=false,termsOnly=false,coverUrl}:{content:Row;review?:boolean;termsOnly?:boolean;coverUrl?:string}) {
  const [index,setIndex]=useState(0),[ids,setIds]=useState<number[]|null>(null);
  const days:Row[]=content.days??[], day=days[Math.min(index,Math.max(days.length-1,0))];
  const source=new Map<number,Row>((content.units??[]).map((u:Row)=>[u.id,u]));
  const select=(i:number)=>{setIndex(i);setIds(null);};
  const fees=[...(content.optional_items??[]),...(content.shopping??[])];
  return <article className={styles.route} aria-label={review?"待审线路内容":"线路行程详情"}>
    {content.publication_notice&&<p className={styles.notice} role="status">{content.publication_notice}</p>}
    {!termsOnly&&<><header className={styles.hero}>{coverUrl&&<img src={coverUrl} alt="线路封面" className={styles.cover}/>}<p className={styles.kicker}>{review?"商户审核 · 待确认内容":"线路详情"}</p><h1>{tidy(content.title||content.listed_name)}</h1>{content.subtitle&&<p>{tidy(content.subtitle)}</p>}<p>{content.days_count} 天{content.nights!=null?` / ${content.nights} 晚`:""} · {(content.countries??[]).join("、")}{content.depart_city?` · ${content.depart_city}出发`:""}</p><div className={styles.badges}>{(content.selling_points??[]).map((x:any,i:number)=><span key={i}>{text(x)}</span>)}{(content.tags??[]).map((x:string)=><span key={x}>{x}</span>)}</div><Lines items={content.cover_facts??[]}/></header>
    {!!content.highlights?.length&&<section><h2>行程亮点</h2>{content.highlights.map((x:Row,i:number)=><div key={i}>{x.title&&<h3>{tidy(x.title)}</h3>}<p>{text(x)}</p></div>)}</section>}
    <section><h2>简要行程</h2><ol className={styles.overview}>{days.map((d,i)=><li key={d.day_id??i}><button onClick={()=>select(i)}><strong>D{d.day}{d.day_end?`–${d.day_end}`:""}　{tidy(d.title)}</strong><span>{tidy(d.summary)}</span></button></li>)}</ol></section>
    <div className={styles.layout}><div><nav className={styles.days} aria-label="选择行程日">{days.map((d,i)=><button key={d.day_id??i} aria-current={index===i?"step":undefined} onClick={()=>select(i)}>D{d.day}{d.day_end?`–${d.day_end}`:""}</button>)}</nav>
    {day?<section className={styles.day}><p className={styles.kicker}>DAY {day.day}{day.day_end?`–${day.day_end}`:""}</p><h2>{tidy(day.title)}</h2><p className={styles.summary}>{tidy(day.summary)}</p>{day.travel_text&&<div className={styles.reference}><strong>每日参考里程与交通</strong><p>{tidy(day.travel_text)}</p></div>}
    <ul className={styles.timeline}>{(day.items??[]).map((n:Row,i:number)=><Node key={n.node_id??i} item={n} review={review} source={source} onSource={setIds}/>)}</ul>
    <div className={styles.meals}>{[["breakfast","早餐"],["lunch","午餐"],["dinner","晚餐"]].map(([k,l])=><div key={k}><h4>{l}</h4><p>{text(day.meals?.[k])||known(day.meals?.[k]?.status)||"待确认"}</p></div>)}</div>
    <div className={styles.reference}><strong>住宿</strong><p>{(day.stay?.names??[]).join(" / ")||known(day.stay?.kind)||"待确认"}{day.stay?.or_similar?" 或同级":""}</p><p>{[day.stay?.city,day.stay?.grade_text,day.stay?.room_type,day.stay?.check_in_text].filter(Boolean).map(tidy).join(" · ")}</p>{day.stay?.consecutive_nights&&<p>连住 {day.stay.consecutive_nights} 晚</p>}</div>
    {day.services&&<p>用车：{known(day.services.coach)||"待确认"} · 导游：{known(day.services.guide)||"待确认"} {tidy(day.services.note)}</p>}{day.port_call&&<p>靠港：{day.port_call.port} · 到港 {day.port_call.arrive_text} · 离港 {day.port_call.depart_text} · 最迟返船 {day.port_call.all_aboard_text}</p>}
    {!!day.notes?.length&&<aside className={styles.notice}><h3>当日提示</h3>{day.notes.map((n:Row,i:number)=><div key={i}><p>{text(n)}</p>{review&&n.auto&&<small>原样挂入 · 待审</small>}</div>)}</aside>}
    {review&&<><button onClick={()=>setIds(day.units??[])}>查看当天原文</button>{ids&&<div className={styles.source}><h3>原文对照</h3>{ids.map(id=><p key={id}><small>#{id}{source.get(id)?.page?` · PDF 第 ${source.get(id)?.page} 页`:""}{source.get(id)?.origin==="image"?" · 图片转写":""}</small><br/>{source.get(id)?.text??"无此证据，请核对"}</p>)}</div>}</>}
    <div className={styles.pager}><button disabled={index===0} onClick={()=>select(index-1)}>上一天</button><span>{index+1} / {days.length}</span><button disabled={index>=days.length-1} onClick={()=>select(index+1)}>下一天</button></div></section>:<p>暂无完整逐日行程，资料待补充。</p>}</div>
    <aside className={styles.sidebar}><h3>费用与安排提示</h3><p>具体团期、实时价格和库存需另行查询。</p>{fees.slice(0,8).map((x:Row,i:number)=><p key={i}>{tidy(x.name)} {tidy(x.price_text)} {tidy(x.note)}</p>)}</aside></div></>}
    {!!content.prices?.length&&<section><h2>附件参考价格</h2><p className={styles.notice}>{content.price_notice||"非实时报价；另付费用不与团费自动合计。"}</p><div className={styles.prices}>{content.prices.map((p:Row,i:number)=><div key={i}><strong>{p.label||"附件价格说明"}</strong><p>{[p.amount,p.currency,p.basis].filter(Boolean).join(" ")}</p><p>{p.text}</p>{p.condition&&<small>{p.condition}</small>}</div>)}</div></section>}
    {[["inclusions","费用包含"],["exclusions","费用不含"]].map(([k,l])=><section key={k}><h2>{l}</h2>{content[k]?.length?<Lines items={content[k]}/>:<p>资料未说明，待确认。</p>}</section>)}
    <section><h2>购物与自费</h2>{[["shopping","购物安排"],["optional_items","自费项目"]].map(([k,l])=><div key={k}><h3>{l}</h3>{content[k]?.length?content[k].map((n:Row,i:number)=><p key={i}>{[n.name,n.price_text,n.duration_text,n.note,(n.categories??[]).join("、")].filter(Boolean).join(" · ")}</p>):<p>资料未列明，不代表承诺没有。</p>}</div>)}</section>
    <section><h2>报名及退改规则</h2>{Object.entries({single_room:"单房差",child:"儿童政策",tips:"服务费",cancellation:"退改规则",deposit:"定金说明"}).map(([k,l])=>content.policies?.[k]?.length?<div key={k}><h3>{l}</h3><Lines items={content.policies[k]}/></div>:null)}{content.applicability?.version_label&&<p>适用版本：{content.applicability.version_label}</p>}{content.applicability?.start&&<p>适用日期：{content.applicability.start} 至 {content.applicability.end}</p>}{!!content.applicability?.departure_cities?.length&&<p>适用出发地：{content.applicability.departure_cities.join("、")}</p>}{content.formation?.minimum_travelers!=null&&<p>最低成团人数：{content.formation.minimum_travelers}</p>}<p>{content.formation?.failure_action} {content.formation?.booking_deadline}</p><p>{content.meeting?.location} {content.meeting?.time} {content.meeting?.domestic_connection}</p>{Object.entries({child:"儿童",senior:"老人",pregnancy:"孕妇",visa:"签证",insurance:"保险"}).map(([k,l])=>{const r=content.traveler_requirements?.[k];return (r&&Object.entries(r).some(([key,value])=>!["cite","raw"].includes(key)&&(typeof value==="string"?!!value.trim():typeof value==="boolean"||typeof value==="number"))||r?.raw)?<div key={k}><h3>{l}</h3><p>{[r.raw||r.conditions,r.bed_policy,r.submission_deadline,r.passport_validity,r.description].filter(Boolean).join("；")}</p>{r.included!=null&&<p>是否包含：{r.included?"是":"否"}</p>}{(r.minimum_age!=null||r.maximum_age!=null)&&<p>年龄要求：{r.minimum_age??"未说明"}–{r.maximum_age??"未说明"} 岁</p>}</div>:null;})}{content.cancellation_tiers?.map((t:Row,i:number)=><p key={i}>出发前 {t.days_before_min??"待确认"}–{t.days_before_max??"待确认"} 天：{t.penalty_percent!=null?`${t.penalty_percent}%`:[t.penalty_money?.amount,t.penalty_money?.currency].filter(Boolean).join(" ")} {t.raw}</p>)}</section>
    {!!content.notices?.length&&<section><h2>温馨提示</h2>{content.notices.map((n:Row,i:number)=><div key={i}><h3>{n.title}</h3>{n.items?.map((x:Row,j:number)=><div key={j}><p>{text(x)}</p>{review&&x.auto&&<small>原样挂入 · 待审</small>}</div>)}</div>)}</section>}
    {review&&<details><summary>解析问题与读取记录</summary>{(content.quality?.issues??[]).map((q:Row,i:number)=><p key={i}>{q.code} · {q.path} · {q.detail}</p>)}<h3>读取时移除的内容</h3><Lines items={content.quality?.removed??[]}/></details>}
  </article>;
}

/** Read-only adapter for historical customer projections; never saved as a new parse. */
export function LegacyRouteDetail({content}:{content:Row}) {
 const notes=(items:any[]) => (items??[]).map(x=>typeof x==="string"?{text:x}:x);
 const fee=(x:Row|undefined,label:string)=>x?.amount!=null?{label,amount:x.amount,currency:x.currency,basis:({person:"人",room:"间",night:"晚"} as Row)[x.unit]??"",text:x.raw??""}:null;
 const converted={...content,days_count:content.summary?.days,nights:content.summary?.nights,depart_city:content.summary?.depart_city,
  cover_facts:[],selling_points:[],highlights:(content.highlights??[]).map((text:string)=>({text})),
  days:(content.days??[]).map((d:Row)=>({...d,travel_text:[d.travel_reference,d.transport].filter(Boolean).join("；"),
    items:[...(d.blocks?.length?d.blocks.map((b:Row)=>({type:({visit:"poi",free_time:"free",programme:"activity"} as Row)[b.type]??b.type,name:b.title||"当天行程",description:(b.paragraphs??[]).join("\n"),visit_mode:b.visit_mode,ticket:b.ticket_status})):d.text?[{type:"activity",name:"当天行程",description:d.text}]:[]),...(d.flights??[]).map((f:Row)=>({type:"transport",name:[f.carrier,f.flight_no].filter(Boolean).join(" ")||"参考航班",transport:{from_place:f.from_place,to_place:f.to_place,times_text:f.times,service_no:f.flight_no,arrival_day_offset:f.arrival_day_offset}}))],
    meals:Object.fromEntries(Object.entries(d.meals??{}).map(([k,v])=>{const m=v as Row;return [k,{text:m.text,status:m.included===true?"included":m.included===false?"self":"unknown"}];})),
    stay:{kind:d.overnight,names:d.hotel?.name?[d.hotel.name]:[],grade_text:d.hotel?.grade,or_similar:d.hotel?.or_similar,room_type:d.hotel?.room_type,consecutive_nights:d.hotel?.consecutive_nights}})),
  inclusions:notes(content.inclusions),exclusions:notes(content.exclusions),notices:(content.notices??[]).length?[{title:"出行提示",items:notes(content.notices)}]:[],
  optional_items:(content.optional??[]).map((n:Row)=>({...n,price_text:n.price})),shopping:(content.shopping??[]).map((n:Row)=>({...n,duration_text:n.duration})),
  policies:Object.fromEntries(Object.entries(content.policies??{}).map(([k,v])=>[k,v?notes(Array.isArray(v)?v:[v]):[]])),
  prices:[fee(content.service_fee,"服务费"),fee(content.single_room_supplement,"单房差")].filter(Boolean),
  traveler_requirements:{...content.traveler_requirements,pregnancy:{raw:content.traveler_requirements?.pregnancy??""}}
 };
 return <RouteDetail content={converted}/>;
}
