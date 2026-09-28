"use client";

import { WarehouseClient } from "../lib/api";
import { LoadState, usePolling } from "./common";

type Observation = {
  observed_at: string; status: "ok" | "warning" | "critical";
  issues: { code: string; severity: string; message: string; action: string; resource_id: string | null; count: number | null }[];
  sources: { id: string; name: string; monitored: boolean; runs_24h: number; successes_24h: number; last_success_at: string | null }[];
  inventory: { departures: number; expired: number; unknown: number };
  ledger: { pools: number; mismatches: number };
  documents: { fetch_failed: number; parse_failed: number; awaiting_review: number; failure_stages?:{code:string;count:number}[] };
  search: { pending: number; failed: number; projection: { products: number; current: number; stale_or_missing: number } };
  not_monitored: string[];
};

const time = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });

export function OperationsOverview({ api, revision }: { api: WarehouseClient; revision: number }) {
  const state = usePolling<Observation>(api, "/operations", revision);
  const data = state.data;
  const names = new Map(data?.sources.map((source) => [source.id, source.name]));
  return <section className="panel stack" aria-label="运行监测">
    <h2>运行监测</h2>
    <p className="muted">每 10 秒刷新当前组织的观测结果。异常提示供人工核查，不会自动修改库存或重试业务操作。</p>
    <LoadState {...state} />
    {data && <>
      <p className={`notice ${state.error || data.status === "critical" ? "error" : data.status === "warning" ? "warning" : ""}`} role="status">
        {state.error ? "更新失败，以下为上次观测，无法确认当前状态。" : data.status === "critical" ? "有需要优先处理的异常。" : data.status === "warning" ? "有需要核查的运行提醒。" : "已监测项目未发现当前异常。"}
        <br /><span className="muted">观测时间：{time(data.observed_at)}（北京时间）</span>
      </p>
      <div className="form-grid">
        <article className="panel stack"><h3>外部库存时效</h3>
          <p>未来已发布团期 {data.inventory.departures} 个 · 过期 {data.inventory.expired} 个 · 未知数量 {data.inventory.unknown} 个</p>
          <p className="muted">过期与未知可能重叠。未过期也仅代表上游快照，询价时仍须核实。</p>
        </article>
        <article className="panel stack"><h3>托管库存核对</h3>
          <p>库存池 {data.ledger.pools} 个 · 账本差异 {data.ledger.mismatches} 个</p>
          <p className="muted">比较库存余额、版本与完整流水；存在差异时须人工核对。</p>
        </article>
        <article className="panel stack"><h3>检索更新</h3>
          <p>版本一致 {data.search.projection.current} / {data.search.projection.products} 条线路</p>
          <p>待处理 {data.search.pending} 条 · 失败 {data.search.failed} 条</p>
        </article>
        <article className="panel stack"><h3>当前版本文档</h3>
          <p>下载失败 {data.documents.fetch_failed} 个 · 解析失败 {data.documents.parse_failed} 个</p>
          {data.documents.failure_stages?.map(item=><p key={item.code}>{({DOCUMENT_PARSE_OPEN_FAILED:"打开文件失败",DOCUMENT_PARSE_SEGMENT_FAILED:"行程分段失败",DOCUMENT_PARSE_DAYS_FAILED:"逐日读取失败",DOCUMENT_PARSE_TERMS_FAILED:"条款读取失败",DOCUMENT_PARSE_VALIDATION_FAILED:"结构校验失败",DOCUMENT_TEXT_MISSING:"缺少文字层",DOCUMENT_TEXT_INSUFFICIENT:"文字不足",DOCUMENT_EXTRACTION_RETIRED:"旧抽取任务已停用，请重新解析"} as Record<string,string>)[item.code]||"解析未完成"}：{item.count} 个</p>)}
          <p>已解析待复核 {data.documents.awaiting_review} 个文件</p>
        </article>
      </div>
      {data.issues.map((issue, index) => <article className={`notice ${issue.severity === "critical" ? "error" : issue.severity === "warning" ? "warning" : ""}`} key={`${issue.code}-${issue.resource_id ?? index}`}>
        <strong>{issue.message}{issue.count !== null ? `（${issue.count}）` : ""}</strong>
        {issue.resource_id && names.has(issue.resource_id) && <p>供应源：{names.get(issue.resource_id)}</p>}
        <p>{issue.action}</p>
      </article>)}
      {data.sources.filter((source) => source.monitored).map((source) => <p key={source.id}>
        <strong>{source.name}</strong> · 近 24 小时完成 {source.runs_24h} 次同步，其中成功 {source.successes_24h} 次。<br />
        <span className="muted">最近成功：{source.last_success_at ? time(source.last_success_at) : "尚无记录"}</span>
      </p>)}
      <p className="muted">尚未接入集中监测：{data.not_monitored.join("、")}。未监测不代表正常。</p>
    </>}
  </section>;
}
