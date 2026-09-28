"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Change, Listing, WarehouseClient, message, time } from "../lib/api";
import { Field, LoadState, Pagination, useData } from "./common";
import { DocumentContent, DocumentDiff, type PageLocation, type RouteContent } from "./document-content";

type Page<T> = {items:T[]; next_cursor:string | null};
type Asset = {id:string; product_id:string; file_name:string; file_hash:string; product_version:number; status:string; created_at:string};
type Detail = Asset & {
  last_error:string | null;
  origin:null | {source_snapshot_id:string;completed_at:string};
  current_product_version:number;
  parse_generation:number;
  publications:{id:string; product_version:number; created_at:string}[];
  parse:null | {id:string; generation:number; parser_version:string; body:RouteContent; field_sources:Record<string,unknown>};
};
const parseNames:Record<string,string> = {queued:"等待解析", parsing:"正在解析", parsed:"解析完成", failed:"解析失败"};
const parseReasons:Record<string,string> = {
  DOCUMENT_EXTRACTION_RETIRED:"旧抽取任务已停用，请重新解析原文件",
  DOCUMENT_PARSE_OPEN_FAILED:"原文件打开失败",DOCUMENT_PARSE_SEGMENT_FAILED:"行程分段失败",DOCUMENT_PARSE_DAYS_FAILED:"逐日内容读取失败",DOCUMENT_PARSE_TERMS_FAILED:"费用条款读取失败",DOCUMENT_PARSE_VALIDATION_FAILED:"结构字段校验失败",
  DOCUMENT_TEXT_MISSING:"未提取到文字，文件可能是扫描件或图片。请提供带文字层的完整行程，或先完成 OCR 后上传并复核。",
  DOCUMENT_TEXT_INSUFFICIENT:"可提取文字过少，无法可靠生成完整行程。请核对是否为封面、结算单或部分内容为图片，补充完整行程后重新上传。",
  DOCUMENT_PDF_READ_FAILED:"PDF 读取失败。请检查文件是否损坏或加密，并重新导出可读取的文件。",
  DOCUMENT_PARSER_UNAVAILABLE:"解析组件暂不可用，请联系管理员检查服务配置后重试。",
  DOCUMENT_PARSE_TIMEOUT:"解析超过时间限制。请简化文件或联系管理员检查后重试。",
  DOCUMENT_PARSE_OUTPUT_LIMIT:"解析内容超过大小上限，请精简附件后重新上传。",
  PARSER_ATTEMPTS_EXHAUSTED:"解析任务多次中断，请联系管理员检查服务后重试。",
};

function useDocumentRequest(api:WarehouseClient) {
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  async function request(path:string, init:RequestInit={}) {
    pending.current?.abort();
    const cancel = new AbortController();
    pending.current = cancel;
    const response = await api.response(path, {...init, signal:cancel.signal});
    if (cancel.signal.aborted) throw new Error("操作已取消");
    return response;
  }
  return request;
}

export function Documents({api, revision, writable, onProposed}: {
  api:WarehouseClient; revision:number; writable:boolean; onProposed:()=>void;
}) {
  const [search,setSearch] = useState("");
  const [query,setQuery] = useState("");
  const [cursors,setCursors] = useState([""]);
  const [selected,setSelected] = useState<Listing | null>(null);
  const params = new URLSearchParams({query,limit:"15"});
  if(cursors.at(-1)) params.set("after",cursors.at(-1)!);
  const state = useData<Page<Listing>>(api,`/merchant/catalog?${params}`,revision);
  return <>
    <p className="muted">选择所属线路，上传 Word 或 PDF 行程。解析结果须经复核与审批，才向获授权的采购方发布。</p>
    <form className="row panel" onSubmit={(e)=>{e.preventDefault();setQuery(search);setCursors([""]);setSelected(null);}}>
      <Field label="查找文档所属线路"><input className="input" value={search} onChange={(e)=>setSearch(e.target.value)} placeholder="输入线路名称或编号"/></Field>
      <button className="btn primary">查询线路</button>
    </form>
    <LoadState {...state}/>
    {state.data && <section className="panel stack"><div className="table-wrap"><table><thead><tr><th>线路</th><th>天数</th><th>操作</th></tr></thead><tbody>
      {state.data.items.map((row)=><tr key={row.listing_id}><td>{row.title}</td><td>{row.attributes["天数"] ?? "待核实"}</td><td><button className="link" onClick={()=>setSelected(row)}>管理文档</button></td></tr>)}
    </tbody></table></div>{!state.data.items.length && <p className="empty">当前组织没有符合条件的线路。</p>}
      <Pagination next={state.data.next_cursor} previous={cursors.length>1} onNext={()=>setCursors([...cursors,state.data!.next_cursor!])} onPrevious={()=>setCursors(cursors.slice(0,-1))}/>
    </section>}
    {selected && <ProductDocuments key={`${selected.listing_id}:${revision}`} api={api} product={selected} writable={writable} onProposed={onProposed}/>}
  </>;
}

