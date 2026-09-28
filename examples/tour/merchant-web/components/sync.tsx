"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { message, WarehouseClient } from "../lib/api";
import { Badge, Field, LoadState, Pagination, useData, usePolling } from "./common";

import { OperationsOverview } from "./operations";

type Page<T> = { items: T[]; next_cursor: string | null };
const time = (value:string) => new Date(value).toLocaleString("zh-CN", {
  timeZone:"Asia/Shanghai", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit",
});
type Job = {
  id: string; connection_id: string; source_name: string; source_active: boolean;
  enabled: boolean; config_version: number; status: string;
  interval_seconds: number; attempts: number; max_attempts: number;
  next_run_at: string; lease_until: string | null; last_error: string | null;
  completed_count: number; worker_last_seen_at: string | null;
  provider_not_before: string | null;
};
type Run = {
  id: string; connection_id: string; source_name: string; status: string;
  started_at: string; completed_at: string | null; product_count: number;
  source_departure_count: number; departure_count: number;
  quarantined_count: number; error_code: string | null;
};

function failure(code: string | null) {
  if (!code) return "—";
  return ({
    UNLINKED_ROUTE: "缺少有效所属线路，已隔离",
    INCOMPLETE_SOURCE_SCAN: "来源数据未读取完整，保留已发布目录",
    SOURCE_CHANGED_DURING_SCAN: "读取期间来源数据变化，将重新读取",
    PAGINATION_LIMIT_OR_INVALID: "来源分页信息异常或超过安全上限",
    PAGINATION_METADATA_MISSING: "来源没有提供完整分页信息",
    SOURCE_REFERENCE_MISSING: "团期引用的线路不存在",
    DUPLICATE_SOURCE_ID: "来源编号重复",
    INTERRUPTED: "上次执行中断，请核对任务状态与重试次数",
    SCAN_TIMEOUT: "本次同步超时",
    ACCESS_REVOKED: "同步账号或来源授权已失效",
    SYNC_FAILED: "同步失败，请联系管理员核对日志",
    SYNC_BUSY: "来源正在同步，稍后重试",
    SOURCE_RATE_LIMITED: "上游限流，已安排冷却后重试",
    SOURCE_COOLDOWN_ACTIVE: "上游冷却尚未结束，保留已发布目录",
    SOURCE_AUTH_FAILED: "上游登录失败，请核对接入账号后人工重试",
    SOURCE_PERMISSION_DENIED: "上游权限不足，请联系供应商确认授权",
    SOURCE_NOT_FOUND: "上游接口或记录不存在，请核对接口配置",
    SOURCE_REQUEST_REFUSED: "上游拒绝请求，请核对接口参数及业务规则",
    SOURCE_UNAVAILABLE: "上游暂时不可用，按退避计划重试",
    SOURCE_COOLDOWN_REQUIRES_REVIEW: "上游要求等待超过七天，已停止自动重试，请人工核对",
  } as Record<string, string>)[code] ?? `同步异常（${code}）`;
}

