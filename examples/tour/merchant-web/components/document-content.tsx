"use client";
import {RouteDetail,isRouteKit} from "web-shared/route-detail";

import { Field } from "./common";

export type RouteContent = Record<string, any> & {
  name: string;
  summary: { days: number; nights: number | null; [key: string]: any };
  days: Record<string, any>[];
  source: { attachment_name: string; etag: string; parser: string; page_locations?: PageLocation[]; variants?: {label:string;start_line:number;end_line:number}[] };
  quality: { completeness: number; needs_review: string[]; reviewed_by: string; resolutions?: {code:string;path:string;basis_hash:string;note:string}[] };
};
export type PageLocation = {section:"cover"|"itinerary"|"terms";day:number|null;pages:number[]};
export const documentLabels: Record<string, string> = {
  applicability:"行程适用范围", start:"适用开始日期", end:"适用结束日期", departure_cities:"适用出发城市", version_label:"原文版本标签",
  formation:"成团与收客", minimum_travelers:"最少成团人数", failure_action:"不成团处理", booking_deadline:"截止收客说明",
  service_fee:"司导服务费", single_room_supplement:"单房差", money:"结构化费用", amount:"金额", currency:"币种", unit:"计费单位",
  cancellation_tiers:"退改区间", days_before_min:"出发前最少天数", days_before_max:"出发前最多天数", penalty_percent:"收取比例（%）", penalty_money:"收取金额",
  traveler_requirements:"人群与证件要求", senior:"老人要求", pregnancy:"孕妇限制", minimum_age:"最低年龄", maximum_age:"最高年龄", bed_policy:"占床说明", conditions:"附加条件", submission_deadline:"送签截止说明", passport_validity:"护照有效期", insurance:"保险", description:"说明",
  meeting:"集合与联运", location:"集合地点", time:"集合时间", domestic_connection:"国内联运", cities:"当天城市", location_raw:"地点依据", vehicle_type:"用车类型", vehicle_raw:"交通依据", room_type:"房型", consecutive_nights:"连住晚数", departure_local_time:"当地起飞时间", arrival_local_time:"当地到达时间", arrival_day_offset:"到达跨日数", categories:"经营品类", shopping_total:"购物店总数",
  day_id: "日期内容标识", blocks: "分段内容", block_id: "段落标识", paragraphs: "段落正文", type: "内容类型", visit_mode: "游览方式", ticket_status: "门票状态", summary_basis_hash: "简述核对记录", grade_basis: "住宿标准依据",
  summary: "行程概要", days: "逐日行程", nights: "住宿晚数", depart_city: "出发城市",
  countries: "目的地国家", region: "目的地区域", cover: "产品亮点", airline: "航空公司",
  hotel_standard: "住宿标准", meal_standard: "餐食标准", highlights: "行程亮点", fields: "原文补充栏目",
  transport: "交通安排", flights: "航班", inclusions: "费用包含", exclusions: "费用不含",
  shopping: "购物安排", optional: "自费项目", policies: "费用与退改条款", notices: "出行须知",
  day: "行程日序", title: "当天标题", places: "途经地点", distances_km: "里程（公里）", travel_reference: "参考里程与车程",
  overnight: "当晚住宿类型", sights: "游览项目", meals: "三餐安排", hotel: "住宿安排",
  text: "内容", name: "名称", kind: "项目类型", duration: "停留时间", ticket_included: "是否含门票",
  breakfast: "早餐", lunch: "午餐", dinner: "晚餐", included: "是否包含", or_similar: "或同级",
  grade: "住宿等级", price: "原文价格说明", flight_no: "航班号", carrier: "承运航司",
  from_place: "出发地", to_place: "到达地", times: "起降时间", raw: "原文记录",
  single_room: "单房差", child: "儿童政策", visa: "签证说明", cancellation: "退改规则",
  deposit: "定金说明", sale_type: "销售类型",
};
const rootKeys = ["summary", "cover", "days", "transport", "inclusions", "exclusions", "shopping", "optional", "policies", "notices", "sale_type", "applicability", "formation", "service_fee", "single_room_supplement", "cancellation_tiers", "traveler_requirements", "meeting", "shopping_total"];
const flight = {day: 1, flight_no: "", carrier: "", from_place: "", to_place: "", times: "", raw: ""};
const templates: Record<string, any> = {
  cancellation_tiers: {days_before_min:null,days_before_max:null,penalty_percent:null,penalty_money:null,raw:"",source_lines:[]},
  days: {day: 1, title: "", places: [], distances_km: [], transport: "", overnight: "unknown", flights: [], sights: [], meals: {breakfast: {text:"", included:null}, lunch:{text:"",included:null}, dinner:{text:"",included:null}}, hotel:null, text:""},
  flights: flight, transport: flight,
  blocks: {block_id:"",type:"programme",title:"",paragraphs:[],visit_mode:"unknown",ticket_status:"unknown"},
  sights: {name:"", kind:"景点", duration:"", ticket_included:null},
  shopping: {name:"", day:null, duration:"", categories:[], raw:""}, optional: {name:"", price:"", day:null, money:{amount:null,currency:"",unit:null,raw:"",source_lines:[]},raw:""},
};
const internalKeys = new Set(["day_id", "block_id", "summary_basis_hash", "source_lines"]);
const booleanKeys = new Set(["ticket_included", "included", "or_similar"]);
const numberKeys = new Set(["nights", "day", "minimum_travelers", "days_before_min", "days_before_max", "minimum_age", "maximum_age", "consecutive_nights", "arrival_day_offset", "shopping_total"]);