export function ProductDocuments({api, product, writable, onProposed}: {
  api:WarehouseClient; product:Listing; writable:boolean; onProposed:()=>void;
}) {
  const request = useDocumentRequest(api);
  const [revision,setRevision] = useState(0);
  const [listRevision,setListRevision] = useState(0);
  const refreshList = useCallback(()=>setListRevision(n=>n+1),[]);
  const [cursors,setCursors] = useState([""]);
  const [selected,setSelected] = useState("");
  const [busy,setBusy] = useState(false);
  const [notice,setNotice] = useState("");
  const [error,setError] = useState("");
  const params = new URLSearchParams({product_id:product.listing_id,limit:"10"});
  if(cursors.at(-1)) params.set("before",cursors.at(-1)!);
  const state = useData<Page<Asset>>(api,`/documents?${params}`,revision + listRevision);
  useEffect(()=>{
    if(!state.data?.items.some(row=>["queued","parsing"].includes(row.status))) return;
    const timer=window.setTimeout(refreshList,5000);
    return ()=>window.clearTimeout(timer);
  },[state.data,refreshList]);
  async function upload(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form=e.currentTarget, file=new FormData(form).get("file") as File;
    if(!file?.size || file.size>20_000_000) {setError("请选择不超过 20 MB 的文档。");return;}
    setBusy(true);setError("");setNotice("");
    try {
      const response=await request(`/documents?product_id=${product.listing_id}`,{method:"POST",headers:{"X-File-Name":encodeURIComponent(file.name),"Content-Type":"application/octet-stream"},body:file});
      const result=await response.json();
      setSelected(result.id);setCursors([""]);setRevision(n=>n+1);form.reset();
      setNotice(result.duplicate ? "此文件已存在，已打开对应记录。" : "原文件已保存，等待解析。解析完成后请逐项复核。");
    } catch(err) {setError(message(err));} finally {setBusy(false);}
  }
  return <section className="stack" aria-label="线路文档管理"><div className="row between"><h2>{product.title} · 文档</h2><button className="btn" onClick={()=>setRevision(n=>n+1)}>刷新文档状态</button></div>
    <SourceFetch api={api} product={product.listing_id} revision={revision} writable={writable} onFetched={refreshList}/>
    {writable && <form className="panel row" onSubmit={upload}><Field label="线路原文件"><input className="input" name="file" type="file" accept=".docx,.pdf" required disabled={busy}/></Field><button className="btn primary" disabled={busy}>{busy?"正在上传…":"上传并解析"}</button><p className="muted">支持 DOCX、PDF，最多 20 MB。扫描件须先转换为可读取的文字；上传不会修改价格或库存。</p></form>}
    {notice && <p className="notice" role="status">{notice}</p>}{error && <p className="notice error" role="alert">{error}</p>}
    <LoadState {...state}/>
    {state.data && <section className="panel stack"><div className="table-wrap"><table><thead><tr><th>文件</th><th>绑定产品版本</th><th>解析状态</th><th>操作</th></tr></thead><tbody>
      {state.data.items.map((row)=><tr key={row.id}><td>{row.file_name}</td><td>{row.product_version}</td><td>{parseNames[row.status] ?? row.status}</td><td><button className="link" disabled={busy} onClick={()=>setSelected(row.id)}>查看与复核</button></td></tr>)}
    </tbody></table></div>{!state.data.items.length && <p className="empty">尚无文档记录。</p>}
      <Pagination next={state.data.next_cursor} previous={cursors.length>1} onNext={()=>setCursors([...cursors,state.data!.next_cursor!])} onPrevious={()=>setCursors(cursors.slice(0,-1))}/>
    </section>}
    {selected && <DocumentDetail key={`${selected}:${revision}`} api={api} asset={selected} writable={writable} onProposed={onProposed} onParsed={refreshList}/>}
  </section>;
}

