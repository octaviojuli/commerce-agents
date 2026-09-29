"use client";
import {useEffect,useId,useRef,useState} from "react";
import schema from "web-shared/route-kit-schema.json";
import {useRouteCover} from "web-shared/route-cover";
import {RouteDetail} from "web-shared/route-detail";
import {WarehouseClient,message} from "../lib/api";
import {DocumentEvidence} from "./documents";
import {Field} from "./common";
import {buildCards,reconcileCards,type Card,type Context} from "../lib/route-review";
import {RouteKitReview,type Choice} from "./route-kit-review";

type Row=Record<string,any>;
type Mode="review"|"read"|"edit"|"customer";
const modeLabels:Record<Mode,string>={review:"审核",read:"阅读行程",edit:"高级编辑",customer:"已发布客户预览"};
const clone=(x:Row)=>structuredClone(x);
const names:Record<string,string>={title:"标题",subtitle:"副标题",days_count:"行程天数",nights:"住宿晚数",depart_city:"出发地",countries:"到访国家",cities:"到访城市",day:"日序",day_end:"连续日期结束日",summary:"当天简述",travel_text:"参考里程与车程",items:"行程节点",name:"名称",local_name:"当地名称",description:"说明",type:"节点类型",visit_mode:"游览方式",ticket:"门票",inclusion:"是否包含",duration_text:"参考时长",price_text:"附件费用",min_participants:"项目最低人数",booking_note:"预约说明",includes:"包含内容",children:"子项目",alternative:"替代安排",disclaimer:"限制条件",extra_cost_note:"另付费用",condition:"适用条件",text:"正文",meals:"三餐",breakfast:"早餐",lunch:"午餐",dinner:"晚餐",stay:"住宿",kind:"类型",names:"酒店名称",city:"城市",grade_text:"住宿标准",grade_basis:"标准依据",or_similar:"或同级",check_in_text:"入住说明",room_type:"房型",consecutive_nights:"连住晚数",status:"状态",notes:"当日提示",transport:"交通",mode:"交通类型",from_place:"出发地",to_place:"目的地",distance_text:"里程",service_no:"航班或车次",times_text:"参考起降时间",reference:"参考班次",departure_local_time:"当地出发时间",arrival_local_time:"当地到达时间",arrival_day_offset:"到达跨日数",clock_text:"参考时间",part_of_day:"时段",highlight:"特别安排",spot_label:"原文机位标识",shopping_kind:"购物类别",categories:"品类",services:"用车与导游",coach:"用车",guide:"导游",note:"说明",port_call:"邮轮靠港",port:"港口",arrive_text:"到港",depart_text:"离港",all_aboard_text:"最迟返船",day_kind:"行程日类型",cover_facts:"吃住行概览",selling_points:"卖点标签",highlights:"行程亮点",prices:"附件参考价格",label:"价格名称",amount:"金额",currency:"币种",basis:"计价单位",audience:"适用人群",category:"分类",inclusions:"费用包含",exclusions:"费用不含",shopping:"购物点",optional_items:"自费项目",policies:"费用与退改",single_room:"单房差",child:"儿童",senior:"老人",tips:"服务费",cancellation:"退改",deposit:"定金",notices:"温馨提示",applicability:"行程适用范围",start:"适用开始日期",end:"适用结束日期",departure_cities:"适用出发城市",version_label:"版本名称",formation:"成团要求",minimum_travelers:"最低成团人数",failure_action:"不成团处理",booking_deadline:"收客截止说明",traveler_requirements:"报名限制",minimum_age:"最低年龄",maximum_age:"最高年龄",bed_policy:"占床要求",conditions:"限制条件",pregnancy:"孕妇",visa:"签证",submission_deadline:"材料截止说明",passport_validity:"护照有效期",insurance:"保险",included:"是否包含",raw:"原文依据",meeting:"集合与联运",location:"地点",time:"时间",domestic_connection:"国内联运",cancellation_tiers:"退改区间",days_before_min:"出发前最少天数",days_before_max:"出发前最多天数",penalty_percent:"扣费比例",penalty_money:"扣费金额",unit:"计费单位",shopping_status:"购物安排状态",reason:"原因"};
const hidden=new Set(["cite","title_cite","shopping_cite","departure_dates_cite","day_id","node_id","units","overview_units","issues","auto","review"]);
const enumNames:Record<string,string>={hotel:"酒店",ship:"船上",flight:"航班",train:"列车",home:"回家",self:"自理",not_applicable:"不适用",morning:"上午",noon:"中午",afternoon:"下午",evening:"傍晚",night:"夜间",coach:"巴士",night_train:"夜车",ferry:"渡轮",cruise:"邮轮",boat:"游船",speedboat:"快艇",seaplane:"水上飞机",shuttle:"接驳车",cable_car:"缆车",designated_store:"指定购物店",outlet_mall:"奥特莱斯",department_store:"百货商场",market:"市场",tour:"团费",service_fee:"服务费",additional:"另付费用",child:"儿童",senior:"老人",single_room:"单房差",person:"人",room:"间",weather:"天气",wildlife:"野生动物",operating_days:"营业日期",closure:"关闭",booking:"预约",traffic:"交通",other:"其他",partial:"部分包含",unknown:"未说明",inside:"入内",outside:"外观",passing:"途经",drive_by:"乘车游览",distant_view:"远眺",walk:"步行",included:"包含",excluded:"不含",free_entry:"免费",gift:"赠送",optional_paid:"另付费",package_item:"套餐内",recommended_not_included:"推荐·不含",meet:"集合",transport:"交通",poi:"景点",group:"观光段",activity:"活动",free:"自由活动",shopping:"购物",optional:"自费",package:"套餐",recommend:"推荐",photo_spot:"拍照点",notice:"提醒",regular:"常规行程",transit:"交通日",at_sea:"海上日",port_call:"靠港",cruising_no_landing:"不登岸巡游",resort:"度假",supplier_description:"供应商描述",official_rating:"已核验官方评级",present:"含购物安排",none:"明确无购物"};
const node=()=>({node_id:crypto.randomUUID(),type:"poi",name:"",description:"",visit_mode:"unknown",ticket:"unknown",inclusion:"unknown",duration_text:"",includes:[],children:[],cite:[]});
const defs:Row=(schema as Row).$defs;
function resolve(s:Row):Row{return s?.$ref?defs[s.$ref.split("/").pop()]:s??{};}
function fresh(s:Row):any {s=resolve(s);if(s.default!==undefined)return structuredClone(s.default);if(s.anyOf)return fresh(s.anyOf.find((x:Row)=>x.type!=="null")??s.anyOf[0]);if(s.type==="array")return [];if(s.type==="object")return Object.fromEntries(Object.entries(s.properties??{}).map(([k,v])=>[k,k==="node_id"||k==="day_id"?crypto.randomUUID():fresh(v as Row)]));if(s.enum)return s.enum[0];if(s.type==="boolean")return false;if(s.type==="integer"||s.type==="number")return null;return "";}
function Fields({value,name,onChange,shape}:{value:any;name:string;onChange:(x:any)=>void;shape?:Row}) {
  let spec=resolve(shape??(name==="day"?defs.Day:(schema as Row).properties?.[name]));
  const nullable=spec.anyOf?.some((x:Row)=>x.type==="null");
  if(spec.anyOf)spec=resolve(spec.anyOf.find((x:Row)=>x.type!=="null")??spec.anyOf[0]);
  if(value==null&&spec.type==="object")return <div className="row"><span>{names[name]??name}：未提供</span><button className="btn" type="button" onClick={()=>onChange(fresh(spec))}>补充此项</button></div>;
  if(Array.isArray(value))return <fieldset className="panel stack"><legend>{names[name]??name}</legend>{value.map((x,i)=><div className="stack" key={x?.node_id??i}><Fields name={name} value={x} shape={spec.items} onChange={v=>onChange(value.map((z,j)=>i===j?v:z))}/><div className="row"><button className="link" type="button" disabled={!i} onClick={()=>{const a=[...value];[a[i-1],a[i]]=[a[i],a[i-1]];onChange(a);}}>上移</button><button className="link" type="button" onClick={()=>onChange(value.filter((_,j)=>i!==j))}>移除此项</button></div></div>)}<button className="btn" type="button" onClick={()=>onChange([...value,fresh(spec.items??{})])}>＋ 添加{names[name]??"条目"}</button></fieldset>;
  if(value&&typeof value==="object")return <div className="stack">{name!=="day"&&<h4>{names[name]??name}</h4>}{Array.isArray(value.cite)&&<Field label="依据原文编号（在原文对照中查阅）"><input className="input" value={value.cite.join(",")} onChange={e=>{if(/^[0-9,， ]*$/.test(e.target.value))onChange({...value,cite:e.target.value.split(/[,， ]+/).filter(Boolean).map(Number)});}}/></Field>}{Object.entries(value).filter(([k])=>!hidden.has(k)&&!(name==="day"&&k==="status")).map(([k,v])=><Fields key={k} name={k} value={v} shape={spec.properties?.[k]} onChange={next=>onChange({...value,[k]:next})}/>)}{nullable&&<button className="link" type="button" onClick={()=>onChange(null)}>清除此项</button>}</div>;
  if(spec.enum)return <Field label={names[name]??name}><select className="input" value={value??""} onChange={e=>onChange(e.target.value||null)}>{nullable&&<option value="">未说明</option>}{spec.enum.map((x:string)=><option key={x} value={x}>{enumNames[x]??x}</option>)}</select></Field>;
  if(spec.type==="boolean")return <Field label={names[name]??name}><select className="input" value={value==null?"unknown":String(value)} onChange={e=>onChange(e.target.value==="unknown"?null:e.target.value==="true")}>{nullable&&<option value="unknown">未说明</option>}<option value="true">是</option><option value="false">否</option></select></Field>;
  if(spec.type==="integer"||spec.type==="number")return <Field label={names[name]??name}><input className="input" type="number" min={spec.minimum} max={spec.maximum} value={value??""} onChange={e=>onChange(e.target.value===""?null:Number(e.target.value))}/></Field>;
  if(spec.format==="date")return <Field label={names[name]??name}><input className="input" type="date" value={value??""} onChange={e=>onChange(e.target.value||null)}/></Field>;
  return <Field label={names[name]??name}><textarea className="input" rows={String(value??"").length>70?4:2} value={value??""} onChange={e=>onChange(e.target.value)}/></Field>;
}
export function RouteKitEditor({api,id,initial,writable,publishable=false,onProposed,onPublished}:{api:WarehouseClient;id:string;initial:Row;writable:boolean;publishable?:boolean;onProposed:()=>void;onPublished?:()=>void}) {
  const [draft,setDraft]=useState(initial),[content,setContent]=useState(clone(initial.content)),[mode,setMode]=useState<Mode>(writable?"review":"read"),[day,setDay]=useState(0),[extra,setExtra]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState(""),[dirty,setDirty]=useState(false),[allowed,setAllowed]=useState(true),[preview,setPreview]=useState<Row|null>(null),[done,setDone]=useState<"proposed"|"published"|null>(null);
  const [previewLoading,setPreviewLoading]=useState(false),[previewError,setPreviewError]=useState("");
  const [focusRequest,setFocusRequest]=useState(0);
  const modeAnchor=useRef<HTMLDivElement>(null),previewRequest=useRef<AbortController|null>(null);
  const contentId=useId();
  useEffect(()=>()=>previewRequest.current?.abort(),[]);
  useEffect(()=>{
    if(!focusRequest||!modeAnchor.current)return;
    const target=modeAnchor.current;
    const heading=target.closest<HTMLElement>(".portal-main")?.querySelector<HTMLElement>(".business-detail-head");
    target.style.scrollMarginTop=`${(heading?.offsetHeight??0)+16}px`;
    target.scrollIntoView({block:"start",behavior:"instant"});target.focus({preventScroll:true});
  },[focusRequest]);
  function showMode(next:Mode){previewRequest.current?.abort();setPreviewLoading(false);setMode(next);setFocusRequest(n=>n+1);}
  async function customer(){
    showMode("customer");const request=new AbortController();previewRequest.current=request;
    setPreview(null);setPreviewError("");setPreviewLoading(true);
    try{const result=await api.get<Row>(`/merchant/routes/${id}/customer-preview`,request.signal);if(!request.signal.aborted)setPreview(result);}
    catch(e){if(!request.signal.aborted)setPreviewError(message(e));}
    finally{if(!request.signal.aborted)setPreviewLoading(false);}
  }
  const [savedDecisions,setSavedDecisions]=useState<Row[]>(initial.fact_decisions??[]);
  const [ctx,setCtx]=useState<Context>({listing:initial.listing,departureDays:initial.departure_days});
  const [cards,setCards]=useState<Card[]>(()=>buildCards(initial.issues??[],initial.content,initial.content.units??[],{listing:initial.listing,departureDays:initial.departure_days})),[choices,setChoices]=useState<Record<string,Choice>>({});
  useEffect(()=>{let stopped=false;const abort=new AbortController();async function check(){try{await api.get(`/merchant/routes/${id}/content`,abort.signal);}catch(e){if(!stopped){setAllowed(false);previewRequest.current?.abort();setContent({});setPreview(null);setSavedDecisions([]);setError(message(e));}}}const timer=setInterval(check,30000);window.addEventListener("focus",check);return()=>{stopped=true;abort.abort();clearInterval(timer);window.removeEventListener("focus",check);};},[api,id]);
  const coverUrl=useRouteCover(api,allowed?(mode==="customer"?preview?.content?.cover_asset_id:content.source?.cover_image):null);
  function edit(next:Row){setContent(next);setDirty(true);setChoices({});setNotice("内容已修改，请重新检查并确认。");}
  const decisions=(chosen:Record<string,Choice>)=>{
    const result=new Map(savedDecisions.map(d=>[d.field,d]));
    for(const c of cards){const o=c.options.find(x=>x.id===chosen[c.key]?.option);const at=ctx.listing?.[o?.decide?.field??"days"];if(o?.decide&&at)result.set(o.decide.field,{field:o.decide.field,upstream:at.upstream,value:o.decide.value,basis:c.facts??[]});}
    return [...result.values()];
  };
  function choose(card:Card,option:string,input?:string){
    const picked=card.options.find(o=>o.id===option);if(!picked)return;
    const next=clone(content);picked.apply(next,input);setContent(next);setDirty(true);setNotice("");
    const chosen={...choices,[card.key]:{option,input}};setChoices(chosen);
    if(picked.decide)void recheck(chosen,next);
  }
  function undo(card:Card){setChoices(old=>Object.fromEntries(Object.entries(old).filter(([k])=>k!==card.key)));}
  function keepAll(){
    let next=clone(content);const made:Record<string,Choice>={};
    for(const card of cards){if(choices[card.key]||card.editOnly||card.facts||card.waiting)continue;const keep=card.options.find(o=>o.keeps);if(keep){keep.apply(next);made[card.key]={option:keep.id};}}
    setContent(next);setDirty(true);setChoices(old=>({...old,...made}));
  }
  function merge(found:Row[],now:Row,chosen=choices,context=ctx){
    const fresh=buildCards(found,now,now.units??[],context);
    const next=reconcileCards(cards,fresh,chosen);
    setCards(next.cards);setChoices(next.choices);
  }

  const decided=(serverKey:string)=>{const group=cards.filter(c=>c.serverKey===serverKey);return group.length>0&&group.every(c=>choices[c.key]);};
  const pending=cards.filter(c=>!choices[c.key]);
  const summary=()=>{const made=cards.filter(c=>choices[c.key]);return `供应商在审核页逐项确认 ${made.length} 项：`+made.map(c=>`${c.where}${c.title}→${c.options.find(o=>o.id===choices[c.key].option)?.label??""}`).join("；")+(extra.trim()?`。补充：${extra.trim()}`:"");};
  const body=(resolutions:Row[],source_note=summary().slice(0,2000),chosen=choices,doc=content)=>({expected_revision:draft.revision,expected_source_hash:draft.source_hash,source_kind:draft.source_kind,source_note,content:doc,review_resolutions:resolutions,fact_decisions:decisions(chosen)});
  async function save(){
    setBusy(true);setError("");
    try{const r=await api.post<Row>(`/merchant/routes/${id}/content`,body([],extra.trim()||"高级编辑保存草稿"));setDraft({...draft,revision:r.revision,revision_id:r.revision_id});setContent(r.content);setDirty(false);merge(r.issues,r.content);setNotice("草稿已保存，尚未发布。请回到“审核”确认剩余事项。");}
    catch(e){setError(message(e));}finally{setBusy(false);}
  }
  async function recheck(chosen=choices,doc=content){
    setBusy(true);setError("");
    try{const r=await api.post<Row>(`/merchant/routes/${id}/content/validate`,body([],undefined,chosen,doc));merge(r.issues,doc,chosen);setNotice(r.issues.filter((i:Row)=>i.code!=="CONTENT_FACT_REVIEW").length?"已重新检查，请处理剩余事项。":"重新检查通过，没有剩余事项。");}
    catch(e){setError(message(e));}finally{setBusy(false);}
  }
  async function finish(){
    setBusy(true);setError("");setNotice("");
    try{
      const note=summary().slice(0,2000);
      const check=await api.post<Row>(`/merchant/routes/${id}/content/validate`,body([],note));
      const open=check.issues.filter((i:Row)=>i.code!=="CONTENT_FACT_REVIEW");
      const blocked=open.filter((i:Row)=>!i.acknowledgeable||!decided(`${i.code}|${i.path}`));
      if(blocked.length){merge(check.issues,content);setError(`还有 ${blocked.length} 项没有处理，请先完成上面的确认。`);return;}
      const saying=(i:Row)=>i.code==="CONTENT_FACT_REVIEW"?"供应商已对照原文核对景点、餐宿、费用与限制条件，确认整理内容与原文一致":cards.filter(c=>c.serverKey===`${i.code}|${i.path}`).map(c=>`${c.title}：${c.options.find(o=>o.id===choices[c.key]?.option)?.label}`).join("；");
      const resolutions=check.issues.filter((i:Row)=>i.acknowledgeable).map((i:Row)=>({code:i.code,path:i.path,basis_hash:i.basis_hash,note:saying(i)}));
      const saved=await api.post<Row>(`/merchant/routes/${id}/content`,body(resolutions,note));
      setDraft({...draft,revision:saved.revision,revision_id:saved.revision_id});setContent(saved.content);setDirty(false);
      if(saved.issues.length){merge(saved.issues,saved.content);setError("内容已保存，但仍有事项未通过检查，请查看。");return;}
      const change=await api.post<Row>("/route-content-proposals",{target_id:id,revision_id:saved.revision_id,note});
      if(publishable){
        await api.post(`/changes/${change.id}/approve`,{payload_hash:change.payload_hash});
        await api.post(`/changes/${change.id}/apply`);
        setDone("published");onPublished?.();
      }else{setDone("proposed");onProposed();}
    }catch(e){setError(`${message(e)} 已保存的草稿不会丢失，可刷新后继续。`);}finally{setBusy(false);}
  }
  async function adopt(){setBusy(true);try{const fresh=await api.get<Row>(`/merchant/routes/${id}/content?include_source=true`);setDraft(fresh);setCtx({listing:fresh.listing,departureDays:fresh.departure_days});setSavedDecisions([]);edit(clone(fresh.candidate));setCards(buildCards(fresh.issues,fresh.candidate,fresh.candidate.units??[],{listing:fresh.listing,departureDays:fresh.departure_days}));setChoices({});setDay(0);}catch(e){setError(message(e));}finally{setBusy(false);}}
  if(!allowed)return <p role="alert" className="notice error">访问权限需要重新确认，请重新进入线路详情。{error}</p>;
  const blockedBy=draft.parsing?"附件正在处理，暂不能提交":draft.source_changed?"资料已有新版本，请先采用新候选稿":pending.length?`还有 ${pending.length} 项没有确认`:"";
  return <section className="stack route-kit-workspace"><header className="panel stack"><h2>线路内容工作台</h2><p>{draft.published_review_mode==="test_auto"?"测试自动发布 · 未人工审核":draft.published_review_mode==="human"?"已有人工审核发布内容":"结构化整理稿 · 待人工复核"}　草稿 {draft.revision} 版 / 已发布 {draft.content_version} 版{dirty?" · 有未保存修改":""}</p></header>
    <div ref={modeAnchor} tabIndex={-1} className="panel stack route-mode-bar">
      <div className="route-mode-buttons" role="group" aria-label="行程工作模式">{([...(writable?["review"]:[]),"read",...(writable?["edit"]:[]),"customer"] as Mode[]).map(value=><button key={value} type="button" className="btn" aria-label={modeLabels[value]} aria-pressed={mode===value} aria-controls={contentId} onClick={()=>value==="customer"?customer():showMode(value)}>{modeLabels[value]}{value==="review"&&pending.length>0&&<span className="route-mode-badge">{pending.length} 项</span>}{value==="customer"&&!draft.published_review_mode&&!preview?.published&&<span className="route-mode-badge">未发布</span>}</button>)}</div>
      <div className="route-mode-help"><div><strong>当前：{modeLabels[mode]}</strong><p>{mode==="review"?"对照原文逐项确认，下方可提交审批或明确确认发布。":mode==="edit"?"修改后保存草稿，再回到审核确认；保存不会发布。":mode==="read"?"查看当前整理稿及其原文依据。":"这里只展示已发布版本，不包含尚未发布的修改。"}</p></div></div>
    </div>
    {draft.test_publication&&<details className="panel route-review-reference"><summary>测试发布评估 · 展开查看</summary><div className="stack"><p>有效内容覆盖率 <strong>{draft.test_publication.coverage_percent}%</strong> / 门槛 85%</p><p className="muted">直接引用原文的单元比例；不把推断续句和原样挂入算作整理完成，不代表事实准确率。</p><p>{draft.test_publication.enabled?"测试自动发布已启用":"自动发布未在当前账号启用"} · {draft.test_publication.eligible?"已达到发布门槛":"保留草稿"}</p>{draft.test_publication.blockers?.map((x:Row,i:number)=><p key={i} className="notice warning">{x.message}</p>)}</div></details>}
    {draft.parsing&&<p className="notice">附件正在处理或需要重试，当前保留上一版内容供对照。</p>}{draft.source_changed&&<p className="notice warning">资料已有新版本，请对照后采用新候选稿。<button className="btn" disabled={busy||dirty} onClick={adopt}>采用新候选稿</button></p>}
    {error&&<p className="notice error" role="alert">{error}</p>}{notice&&<p className="notice" role="status">{notice}</p>}
    {(["days","gateway"] as const).map(k=>{const f=draft.listing?.[k];const name=k==="days"?"行程天数":"出发口岸";return f&&(f.state==="active"?<p key={k} className="notice" role="status">{name}“{String(f.effective)}”是云仓核定值，上游登记为“{String(f.upstream)}”。</p>:f.state==="stale"?<p key={k} className="notice warning" role="status">此前核定的{name}已失效：上游现在是“{String(f.upstream)}”。下次审核会重新提问。</p>:null);})}
    {savedDecisions.map(d=><p key={d.field} className="notice" role="status">待发布核定：{d.field==="days"?"行程天数":"出发口岸"}由“{String(d.upstream??"未提供")}”改为“{String(d.value)}”。{writable&&<button className="link" disabled={busy} onClick={()=>{setSavedDecisions(old=>old.filter(x=>x.field!==d.field));setDirty(true);setNotice("已撤销该核定，请重新检查。");}}>撤销此核定</button>}</p>)}
    {done&&<p className="notice" role="status">{done==="published"?"已发布。顾问现在可以看到这条线路的最新行程。":"已提交，请到审批中心确认发布。"}</p>}
    {draft.asset_id&&<details className="panel"><summary>原文件与来源记录</summary><DocumentEvidence api={api} asset={draft.asset_id} name={content.source?.file_name} hash={content.source?.sha256} version={draft.asset_product_version} parser={content.source?.parser}/></details>}
    <div id={contentId} role="region" aria-label={`${modeLabels[mode]}内容`} className="stack" aria-busy={mode==="customer"&&previewLoading}>
    {mode==="review"&&writable&&!done&&<fieldset className="document-fieldset" disabled={busy||!!draft.parsing}><RouteKitReview cards={cards} choices={choices} onChoose={choose} onKeepAll={keepAll} onJump={(d,card)=>{if(card)undo(card);if(d){const at=content.days.findIndex((x:Row)=>x.day===d);if(at>=0)setDay(at);}showMode("edit");}}
      footer={<div className="panel stack review-submit"><details><summary>补充说明（可选）</summary><input className="input" aria-label="补充说明" value={extra} maxLength={500} onChange={e=>setExtra(e.target.value)} placeholder="例如：已电话向供应商确认第 5 天用餐"/></details><div className="row between"><span className="muted">{blockedBy||"点击即表示已对照原文核对景点、餐宿、费用与限制条件。"}</span><div className="row"><button className="btn" disabled={busy} onClick={()=>recheck()}>重新检查</button><button className="btn primary" disabled={busy||!!blockedBy} onClick={finish}>{busy?"处理中…":publishable?"确认无误并发布":"确认无误并提交审批"}</button></div></div></div>}/></fieldset>}
    {mode==="read"&&<RouteDetail content={content} review coverUrl={coverUrl}/>}
    {mode==="customer"&&(previewLoading?<p className="notice" role="status">正在读取已发布行程…</p>:previewError?<section className="panel stack"><p role="alert" className="notice error">预览读取失败：{previewError}</p><button type="button" className="btn" onClick={customer}>重试预览</button></section>:preview?.published?<RouteDetail content={preview.content} coverUrl={coverUrl}/>:<section className="panel stack route-preview-empty"><h3>这条线路尚未发布</h3><p>完成行程复核并通过发布审批后，才会显示客户版本。</p><div className="row"><button type="button" className="btn" onClick={()=>showMode("read")}>查看当前整理稿</button>{writable&&<button type="button" className="btn primary" onClick={()=>showMode("review")}>去审核行程</button>}</div></section>)}
    {mode==="edit"&&writable&&<fieldset disabled={busy||!!draft.parsing} className="stack document-fieldset"><div className="row"><label>选择日期 <select className="input" value={day} onChange={e=>setDay(Number(e.target.value))}>{content.days.map((d:Row,i:number)=><option key={d.day_id??i} value={i}>D{d.day} · {d.title}</option>)}</select></label><button className="btn" onClick={()=>{const c=clone(content);c.days.push({day_id:crypto.randomUUID(),day:c.days.length+1,day_end:null,title:"",summary:"",cities:[],countries:[],travel_text:"",day_kind:"regular",items:[node()],meals:{breakfast:{status:"unknown",text:"",cite:[]},lunch:{status:"unknown",text:"",cite:[]},dinner:{status:"unknown",text:"",cite:[]}},stay:{kind:"unknown",names:[],city:"",grade_text:"",grade_basis:"unknown",room_type:"",consecutive_nights:null,or_similar:false,check_in_text:"",cite:[]},notes:[],units:[],overview_units:[],status:"extracted",issues:[],title_cite:[]});edit(c);setDay(c.days.length-1);}}>＋ 添加一天</button></div>
      {content.days[day]&&<div className="panel stack"><h3>第 {content.days[day].day} 天</h3><Fields name="day" value={content.days[day]} onChange={value=>{const c=clone(content);c.days[day]=value;edit(c);}}/><button className="link" onClick={()=>{const c=clone(content);c.days.splice(day,1);edit(c);setDay(Math.max(0,day-1));}}>移除此日</button></div>}
      <details className="panel"><summary>线路概览、价格、费用与报名条件</summary><div className="stack">{["title","subtitle","days_count","nights","depart_city","countries","cover_facts","selling_points","highlights","prices","inclusions","exclusions","shopping_status","shopping","optional_items","policies","notices","applicability","formation","traveler_requirements","meeting","cancellation_tiers"].map(k=><Fields key={k} name={k} value={content[k]} onChange={value=>edit({...content,[k]:value})}/>)}</div></details>
      <div className="row"><button className="btn primary" disabled={busy||!dirty} onClick={save}>保存草稿</button><button className="btn" onClick={()=>showMode("review")}>回到审核</button></div><p className="muted">高级编辑保存后回到“审核”确认剩余事项并发布；保存草稿不会改变已发布内容。</p></fieldset>}
    </div>
  </section>;
}
