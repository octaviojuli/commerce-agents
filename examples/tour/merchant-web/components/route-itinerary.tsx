"use client";
import {RouteDetail,isRouteKit} from "web-shared/route-detail";

import { useState } from "react";
import type { RouteContent } from "./document-content";

type Row = Record<string, any>;
const text = (value: unknown) => typeof value === "string" && value.trim() ? value : "资料未提供";
const list = (value: unknown): any[] => Array.isArray(value) ? value : [];
const included = (value: unknown) => value === true ? "已含" : value === false ? "不含" : "待确认";

export function Flights({items}: {items: Row[]}) {
  return <>{items.map((flight, index) => <p key={index} className="muted">{[flight.carrier,flight.flight_no,flight.from_place,flight.to_place,flight.times].filter(Boolean).join(" · ") || text(flight.raw)}</p>)}</>;
}

export function DailyTravel({day}: {day: Row}) {
  const reference=day.travel_reference || (list(day.distances_km).length ? `${day.distances_km.join(" / ")} 公里（分段对应关系以原文为准）` : "");
  if(!reference && !day.transport && !list(day.flights).length)return null;
  return <aside className="itinerary-travel-card" aria-label="每日参考交通">
    {reference && <div><span>参考里程与车程</span><p>{reference}</p></div>}
    {day.transport && <div><span>交通说明</span><p>{day.transport}</p></div>}
    {list(day.flights).length>0 && <div><span>参考航班</span><Flights items={day.flights}/></div>}
  </aside>;
}