function SourceFetch({api,product,revision,writable,onFetched}: {api:WarehouseClient;product:string;revision:number;writable:boolean;onFetched:()=>void}) {
  const [tick,setTick]=useState(0);
  const state=useData<{items:{id:string;status:string;last_error:string|null;asset_id:string|null;last_checked_at:string|null;next_check_at:string|null;last_outcome:string|null}[]}>(api,`/document-fetches?product_id=${product}`,revision+tick);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const notified=useRef("");
  const request=useDocumentRequest(api);
  const work=state.data?.items[0];
  useEffect(()=>{
    if(work?.status === "fetched" && work.asset_id && notified.current !== work.asset_id) {notified.current=work.asset_id;onFetched();}
    if(!work || !["queued","fetching","fetched"].includes(work.status)) return;
    const timer=window.setTimeout(()=>setTick(n=>n+1),work.status === "fetched" ? 30000 : 5000);
    return ()=>window.clearTimeout(timer);
  },[work,onFetched]);
  async function retry() {
    setBusy(true);setError("");
    try {await request(`/document-fetches/${work!.id}/retry`,{method:"POST"});setTick(n=>n+1);}
    catch(err){setError(message(err));}finally{setBusy(false);}
  }
  const labels:Record<string,string>={queued:"等待拉取来源附件",fetching:"正在拉取来源附件",fetched:"来源附件已保存，后续解析与复核请查看文档记录",failed:"来源附件拉取失败",obsolete:"产品已更新，旧附件任务已停止"};
  const reasons:Record<string,string>={
    ATTACHMENT_TYPE_UNSUPPORTED:"目前仅支持 DOCX 与文字版 PDF，请供应商更新附件。",
    ATTACHMENT_FILE_INVALID:"文件内容与格式不符，或文件校验未通过，请供应商检查原文件。",
    ATTACHMENT_SIZE_INVALID:"附件超过 20 MB 或文件大小声明无效，请检查或拆分文件。",
    ATTACHMENT_DESTINATION_DENIED:"文件主机未获允许，请联系平台核对来源配置。",
    ATTACHMENT_ADDRESS_DENIED:"文件地址未通过校验，请联系平台核对来源。",
    ATTACHMENT_REDIRECT_DENIED:"来源地址发生跳转，请供应商提供直接文件地址。",
    ATTACHMENT_NETWORK_FAILED:"文件暂时无法连接，可稍后重试。",
    ATTACHMENT_HTTP_FAILED:"文件服务器未提供可下载文件，请核对来源文件是否仍有效。",
    ATTACHMENT_TIMEOUT:"下载超过时间限制，可稍后重试。",
  };
  return <><LoadState {...state}/>{error && <p className="notice error" role="alert">{error}</p>}{work && <div className="notice" role="status"><p>{labels[work.status]}</p>
    {work.last_checked_at && <p>最近成功检查：{time(work.last_checked_at)}{work.last_outcome === "unchanged" ? " · 文件内容未变" : work.last_outcome === "changed" ? " · 已保存不同内容，请核对文档记录与复核状态" : ""}</p>}
    {work.next_check_at && <p className="muted">下次计划检查：{time(work.next_check_at)}。实际执行需附件服务保持运行；新内容不会自动替换已发布文档。</p>}
    {work.last_error && <p>{reasons[work.last_error] ?? "上次拉取未完成，请核对供应商来源文件及下载配置。"}文件不会自动发布。</p>}
    {work.status === "failed" && writable && <button className="btn" disabled={busy} onClick={retry}>重试拉取附件</button>}
  </div>}</>;
}