export function SyncHistory({ api, revision, writable }: {
  api: WarehouseClient; revision: number; writable: boolean;
}) {
  const [jobPages, setJobPages] = useState<(string | null)[]>([null]);
  const [historyPages, setHistoryPages] = useState<(string | null)[]>([null]);
  const [source, setSource] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [localRevision, setLocalRevision] = useState(0);
  const refresh = () => setLocalRevision((value) => value + 1);
  const cursor = jobPages.at(-1);
  const jobs = usePolling<Page<Job>>(api, `/sync-jobs?limit=10${cursor ? `&after=${cursor}` : ""}`, revision + localRevision);
  const before = historyPages.at(-1);
  const runs = useData<Page<Run>>(api, `/sync-runs?limit=25${before ? `&before=${before}` : ""}${source ? `&connection_id=${source}` : ""}`, revision + localRevision);
  return <div className="stack">
    <OperationsOverview api={api} revision={revision + localRevision} />
    <section className="panel stack">
      <div className="row between"><h2>同步任务</h2><button className="btn" onClick={refresh}>刷新状态</button></div>
      <p className="muted">本页时间均为北京时间。任务状态每 10 秒更新。暂停只停止后续调度，当前批次可以完成。立即同步会加入队列，须由后台进程执行。</p>
      <LoadState {...jobs} />
      {jobs.data?.items.map((job) => <JobCard key={job.id} api={api} job={job} writable={writable} onChanged={refresh}
        onHistory={() => { setSource(job.connection_id); setHistoryPages([null]); setRun(null); }} />)}
      {jobs.data?.items.length === 0 && <p className="empty">暂无已注册的同步任务。请由运维人员配置供应源与后台同步进程。</p>}
      <Pagination previous={jobPages.length > 1} next={jobs.data?.next_cursor}
        onPrevious={() => setJobPages((pages) => pages.slice(0, -1))}
        onNext={() => { if (jobs.data?.next_cursor) setJobPages((pages) => [...pages, jobs.data!.next_cursor]); }} />
    </section>
    <section className="panel stack">
      <div className="row between"><h2>{source ? "所选来源的同步历史" : "全部同步历史"}</h2>
        {source && <button className="btn" onClick={() => { setSource(""); setHistoryPages([null]); setRun(null); }}>显示全部来源</button>}
      </div>
      <p className="muted">完整性校验失败不会替换已发布目录；缺少线路关联的团期单独保留，等待供应商处理。</p>
      <LoadState {...runs} />
      {runs.data && <div className="table-wrap"><table><thead><tr>
        <th>时间 / 数据源</th><th>状态</th><th>线路</th><th>来源团期</th><th>发布团期</th><th>隔离</th><th>详情</th>
      </tr></thead><tbody>{runs.data.items.map((row) => <tr key={row.id}>
        <td>{time(row.started_at)}<br /><span className="muted">{row.source_name}</span></td>
        <td><Badge status={row.status} />{row.error_code && <p>{failure(row.error_code)}</p>}</td>
        <td>{row.product_count}</td><td>{row.source_departure_count}</td><td>{row.departure_count}</td><td>{row.quarantined_count}</td>
        <td><button className="link" onClick={() => setRun(row)}>查看异常</button></td>
      </tr>)}</tbody></table></div>}
      {runs.data?.items.length === 0 && <p className="empty">暂无同步记录。</p>}
      <Pagination previous={historyPages.length > 1} next={runs.data?.next_cursor}
        onPrevious={() => { setHistoryPages((pages) => pages.slice(0, -1)); setRun(null); }}
        onNext={() => { if (runs.data?.next_cursor) { setHistoryPages((pages) => [...pages, runs.data!.next_cursor]); setRun(null); } }} />
    </section>
    {run && <SourceIssues key={run.id} api={api} run={run} revision={revision + localRevision} onClose={() => setRun(null)} />}
  </div>;
}