export function RouteItinerary({content, published = true}: {content: RouteContent; published?: boolean}) {
  const [compact,setCompact] = useState(false);
  if(isRouteKit(content))return <RouteDetail content={content} review={!published}/>;
  const days = list(content.days);
  const cover = content.cover ?? {};
  return <div className={`itinerary-reader ${compact ? "is-compact" : ""}`}>
    {content.summary?.days && days.length !== content.summary.days && <p className="notice warning">行程资料不完整：标称 {content.summary.days} 天，当前内容含 {days.length} 个逐日条目。请对照原始附件补充核对；不会自动补写缺少的行程。</p>}
    {list(content.quality?.needs_review).length>0 && <details className="notice warning"><summary>查看解析提示与原始缺项（{content.quality.needs_review.length} 项）</summary><ul>{content.quality.needs_review.map((item:string,index:number)=><li key={index}>{item}</li>)}</ul></details>}
    {content.quality?.resolutions?.length ? <details className="panel"><summary>人工核对说明 · {content.quality.resolutions.length} 项</summary>{content.quality.resolutions.map((r,i)=><p className="document-prose" key={i}>{r.note}</p>)}</details>:null}
    <section className="panel itinerary-overview">
      <div><p className="eyebrow">线路概览</p><h2>{content.name}</h2><p>{content.summary?.days ?? "—"} 天 · {content.summary?.nights == null ? "住宿晚数待确认" : `${content.summary.nights} 晚`} · {text(content.summary?.depart_city)}</p>
        <div className="itinerary-highlights">{list(cover.highlights).map((item,index)=><p key={index}>{text(item)}</p>)}</div>
        <p className="muted">航空公司：{text(cover.airline)}</p><p className="muted">酒店标准：{text(cover.hotel_standard)}</p><p className="muted">用餐标准：{text(cover.meal_standard)}</p>
        {Object.keys(cover.fields ?? {}).length>0 && <details className="itinerary-source-extra"><summary>附件中的补充说明</summary>{Object.entries(cover.fields ?? {}).map(([name,value])=><p key={name}>{name}：{text(value)}</p>)}</details>}
      </div>
      <div><h3>简要行程</h3><ol>{days.map((day,index)=><li key={index}><a href={`#itinerary-day-${index}`}>第 {day.day ?? index+1} 天 · {text(day.title)}</a>{day.summary && day.summary!==day.title && <p className="muted">{day.summary}</p>}</li>)}</ol>{!days.length && <p className="muted">逐日行程尚未提供。</p>}</div>
    </section>
    <div className="row between"><h2>逐日行程</h2><button className="btn" aria-pressed={compact} onClick={()=>setCompact(!compact)}>{compact ? "详细浏览" : "简要浏览"}</button></div>
    <div className="itinerary-layout">
      <nav className="itinerary-daynav" aria-label="逐日导航">{days.map((day,index)=><a key={index} href={`#itinerary-day-${index}`}><strong>D{day.day ?? index+1}</strong><span>{text(day.title)}</span></a>)}</nav>
      <div>{days.map((day,index)=><article className="panel itinerary-day" id={`itinerary-day-${index}`} key={index}>
        <header className="itinerary-dayhead"><span className="itinerary-number">{String(day.day ?? index+1).padStart(2,"0")}</span><div><p className="eyebrow">第 {day.day ?? index+1} 天</p><h2>{text(day.title)}</h2>{day.summary && day.summary!==day.title && <p className="itinerary-brief">{day.summary}</p>}</div></header>
        <DailyTravel day={day}/>
        <section className="itinerary-programme"><h3>当天行程</h3>{list(day.blocks).length ? <div className="itinerary-content-blocks">{day.blocks.map((block:Row,n:number)=><div key={block.block_id||n} className={`itinerary-content-block block-${block.type}`}>{block.type!=="programme" && <p className="eyebrow">{({programme:"行程说明",transport:"参考交通",visit:"游览",free_time:"自由活动",shopping:"购物",optional:"自费",notice:"提醒"} as Row)[block.type]}</p>}{block.title && <h4>{block.title}</h4>}{list(block.paragraphs).map((paragraph:string,j:number)=><p className="document-prose" key={j}>{paragraph}</p>)}</div>)}</div> : <p className="document-prose">{text(day.text)}</p>}
          {!list(day.blocks).length && list(day.sights).length>0 && <div className="itinerary-stops">{day.sights.map((sight:Row,n:number)=><div key={n}><strong>{text(sight.name)}</strong><span className="badge">{text(sight.kind)}</span><p className="muted">门票：{included(sight.ticket_included)}{sight.duration ? ` · ${sight.duration}` : ""}</p></div>)}</div>}
        </section>
        <div className="itinerary-meals">{[["breakfast","早餐"],["lunch","午餐"],["dinner","晚餐"]].map(([key,label])=><div key={key}><span className="muted">{label}</span><strong>{text(day.meals?.[key]?.text)}</strong><small>{included(day.meals?.[key]?.included)}</small></div>)}</div>
        <div className="itinerary-hotel"><strong>当晚住宿</strong><div>{day.hotel ? <><p>{text(day.hotel.name)}{day.hotel.or_similar ? "或同级" : ""}</p><p className="muted">标准：{text(day.hotel.grade)}{day.hotel.room_type?` · ${day.hotel.room_type}`:""}{day.hotel.consecutive_nights?` · 连住 ${day.hotel.consecutive_nights} 晚`:""}</p></> : <p>{({ship:"船上住宿",flight:"航班上过夜",home:"返程到家",hotel:"酒店信息待补充",unknown:"住宿安排待确认"} as Record<string,string>)[day.overnight] ?? "住宿安排待确认"}</p>}</div></div>
      </article>)}</div>
    </div>
    {list(content.transport).length>0 && <section className="panel"><h3>全程参考交通</h3><Flights items={content.transport}/></section>}
    <p className="muted">{published ? "以上为已复核发布的行程内容" : "以上为云仓待审行程，需完成原文事实复核后发布"}；团期日期、价格和库存请进入关联团期查看。{content.source?.attachment_name ? `来源附件：${content.source.attachment_name}。` : "来源：人工整理。"}</p>
  </div>;
}