function DocumentDetail({api,asset,writable,onProposed,onParsed}: {api:WarehouseClient;asset:string;writable:boolean;onProposed:()=>void;onParsed:()=>void}) {
  const [revision,setRevision]=useState(0);
  const state=useData<Detail>(api,`/documents/${asset}`,revision);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const request=useDocumentRequest(api);
  const status=state.data?.status;
  const notified = useRef(false);
  useEffect(()=>{
    if(["queued","parsing"].includes(status ?? "")) notified.current=false;
    if(["parsed","failed"].includes(status ?? "") && !notified.current) {notified.current=true;onParsed();}
  },[status,onParsed]);
  useEffect(()=>{
    if(!["queued","parsing"].includes(status ?? "")) return;
    const timer=window.setTimeout(()=>setRevision(n=>n+1),3000);
    return ()=>window.clearTimeout(timer);
  },[status,revision]);
  async function retry() {
    setBusy(true);setError("");
    try {await request(`/documents/${asset}/retry`,{method:"POST"});notified.current=false;setRevision(n=>n+1);}
    catch(err){setError(message(err));}finally{setBusy(false);}
  }
  const detail=state.data;
  return <section className="panel details stack" aria-label="文档解析与复核"><LoadState {...state}/>{error && <p className="notice error" role="alert">{error}</p>}
    {detail && <><DocumentEvidence api={api} asset={asset} name={detail.file_name} hash={detail.file_hash} version={detail.product_version} parser={detail.parse?.parser_version} locations={detail.parse?.body.source.page_locations}/>
      <p className="muted">{detail.origin ? `来源：供应商系统同步 · ${time(detail.origin.completed_at)}` : "来源：人工上传"}</p>
      <p role="status">解析状态：{parseNames[detail.status]}</p>
      <p className="muted">当前为第 {detail.parse_generation} 次解析。每次结果单独保留，重新解析不会自动发布或替换已发布内容。</p>
      {writable && detail.status === "parsed" && detail.parse && <ReparseDocument api={api} detail={detail} onQueued={()=>{notified.current=false;setRevision(n=>n+1);onParsed();}}/>}
      <details><summary>查看解析版本记录</summary><ParseHistory key={`${asset}:${detail.parse_generation}:${detail.status}`} api={api} detail={detail}/></details>
      {detail.publications.map((publication)=><PublishedDocument key={publication.id} api={api} publication={publication} current={publication.product_version === detail.current_product_version}/>)}
      {detail.current_product_version !== detail.product_version && !detail.publications.some(p=>p.product_version === detail.current_product_version) && <p className="notice warning">产品已更新至版本 {detail.current_product_version}。此文件绑定旧版本，请按当前产品重新上传并复核。</p>}
      {detail.status === "failed" && <div className="notice warning"><p>{parseReasons[detail.last_error ?? ""] ?? "文档未能完成解析。请检查原文件，必要时重新上传或联系管理员。"}</p><p>原文件已保留，尚未发布；解析完成后仍须人工复核。</p>{writable && <button className="btn" disabled={busy} onClick={retry}>重试解析</button>}</div>}
      {detail.parse && (writable && detail.current_product_version === detail.product_version
        ? <DocumentEditor key={detail.parse.id} api={api} detail={detail} writable onProposed={onProposed}/>
        : <details><summary>查看解析原稿（不含人工修订）</summary><DocumentEditor key={detail.parse.id} api={api} detail={detail} writable={false} onProposed={onProposed}/></details>)}
    </>}
  </section>;
}

function ReparseDocument({api,detail,onQueued}: {api:WarehouseClient;detail:Detail;onQueued:()=>void}) {
  const [note,setNote]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const request=useDocumentRequest(api);
  async function submit(event:FormEvent) {
    event.preventDefault();setBusy(true);setError("");
    try {
      await request(`/documents/${detail.id}/reparse`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_parse_id:detail.parse!.id,note})});
      onQueued();
    } catch(err) {setError(message(err));} finally {setBusy(false);}
  }
  return <details><summary>重新解析已保存的原文件</summary>
    <form className="stack" onSubmit={submit}>
      <p className="muted">使用当前解析规则生成新稿，保留旧稿和复核记录。现有未发布提议将不能继续应用，须根据新稿重新复核。</p>
      {detail.product_version !== detail.current_product_version && <p className="notice warning">此文件绑定历史产品版本。重新解析只用于原版本核对；需要发布到当前产品时，请重新上传并复核。</p>}
      <Field label="重新解析原因"><textarea className="input" required maxLength={2000} disabled={busy} value={note} onChange={e=>setNote(e.target.value)} placeholder="说明本次需要重新核对或使用新版规则的原因"/></Field>
      {error && <p className="notice error" role="alert">{error}</p>}
      <button className="btn" disabled={busy || !note.trim()}>{busy ? "正在排队…" : "保留旧稿并重新解析"}</button>
    </form>
  </details>;
}