function JobCard({ api, job, writable, onChanged, onHistory }: {
  api: WarehouseClient; job: Job; writable: boolean; onChanged: () => void; onHistory: () => void;
}) {
  const [edit, setEdit] = useState<{ action: "configure" | "run_now" | "retry"; snapshot: Job } | null>(null);
  const [notice, setNotice] = useState("");
  const cooling = !!job.provider_not_before && new Date(job.provider_not_before).getTime() > Date.now();
  return <article className="batch stack">
    <div className="row between"><h3>{job.source_name}</h3><span>{job.enabled ? "已启用" : "已暂停"} · {({ waiting: "等待执行", running: "执行中", failed: "失败停止" } as Record<string,string>)[job.status] ?? job.status}</span></div>
    {!job.source_active && <p className="notice error">供应源已停用，任务不会执行。</p>}
    <p className="muted">间隔 {job.interval_seconds} 秒 · 连续尝试 {job.attempts} / {job.max_attempts} · 累计成功 {job.completed_count} 次</p>
    <p>最近后台心跳：{job.worker_last_seen_at ? time(job.worker_last_seen_at) : "尚未收到"}<br />
      <span className="muted">心跳时间只表示最近活动，不保证进程仍在运行。</span></p>
    <p>下次计划：{!job.enabled ? "已暂停" : job.status === "failed" ? "等待人工重试" : job.status === "running" ? "当前批次完成后安排" : time(job.next_run_at)}</p>
    {job.last_error && <p className="notice error">{failure(job.last_error)}</p>}
    {cooling && <p className="notice warning">上游要求等待至 {new Date(job.provider_not_before!).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai"})}（北京时间）。立即排队、重启或修改周期都不会提前执行。</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    <div className="row">
      <button className="btn" onClick={onHistory}>查看该来源历史</button>
      {writable && <>
        <button className="btn" disabled={!!edit} onClick={() => { setNotice(""); setEdit({ action: "configure", snapshot: job }); }}>配置任务</button>
        <button className="btn" disabled={!!edit || !job.enabled || !job.source_active || job.status !== "waiting"}
          onClick={() => { setNotice(""); setEdit({ action: "run_now", snapshot: job }); }}>立即同步</button>
        {job.status === "failed" && <button className="btn" disabled={!!edit || !job.enabled || !job.source_active}
          onClick={() => { setNotice(""); setEdit({ action: "retry", snapshot: job }); }}>重试任务</button>}
      </>}
    </div>
    {edit && <ControlForm api={api} job={edit.snapshot} latest={job} action={edit.action}
      onCancel={() => setEdit(null)} onSuccess={() => {
        setNotice(edit.action === "configure" ? "配置已保存并记录操作说明。" : "已加入同步队列，等待后台进程执行。请查看状态与历史确认结果。");
        setEdit(null); onChanged();
      }} />}
  </article>;
}

function ControlForm({ api, job, latest, action, onCancel, onSuccess }: {
  api: WarehouseClient; job: Job; latest: Job; action: "configure" | "run_now" | "retry";
  onCancel: () => void; onSuccess: () => void;
}) {
  const [interval, setInterval] = useState(String(job.interval_seconds));
  const [attempts, setAttempts] = useState(String(job.max_attempts));
  const [enabled, setEnabled] = useState(job.enabled);
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), [api]);
  const stale = job.config_version !== latest.config_version;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || stale) return;
    const controller = new AbortController(); request.current = controller;
    setPending(true); setError("");
    try {
      await api.response(`/sync-jobs/${job.id}/control`, {
        method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, version: job.config_version, note: note.trim(),
          ...(action === "configure" ? { interval_seconds: Number(interval), max_attempts: Number(attempts), enabled } : {}),
        }),
      });
      if (!controller.signal.aborted) onSuccess();
    } catch (failure) { if (!controller.signal.aborted) setError(message(failure)); }
    finally { if (!controller.signal.aborted) setPending(false); }
  }
  return <form className="stack" onSubmit={submit}>
    <h4>{action === "configure" ? "调度配置" : action === "retry" ? "重新排队失败任务" : "立即排队同步"}</h4>
    {action === "configure" && <>
      <div className="form-grid">
        <Field label="同步间隔（秒，60–86400）"><input type="number" required min={60} max={86400} step={1} value={interval}
          disabled={pending || latest.status === "running"} onChange={(event) => setInterval(event.target.value)} /></Field>
        <Field label="最多连续尝试（1–10）"><input type="number" required min={1} max={10} step={1} value={attempts}
          disabled={pending || latest.status === "running"} onChange={(event) => setAttempts(event.target.value)} /></Field>
      </div>
      <Field label="调度开关"><select value={enabled ? "enabled" : "paused"} disabled={pending} onChange={(event) => setEnabled(event.target.value === "enabled")}>
        <option value="enabled">启用</option><option value="paused">暂停后续同步</option>
      </select></Field>
      {latest.status === "running" && <p className="muted">执行期间可暂停后续任务；修改间隔和尝试次数须等待当前批次完成。</p>}
    </>}
    <Field label="操作说明（必填）"><textarea required maxLength={500} value={note} disabled={pending} onChange={(event) => setNote(event.target.value)} /></Field>
    {stale && <p className="notice error" role="alert">其他操作已修改配置。请取消后重新打开，核对最新配置。</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    <div className="row"><button className="btn primary" disabled={pending || stale || !note.trim()}>{pending ? "正在提交…" : action === "configure" ? "保存配置" : "确认排队"}</button>
      <button className="btn" type="button" disabled={pending} onClick={onCancel}>取消</button></div>
  </form>;
}

function SourceIssues({ api, run, revision, onClose }: { api: WarehouseClient; run: Run; revision: number; onClose: () => void }) {
  const [pages, setPages] = useState<(string | null)[]>([null]);
  const [selected, setSelected] = useState<string | null>(null);
  const after = pages.at(-1);
  const state = useData<Page<{ id: string; external_id: string; code: string }>>(api,
    `/source-issues?run_id=${run.id}&limit=50${after ? `&after=${after}` : ""}`, revision);
  return <section className="panel stack">
    <div className="row between"><h2>同步异常记录</h2><button className="btn" onClick={onClose}>关闭详情</button></div>
    <p className="muted">{run.source_name} · {time(run.started_at)} · 批次 {run.id}</p>
    {run.error_code && <p className="notice error">{failure(run.error_code)}</p>}
    <LoadState {...state} />
    {state.data?.items.map((row) => <div className="row between" key={row.id}>
      <p>团期编号 {row.external_id}：{failure(row.code)}</p>
      <button className="btn" aria-pressed={selected === row.id} onClick={() => setSelected(row.id)}>查看团期 {row.external_id} 原因</button>
    </div>)}
    {state.data?.items.length === 0 && <p className="empty">本批次没有团期隔离记录。</p>}
    <Pagination previous={pages.length > 1} next={state.data?.next_cursor}
      onPrevious={() => { setPages((value) => value.slice(0, -1)); setSelected(null); }}
      onNext={() => { if (state.data?.next_cursor) { setPages((value) => [...value, state.data!.next_cursor]); setSelected(null); } }} />
    {selected && state.data?.items.some((row) => row.id === selected) && <SourceIssueDetail key={selected} api={api} issueId={selected} revision={revision} />}
  </section>;
}