export function RouteTerms({content}: {content: RouteContent}) {
  if(isRouteKit(content))return <RouteDetail content={content} termsOnly/>;
  const policyLabels: Record<string,string> = {single_room:"单房差规则",child:"儿童规则",visa:"签证",cancellation:"取消与退改",deposit:"定金"};
  return <div className="stack">{[["inclusions","费用包含"],["exclusions","费用不含"],["notices","出行须知"]].map(([key,label])=><section className="panel" key={key}><h2>{label}</h2>{list(content[key]).length ? <ul>{content[key].map((item:string,i:number)=><li className="document-prose" key={i}>{item}</li>)}</ul> : <p className="muted">资料未提供，待确认。</p>}</section>)}
    <section className="panel"><h2>购物与自费</h2>{content.shopping_total!=null && <p>原文标称购物店：{content.shopping_total} 家</p>}{[["shopping","购物"],["optional","自费"]].map(([key,label])=><div key={key}><h3>{label}安排</h3>{list(content[key]).length ? content[key].map((item:Row,i:number)=><p key={i}>{text(item.name)} · {item.day ? `第 ${item.day} 天` : "日期未提供"}{item.categories?.length?` · 经营品类：${item.categories.join("、")}`:""}{item.duration ? ` · ${item.duration}` : ""}{item.price ? ` · ${item.price}` : ""}</p>) : <p className="muted">资料未提供，不代表承诺无{label}。</p>}</div>)}</section>
    <section className="panel"><h2>报名及退改规则</h2>{Object.entries(policyLabels).map(([key,label])=><div key={key}><h3>{label}</h3><p className="document-prose">{text(content.policies?.[key])}</p></div>)}</section>
    {content.applicability && <section className="panel"><h2>行程适用范围</h2><p>{content.applicability.version_label||"未区分版本"} · {content.applicability.start||"开始日期未限定"} — {content.applicability.end||"结束日期未限定"}</p>{content.applicability.departure_cities?.length>0 && <p>出发城市：{content.applicability.departure_cities.join("、")}</p>}</section>}
    {content.formation && <section className="panel"><h2>成团与集合</h2><p>最少成团人数：{content.formation.minimum_travelers??"未提供"}</p><p>{text(content.formation.failure_action)}</p><p>{text(content.formation.booking_deadline)}</p><p>集合地点：{text(content.meeting?.location)} · 时间：{text(content.meeting?.time)}</p><p>国内联运：{text(content.meeting?.domestic_connection)}</p></section>}
    {(content.service_fee||content.single_room_supplement) && <section className="panel"><h2>附件费用说明</h2><p>司导服务费：{money(content.service_fee)}</p><p>单房差：{money(content.single_room_supplement)}</p><p className="muted">以上为附件条款，实际团期价格另行查询。</p></section>}
    {list(content.cancellation_tiers).length>0 && <section className="panel"><h2>退改区间</h2>{content.cancellation_tiers.map((tier:Row,i:number)=><p key={i}>出发前 {tier.days_before_min??"待确认"}–{tier.days_before_max??"待确认"} 天：{tier.penalty_percent!=null?`${tier.penalty_percent}%`:money(tier.penalty_money)}</p>)}</section>}
    {content.traveler_requirements && <section className="panel"><h2>人群与证件要求</h2>{["child","senior"].map(key=><p key={key}>{{child:"儿童",senior:"老人"}[key]}：{text(content.traveler_requirements[key]?.conditions)} {content.traveler_requirements[key]?.bed_policy||""}</p>)}<p>孕妇限制：{text(typeof content.traveler_requirements.pregnancy==="string"?content.traveler_requirements.pregnancy:content.traveler_requirements.pregnancy?.raw)}</p><p>送签说明：{text(content.traveler_requirements.visa?.submission_deadline)}</p><p>护照要求：{text(content.traveler_requirements.visa?.passport_validity)}</p><p>保险：{text(content.traveler_requirements.insurance?.description)}</p></section>}
  </div>;
}

function money(value:Row|undefined){if(value?.amount==null)return "金额未提供";const unit=({person:"人",room:"间",night:"晚"} as Record<string,string>)[value.unit];return `${value.amount} ${value.currency||"币种待确认"}${unit?` / ${unit}`:"（计费单位待确认）"}`;}