function ParseHistory({api,detail}: {api:WarehouseClient;detail:Detail}) {
  const [cursors,setCursors]=useState([""]);
  const [selected,setSelected]=useState("");
  const params=new URLSearchParams({limit:"10"});
  if(cursors.at(-1)) params.set("before",cursors.at(-1)!);
  const state=useData<Page<{id:string;generation:number;parser_version:string;created_at:string}>>(api,`/documents/${detail.id}/parses?${params}`);
  return <section className="stack" aria-label="解析版本记录"><LoadState {...state}/>
    {state.data && <><div className="table-wrap"><table><thead><tr><th>解析次数</th><th>完成时间</th><th>解析规则</th><th>操作</th></tr></thead><tbody>
      {state.data.items.map(row=><tr key={row.id}><td>第 {row.generation} 次{row.generation === detail.parse_generation ? " · 当前稿" : " · 历史稿"}</td><td>{time(row.created_at)}</td><td>{row.parser_version}</td><td><button type="button" className="link" onClick={()=>setSelected(row.id)}>查看第 {row.generation} 次解析</button></td></tr>)}
    </tbody></table></div>{!state.data.items.length && <p className="empty">尚无成功完成的解析稿。</p>}
      <Pagination next={state.data.next_cursor} previous={cursors.length>1} onNext={()=>{setCursors([...cursors,state.data!.next_cursor!]);setSelected("");}} onPrevious={()=>{setCursors(cursors.slice(0,-1));setSelected("");}}/>
    </>}
    {selected && <ParsedVersion key={selected} api={api} detail={detail} parseId={selected}/>}
  </section>;
}

function ParsedVersion({api,detail,parseId}: {api:WarehouseClient;detail:Detail;parseId:string}) {
  const state=useData<NonNullable<Detail["parse"]>>(api,`/documents/${detail.id}/parses/${parseId}`);
  return <section className="stack" aria-label="只读解析稿"><LoadState {...state}/>{state.data && <>
    <h3>第 {state.data.generation} 次解析 · 只读原稿</h3>
    <p className="notice">此处保留当次解析结果，不含人工修订，也不代表已发布内容。</p>
    <DocumentEvidence api={api} asset={detail.id} name={detail.file_name} hash={detail.file_hash} version={detail.product_version} parser={state.data.parser_version} locations={state.data.body.source.page_locations}/>
    <DocumentContent content={state.data.body}/>
  </>}</section>;
}

function PublishedDocument({api,publication,current}: {
  api:WarehouseClient;publication:Detail["publications"][number];current:boolean;
}) {
  const state=useData<{body:RouteContent;review_note:string}>(api,`/published-documents/${publication.id}`);
  return <section className="stack" aria-label="已发布文档内容">
    <h3>已发布文档 · 产品版本 {publication.product_version}</h3>
    <p className="notice">{time(publication.created_at)} · {current ? "当前有效版本" : "历史版本"}</p>
    <LoadState {...state}/>
    {state.data && <><DocumentContent content={state.data.body}/><p className="document-prose"><strong>复核说明：</strong>{state.data.review_note}</p></>}
  </section>;
}