type Observation = { period_code: string; depart_date: string | null; return_date: string | null; route_id: number | null };
type IssueDetail = {
  external_id: string; source_name: string; source_active: boolean; code: string;
  original: Observation | null; original_observed_at: string | null;
  latest_successful_scan: {
    run_id: string; completed_at: string; window_start: string | null; window_end: string | null;
    observation: Observation | null; observed_at: string | null; state: "unlinked" | "linked" | "not_observed";
  } | null;
  latest_attempt: { run_id: string; status: string; started_at: string; error_code: string | null } | null;
};

function SourceIssueDetail({ api, issueId, revision }: { api: WarehouseClient; issueId: string; revision: number }) {
  const [refresh, setRefresh] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const focused = useRef(false);
  const state = useData<IssueDetail>(api, `/source-issues/${issueId}`, revision + refresh);
  useEffect(() => {
    if (state.data && !focused.current) {
      focused.current = true;
      heading.current?.focus({ preventScroll: true });
      heading.current?.scrollIntoView({ block: "start" });
    }
  }, [state.data]);
  useEffect(() => {
    const update = () => setRefresh((value) => value + 1);
    window.addEventListener("focus", update);
    const timer = window.setInterval(update, 30000);
    return () => { window.removeEventListener("focus", update); window.clearInterval(timer); };
  }, [api, issueId]);
  const detail = state.data;
  const latest = detail?.latest_successful_scan;
  function observation(value: Observation | null) {
    return value ? <dl className="stack">
      <div><dt className="muted">来源团号</dt><dd style={{ overflowWrap: "anywhere" }}>{value.period_code || "未提供"}</dd></div>
      <div><dt className="muted">出发 / 返回日期</dt><dd>{value.depart_date ?? "未提供"} / {value.return_date ?? "未提供"}</dd></div>
      <div><dt className="muted">来源线路编号</dt><dd>{value.route_id === 0 ? "未关联（来源值 0）" : value.route_id ?? "未提供"}</dd></div>
    </dl> : <p className="muted">此批次没有对应的来源记录，不能据此认定已修复或已删除。</p>;
  }
  return <article className="batch stack" aria-labelledby={`issue-${issueId}`}>
    <div className="row between"><h3 id={`issue-${issueId}`} ref={heading} tabIndex={-1}>团期异常核对</h3>
      <button className="btn" disabled={state.loading} onClick={() => setRefresh((value) => value + 1)}>刷新核对结果</button></div>
    <LoadState {...state} />
    {detail && <>
      <p>{detail.source_name} · 来源团期编号 {detail.external_id}</p>
      {!detail.source_active && <p className="notice warning">供应源已停用。以下为历史观测，不能据此判断当前可售状态。</p>}
      <div className="form-grid">
        <section className="stack"><h4>本次异常的来源记录</h4>
          <p className="muted">{detail.original_observed_at ? `观测于 ${time(detail.original_observed_at)}（北京时间）` : "没有对应来源快照"}</p>
          <p>{failure(detail.code)}</p>{observation(detail.original)}</section>
        <section className="stack"><h4>最近成功同步的结果</h4>
          {latest ? <>
            <p className="muted">完成于 {time(latest.completed_at)}（北京时间）<br />扫描范围：{latest.window_start ?? "不限起始日期"} 至 {latest.window_end ?? "不限结束日期"}</p>
            <p className={`notice ${latest.state === "linked" ? "" : "warning"}`} role="status">{{ unlinked: "仍缺少线路关联，继续隔离", linked: "已恢复线路关联，请重新核对价格和库存", not_observed: "最近成功批次未返回此团期，尚不能确认已修复" }[latest.state]}</p>
            {observation(latest.observation)}
          </> : <p className="muted">暂无成功同步批次，不能确认修复结果。</p>}
        </section>
      </div>
      {detail.latest_attempt && detail.latest_attempt.run_id !== latest?.run_id && <p className="notice warning">
        最新尝试开始于 {time(detail.latest_attempt.started_at)}（北京时间），{detail.latest_attempt.status === "running" ? "仍在执行" : failure(detail.latest_attempt.error_code)}。上方结果来自最近成功批次。
      </p>}
      <section className="stack"><h4>处理步骤</h4>
        <ol><li>在供应商原系统中，按来源团期编号或团号查找计划，核对所属线路及出发日期。</li>
          <li>在原系统修正线路关联并保存；返回同步任务，等待下一轮同步，或由供应商管理员立即排队同步。</li>
          <li>同步成功后刷新本详情，核对最新结果。恢复关联不代表有位或允许销售，仍需重新查询价格、库存与停售条件。</li></ol>
        <p className="muted">异常历史保留。页面每 30 秒及重新聚焦时核对权限与结果。</p>
      </section>
    </>}
  </article>;
}