function FieldValue({name, value, title, onChange, readonly = false}: {
  name: string; value: any; title: string; onChange: (value: any) => void; readonly?: boolean;
}) {
  if (Array.isArray(value)) {
    if (templates[name] && (value.length === 0 || typeof value[0] === "object"))
      return <section className="stack document-group" aria-label={title}>
        <h3>{title}</h3>
        {value.length === 0 && <p className="muted">未提供</p>}
        {value.map((item, index) => <div className="panel stack" key={index}>
          <div className="row between"><strong>{name === "days" ? `第 ${index + 1} 天` : `${title} ${index + 1}`}</strong>
            {!readonly && <button type="button" className="link" onClick={() => {
              let next = value.filter((_, i) => i !== index);
              if (name === "days") next = next.map((row, i) => ({...row, day:i + 1}));
              onChange(next);
            }}>移除{name === "days" ? `第 ${index + 1} 天` : `${title} ${index + 1}`}</button>}
          </div>
          {Object.entries(item).filter(([key]) => !internalKeys.has(key) && (name !== "days" || key !== "day")).map(([key, child]) =>
            <FieldValue key={key} name={key} value={child} title={`${name === "days" ? `第 ${index + 1} 天` : `${title} ${index + 1}`} · ${key === "transport" ? "交通说明" : documentLabels[key] ?? key}`} readonly={readonly}
              onChange={(updated) => onChange(value.map((row, i) => i === index ? {...row, [key]:updated} : row))} />)}
        </div>)}
        {!readonly && <button className="btn" type="button" onClick={() => {
          const row = structuredClone(templates[name]);
          if (name === "days") row.day = value.length + 1;
          onChange([...value, row]);
        }}>添加{title}</button>}
      </section>;
    const joined = value.join("\n");
    return readonly ? <ReadValue title={title} value={joined} /> :
      <Field label={title}><textarea className="input" value={joined} placeholder="每行一项" onChange={(e) => {
        const rows = e.target.value ? e.target.value.split("\n") : [];
        onChange(name === "distances_km" ? rows.map((row) => row === "" ? 0 : Number(row)) : rows);
      }}/></Field>;
  }
  if (name === "penalty_money" && value === null) return readonly ? <ReadValue title={title} value=""/> : <button className="btn" type="button" onClick={()=>onChange({amount:null,currency:"",unit:null,raw:"",source_lines:[]})}>补充{title}</button>;
  if (!readonly && (name === "start" || name === "end")) return <Field label={title}><input className="input" type="date" value={value||""} onChange={e=>onChange(e.target.value||null)}/></Field>;
  if (!readonly && (name === "amount" || name === "penalty_percent")) return <Field label={title}><input className="input" inputMode="decimal" value={value??""} onChange={e=>onChange(e.target.value||null)}/></Field>;
  if (!readonly && name === "unit") return <Field label={title}><select className="input" value={value||""} onChange={e=>onChange(e.target.value||null)}><option value="">未明确</option><option value="person">每人</option><option value="room">每间</option><option value="night">每晚</option></select></Field>;
  if (!readonly && name === "type" && title.includes("签证")) return <Field label={title}><select className="input" value={value||""} onChange={e=>onChange(e.target.value||null)}><option value="">未明确</option>{Object.entries({schengen:"申根",electronic:"电子签",on_arrival:"落地签",exempt:"免签"}).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></Field>;
  if (name === "hotel" && value === null)
    return readonly ? <ReadValue title={title} value=""/> : <div className="row"><span className="muted">{title}：未提供</span><button type="button" className="btn" onClick={() => onChange({name:"", or_similar:false, grade:"", grade_basis:"unknown", room_type:null,consecutive_nights:null,raw:""})}>补充{title}</button></div>;
  if (value !== null && typeof value === "object")
    return <section className="stack document-group"><h3>{title}</h3>
      {Object.entries(value).filter(([key])=>!internalKeys.has(key)).map(([key, child]) => <FieldValue key={key} name={key} value={child} readonly={readonly}
        title={`${title} · ${name === "summary" && key === "days" ? "行程天数" : documentLabels[key] ?? key}`}
        onChange={(updated) => onChange({...value, [key]:updated})}/>)}
      {Object.keys(value).length === 0 && <p className="muted">未提供</p>}
      {name === "hotel" && !readonly && <button type="button" className="link" onClick={() => onChange(null)}>清除{title}</button>}
    </section>;
  if (booleanKeys.has(name)) {
    if (readonly) return <ReadValue title={title} value={value == null ? "待核实" : value ? "是" : "否"}/>;
    return <Field label={title}><select className="input" value={value == null ? "unknown" : String(value)} onChange={(e) => onChange(e.target.value === "unknown" ? null : e.target.value === "true")}>
      {name !== "or_similar" && <option value="unknown">待核实</option>}<option value="true">是</option><option value="false">否</option>
    </select></Field>;
  }
  const choices: Record<string,string> | null = name === "overnight" ? {unknown:"待核实", hotel:"酒店", ship:"船上", flight:"航班上", home:"返程到家"} : name === "kind" ? {景点:"景点", 外观:"外观", 购物:"购物", 自费:"自费", 赠送:"赠送", 自由活动:"自由活动"} : null;
  if (readonly) return <ReadValue title={title} value={choices ? choices[value] : value}/>;
  if (choices) return <Field label={title}><select className="input" value={value} onChange={(e) => onChange(e.target.value)}>{Object.entries(choices).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>;
  if (typeof value === "number" || numberKeys.has(name)) return <Field label={title}><input className="input" type="number" step="1" min="0" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}/></Field>;
  return <Field label={title}><textarea className="input" rows={name === "text" ? 5 : 2} value={value ?? ""} onChange={(e) => onChange(e.target.value)}/></Field>;
}
function ReadValue({title, value}: {title:string; value:any}) {
  return <div><p className="muted">{title}</p><p className="document-prose">{value == null || value === "" ? "未提供" : String(value)}</p></div>;
}
export function DocumentContent({content, onChange, excludeDays=false}: {content:RouteContent; onChange?:(content:RouteContent) => void; excludeDays?:boolean}) {
  if(isRouteKit(content))return <><p className="notice">新格式请在线路内容工作台整理并提交审批。</p><RouteDetail content={content} review termsOnly={excludeDays}/></>;
  return <div className="stack">
    {rootKeys.filter(key=>key in content && (!excludeDays || key!=="days")).map((key) => <details key={key} className="document-section" open={key === "days"}>
      <summary>{documentLabels[key]}</summary>
      <div className="stack"><FieldValue name={key} value={content[key]} title={documentLabels[key]} readonly={!onChange} onChange={(value) => onChange?.({...content, [key]:value})}/></div>
    </details>)}
  </div>;
}

function leaves(value:any, prefix=""): Record<string,unknown> {
  if (value && typeof value === "object") {
    if (Object.keys(value).length === 0) return {[prefix]:Array.isArray(value) ? "（空列表）" : "（无补充栏目）"};
    return Object.assign({}, ...Object.entries(value).map(([key, child]) => leaves(child, `${prefix}/${key.replaceAll("~","~0").replaceAll("/","~1")}`)));
  }
  return {[prefix]:value};
}
export function DocumentDiff({before, after}: {before:RouteContent; after:RouteContent}) {
  const first = leaves(before), second = leaves(after);
  const changed = [...new Set([...Object.keys(first), ...Object.keys(second)])].filter((key) => first[key] !== second[key]);
  const label = (path:string) => path.split("/").filter(Boolean).map((key) => /^\d+$/.test(key) ? `第 ${Number(key) + 1} 项` : documentLabels[key.replaceAll("~1","/").replaceAll("~0","~")] ?? key).join(" / ");
  return <section className="stack" aria-label="文档修订对照"><h3>与解析稿相比，修订 {changed.length} 处</h3>
    {!changed.length && <p className="muted">未修改解析字段，仍需核对原文件与完整内容。</p>}
    {changed.map((path) => <div className="document-diff" key={path}><strong>{label(path)}</strong><div className="form-grid"><ReadValue title="解析原值" value={first[path]}/><ReadValue title="复核提交值" value={second[path]}/></div></div>)}
  </section>;
}

export function DailyExtraFields({day,onChange}:{day:Record<string,any>;onChange:(day:Record<string,any>)=>void}) {
  const defaults={countries:[],cities:[],location_raw:"",vehicle_type:null,vehicle_raw:"",flights:[],sights:[]};
  return <details className="panel"><summary>当天地点、用车与参考航班</summary><div className="stack">{Object.entries(defaults).map(([key,value])=><FieldValue key={key} name={key} value={day[key]??value} title={documentLabels[key]} onChange={next=>onChange({...day,[key]:next})}/>)}{day.hotel && <><FieldValue name="room_type" value={day.hotel.room_type} title="酒店房型" onChange={v=>onChange({...day,hotel:{...day.hotel,room_type:v||null}})}/><FieldValue name="consecutive_nights" value={day.hotel.consecutive_nights} title="连住晚数" onChange={v=>onChange({...day,hotel:{...day.hotel,consecutive_nights:v}})}/></>}</div></details>;
}