export function DocumentEvidence({api,asset,name,hash,version,parser,locations}: {api:WarehouseClient;asset:string;name:string;hash:string;version:number;parser?:string;locations?:PageLocation[]}) {
  const [error,setError]=useState("");const [busy,setBusy]=useState(false);
  const request=useDocumentRequest(api);
  async function download() {
    setBusy(true);setError("");
    try {
      const blob=await (await request(`/documents/${asset}/file`)).blob();
      const url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=name;link.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(err){setError(message(err));}finally{setBusy(false);}
  }
  return <div className="stack"><div className="row between"><div><h3>{name}</h3><p className="muted">绑定产品版本 {version}</p></div><button className="btn" disabled={busy} onClick={download}>下载原文件</button></div>
    <details><summary>查看文件来源记录</summary><p className="document-prose muted">文件指纹：{hash}<br/>解析规则版本：{parser ?? "等待解析"}</p></details>
    {parser && (locations?.length ? <DocumentPagePreview key={asset} api={api} asset={asset} locations={locations}/> : <p className="muted">此解析稿没有原文页码记录，请下载原文件核对。DOCX 不提供固定页码。</p>)}
    {error && <p role="alert" className="notice error">{error}</p>}
  </div>;
}

function DocumentPagePreview({api,asset,locations}: {api:WarehouseClient;asset:string;locations:PageLocation[]}) {
  const [selection,setSelection]=useState<{page:number;request:number}|null>(null);
  const [url,setUrl]=useState("");
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    if(!selection) return;
    const controller=new AbortController();
    let objectUrl="", closed=false, checking=false;
    setUrl("");setError("");setBusy(true);
    const close=()=>{if(objectUrl) {URL.revokeObjectURL(objectUrl);objectUrl="";}};
    const fail=(err:unknown)=>{
      if(closed) return;
      closed=true;controller.abort();close();setUrl("");setBusy(false);setError(message(err));
    };
    async function load() {
      try {
        const response=await api.response(`/documents/${asset}/file`,{signal:controller.signal});
        if(response.headers.get("content-type")?.split(";")[0] !== "application/pdf") throw new Error("此原文件不是可预览的 PDF，请下载核对。");
        const blob=await response.blob();
        if(closed) return;
        objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);setBusy(false);
      } catch(err) {fail(err);}
    }
    async function checkAccess() {
      if(closed || checking) return;
      checking=true;
      try {await api.response(`/documents/${asset}`,{signal:controller.signal});}
      catch(err) {fail(err);} finally {checking=false;}
    }
    void load();
    const timer=window.setInterval(checkAccess,30000);
    window.addEventListener("focus",checkAccess);
    return ()=>{closed=true;controller.abort();close();window.clearInterval(timer);window.removeEventListener("focus",checkAccess);};
  },[api,asset,selection]);
  return <section className="stack" aria-label="PDF 原文页码定位">
    <h3>对照 PDF 原文</h3>
    <p className="muted">下列页码是 PDF 文件的实际页序，可能与纸面印刷页码不同。定位对应解析原稿的段落；复核后的修订及由其他段落补充的字段仍须人工核对。</p>
    {locations.map((location,index)=><div className="row" key={index}>
      <strong>{location.section === "itinerary" ? `解析原稿第 ${location.day} 天` : location.section === "cover" ? "封面与概述" : "费用与须知"}</strong>
      {location.pages.map(page=><button type="button" className="btn" key={page} onClick={()=>setSelection({page,request:Date.now()})}>查看第 {page} 页</button>)}
    </div>)}
    {busy && <p role="status">正在读取受权限保护的原文件…</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {url && selection && <div className="stack">
      <div className="row between"><p>原文件预览 · 第 {selection.page} 页</p><button type="button" className="btn" onClick={()=>{setSelection(null);setUrl("");setError("");}}>关闭原文预览</button></div>
      <iframe key={`${url}:${selection.page}`} src={`${url}#page=${selection.page}`} title={`PDF 原文件第 ${selection.page} 页`} style={{width:"100%",height:640,border:"1px solid var(--border)"}}/>
      <p className="muted">若浏览器无法显示或跳页，请下载原文件，按上述实际页序核对。</p>
    </div>}
  </section>;
}
function DocumentEditor({api,detail,writable,onProposed}: {api:WarehouseClient;detail:Detail;writable:boolean;onProposed:()=>void}) {
  const [content,setContent]=useState<RouteContent>(()=>structuredClone(detail.parse!.body));
  const [note,setNote]=useState("");const [confirmed,setConfirmed]=useState(false);
  const [error,setError]=useState("");const [busy,setBusy]=useState(false);
  const request=useDocumentRequest(api);
  async function submit(e:FormEvent) {
    e.preventDefault();setBusy(true);setError("");
    try {await request("/document-proposals",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target_id:detail.product_id,expected_version:detail.product_version,parse_id:detail.parse!.id,content,note})});onProposed();}
    catch(err){setError(message(err));}finally{setBusy(false);}
  }
  if(detail.parse!.body.schema === "route-kit/1")return <section className="stack"><p className="notice">请在线路详情的“行程编辑与发布”中修订和复核新行程，此处为解析原稿。</p><DocumentContent content={content}/></section>;
  return <form className="stack" onSubmit={submit}>
    <p className="notice warning">解析稿需要人工核对。每天的标题与行程须完整，行程天数须与产品一致。文档中的费用说明不替代当前团期报价。</p>
    {!!detail.parse!.body.quality.needs_review.length && <div className="notice warning"><strong>解析待核对项</strong><ul>{detail.parse!.body.quality.needs_review.map((item,i)=><li key={i}>{item}</li>)}</ul></div>}
    <fieldset disabled={busy} className="document-fieldset"><DocumentContent content={content} onChange={writable ? (updated)=>{setContent(updated);setConfirmed(false);} : undefined}/></fieldset>
    {writable && <><DocumentDiff before={detail.parse!.body} after={content}/><Field label="复核说明"><textarea className="input" required maxLength={2000} value={note} disabled={busy} onChange={(e)=>{setNote(e.target.value);setConfirmed(false);}} placeholder="说明已核对的原文件、修订内容及仍需提示客户的未知事项"/></Field>
      <label className="row"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(e)=>setConfirmed(e.target.checked)}/>我已核对原文件、产品版本和全部行程内容</label>
      {error && <p className="notice error" role="alert">{error}</p>}<button className="btn primary" disabled={busy || !confirmed || !note.trim()}>{busy?"正在提交…":"提交文档复核审批"}</button>
    </>}
  </form>;
}

export function DocumentApproval({api,change,onReady}: {api:WarehouseClient;change:Change;onReady:(ready:boolean)=>void}) {
  const source=change.document_source!;
  const state=useData<Detail>(api,`/documents/${source.asset_id}`);
  const original=useData<NonNullable<Detail["parse"]>>(api,`/documents/${source.asset_id}/parses/${change.payload.parse_id}`);
  const valid=!!state.data && !!original.data && original.data.id === change.payload.parse_id && state.data.product_id === change.payload.target_id && state.data.file_hash === source.file_hash;
  const current = change.status !== "staged" || state.data?.current_product_version === change.payload.expected_version;
  const latest = change.status !== "staged" || state.data?.parse?.id === change.payload.parse_id;
  useEffect(()=>{onReady(valid && current && latest);return ()=>onReady(false);},[valid,current,latest,onReady]);
  return <section className="stack" aria-label="文档发布审批">
    <DocumentEvidence api={api} asset={source.asset_id} name={source.file_name} hash={source.file_hash} version={source.product_version} parser={source.parser_version} locations={valid ? original.data!.body.source.page_locations : undefined}/>
    <LoadState {...state}/><LoadState {...original}/>
    {state.data && !original.loading && !valid && <p role="alert" className="notice error">文档来源与审批引用的解析稿不一致，不能应用。请刷新核对。</p>}
    {valid && !current && <p className="notice error" role="alert">产品版本已变化，此审批不能应用。请按当前版本重新上传并复核。</p>}
    {valid && !latest && <p className="notice error" role="alert">原文件已重新解析，此提议基于旧稿，不能继续应用。请根据最新解析稿重新复核。</p>}
    {valid && <><p className="muted">本次审批引用第 {original.data!.generation} 次解析稿；修订对照始终使用当时的原稿。</p>
      {change.status === "staged" ? <p className="notice warning">将以下完整文档发布给获授权的采购组织。请同时核对原文件与修订对照；此次发布不调整团期价格或库存。</p> : <p className="notice">已处理审批记录：以下为当时提交的内容，不因后续重新解析而变化。</p>}
      <DocumentDiff before={original.data!.body} after={change.payload.content}/>
      <h3>提交复核的完整内容</h3><DocumentContent content={change.payload.content}/>
      <p className="document-prose"><strong>复核说明：</strong>{change.payload.note}</p>
    </>}
  </section>;
}
