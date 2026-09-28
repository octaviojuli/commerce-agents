"use client";

import { useState } from "react";
import { Change, WarehouseClient, message, time } from "../lib/api";
import { SchedulePreview, businessTime } from "./prices";
import { Badge, LoadState, Pagination, useData } from "./common";
import { DocumentApproval } from "./documents";
import { RouteContentApproval } from "./route-editor";

const kinds: Record<string, string> = {
  offer: "报价方案维护",
  price_book: "价表与采购等级",
  departure_sales: "团期销售规则",
  document: "文档复核发布",
  route_content: "线路内容发布",
  route_tags: "线路标签维护",
  excel_import: "团期导入",
  inventory: "库存变更",
  merchant: "商户批量变更",
  catalog: "线路修改",
  product_display: "云仓展示补充",
  pricing: "价格与客户映射",
};
const fields: Record<string, string> = {
  title: "线路名称",
  long_description: "线路介绍",
  status: "上架状态",
  stock: "可用库存",
  price: "成人结算价",
};
const operations: Record<string, string> = {
  sale: "登记线下销售",
  adjust: "调整库存总量",
  block: "登记停售",
  reverse: "冲正原流水",
  open: "期初交接",
};
const display = (value: unknown) =>
  value == null
    ? "未设置"
    : ({
        active: "已上架",
        paused: "已暂停",
        published: "已发布",
        draft: "草稿",
      }[String(value)] ?? String(value));

type ImportSalesRule = {
  departure_code: string;
  before_paused: boolean;
  sales_paused: boolean;
  before_deadline: string | null;
  local_booking_deadline: string | null;
  reason: string;
  business_timezone: string;
};

export function Approvals({
  api,
  revision,
  approver,
  onRefresh,
}: {
  api: WarehouseClient;
  revision: number;
  approver: boolean;
  onRefresh: () => void;
}) {
  const [selected, setSelected] = useState("");
  const [all, setAll] = useState(false);
  const [cursors, setCursors] = useState<string[]>([""]);
  const state = useData<{ items: Change[]; next_cursor: string | null }>(
    api,
    `/changes?limit=25${all ? "" : "&status=staged"}${cursors.at(-1) ? `&before=${cursors.at(-1)}` : ""}`,
    revision,
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const change = state.data?.items.find((row) => row.id === selected);
  const list = state.data?.items ?? [];
  function filter(includeProcessed: boolean) {
    setAll(includeProcessed);
    setCursors([""]);
    setSelected("");
  }
  async function act(row: Change, apply: boolean) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (apply) {
        await api.post(`/changes/${row.id}/approve`, {
          payload_hash: row.payload_hash,
        });
        await api.post(`/changes/${row.id}/apply`);
        setNotice("审批内容已应用，业务数据已更新。");
      } else {
        await api.post(`/changes/${row.id}/discard`);
        setNotice("变更已作废。");
      }
      setSelected("");
      setCursors([""]);
    } catch (error) {
      setError(
        `${message(error)} 请核对刷新后的状态；已应用的变更无需重复提交。`,
      );
    } finally {
      setBusy(false);
      onRefresh();
    }
  }
  return (
    <>
      <p className="muted">
        核对具体对象、数量与内容后应用。可翻页查看全部记录，待审批范围由云仓筛选。
      </p>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <div className="row">
        <button
          className="btn"
          aria-pressed={!all}
          disabled={busy}
          onClick={() => filter(false)}
        >
          待审批
        </button>
        <button
          className="btn"
          aria-pressed={all}
          disabled={busy}
          onClick={() => filter(true)}
        >
          含已处理
        </button>
      </div>
      <LoadState {...state} />
      {state.data && (
        <section className="panel table-wrap">
          <table>
            <thead>
              <tr>
                <th>提交时间</th>
                <th>变更类型</th>
                <th>内容</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {list.map((row) => (
                <tr key={row.id}>
                  <td>{time(row.created_at)}</td>
                  <td>{kinds[row.kind] ?? "其他变更"}</td>
                  <td>
                    {row.target_label ??
                      row.payload.summary ??
                      (row.kind === "pricing"
                        ? "采购协议与映射变更"
                        : kinds[row.kind])}
                  </td>
                  <td>
                    <Badge status={row.status} />
                  </td>
                  <td>
                    <button
                      className="link"
                      disabled={busy}
                      onClick={() => setSelected(row.id)}
                    >
                      核对内容
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!list.length && (
            <p className="empty">暂无{all ? "" : "待审批"}变更。</p>
          )}
          {!busy && (
            <Pagination
              next={state.data.next_cursor}
              previous={cursors.length > 1}
              onNext={() => {
                setSelected("");
                setCursors([...cursors, state.data!.next_cursor!]);
              }}
              onPrevious={() => {
                setSelected("");
                setCursors(cursors.slice(0, -1));
              }}
            />
          )}
        </section>
      )}
      {change && (
        <Review
          key={`${change.id}:${change.payload_hash}:${revision}`}
          change={change}
          api={api}
          busy={busy}
          approver={approver}
          onApply={() => act(change, true)}
          onDiscard={() => act(change, false)}
          onClose={() => setSelected("")}
        />
      )}
    </>
  );
}

function Review({
  api,
  change,
  busy,
  approver,
  onApply,
  onDiscard,
  onClose,
}: {
  api: WarehouseClient;
  change: Change;
  busy: boolean;
  approver: boolean;
  onApply: () => void;
  onDiscard: () => void;
  onClose: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [documentReady, setDocumentReady] = useState(false);
  const [page, setPage] = useState(0);
  const payload = change.payload;
  const priceBuyers = new Set<string>(
    (payload.commands ?? [])
      .map(
        (command: { body?: { buyer_org_id?: string } }) =>
          command.body?.buyer_org_id,
      )
      .filter(Boolean),
  );
  const supported =
    ["route_content", "route_tags", "product_display", "offer", "excel_import", "inventory", "merchant", "catalog", "pricing", "price_book", "document", "departure_sales"].includes(
      change.kind,
    ) &&
    (change.kind !== "product_display" || (!!change.target_label && !!payload.source && !!payload.before)) &&
    (change.kind !== "offer" || (!!change.target_label && !!payload.scope?.inventory_pool_id && Object.hasOwn(payload,"before"))) &&
    (change.kind !== "inventory" || !!change.target_label) &&
    (change.kind !== "departure_sales" || !!change.target_label) &&
    (change.kind !== "price_book" || (!!change.target_label && !!payload.snapshot &&
      Object.hasOwn(payload, "before") && ["price_window", "buyer_grade"].includes(payload.action) &&
      (!payload.buyer_org_id || !!change.buyer_name))) &&
    (change.kind !== "route_content" || (!!change.target_label && !!payload.revision_id && documentReady)) &&
    (change.kind !== "route_tags" || (!!change.target_label && Array.isArray(payload.tags))) &&
    (change.kind !== "document" || (!!change.target_label && !!change.document_source && documentReady)) &&
    (payload.action !== "reverse" || !!change.reversed_movement) &&
    (payload.kind !== "price_update" ||
      (priceBuyers.size > 0 &&
        [...priceBuyers].every((id) => !!change.buyer_names?.[id]))) &&
    (change.kind !== "pricing" ||
      (!!change.buyer_name &&
        !!change.target_label &&
        !!payload.grant_snapshot &&
        ["contract", "binding"].includes(payload.action)));
  const rows = (payload.rows ?? []) as Record<string, string | number | null>[];
  const salesRules = new Map<string, ImportSalesRule>(
    (payload.sales_rules ?? []).map((rule: ImportSalesRule) => [rule.departure_code, rule]),
  );
  const movement = change.reversed_movement;
  return (
    <section className="panel details stack" aria-label="变更内容预览">
      <div className="row between">
        <div>
          <div className="eyebrow">变更内容预览</div>
          <h2>
            {change.target_label ?? payload.summary ?? kinds[change.kind]}
          </h2>
        </div>
        <button className="btn" disabled={busy} onClick={onClose}>
          收起
        </button>
      </div>
      {change.kind === "departure_sales" && (
        <div className="stack">
          <p className="notice warning">此变更控制云仓询价资格，审批后生效。账面余位保持不变；恢复询价仍需核实供应商报名条件。</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>规则</th><th>原值</th><th>修改为</th></tr></thead>
              <tbody>
                <tr><td>云仓停售</td><td>{payload.before_paused ? "已停售" : "未停售"}</td><td>{payload.sales_paused ? "停售" : "解除停售"}</td></tr>
                <tr><td>云仓报名截止（北京时间）</td><td>{payload.before_deadline ? businessTime(payload.before_deadline) : "未设置"}</td><td>{payload.local_booking_deadline ? businessTime(payload.local_booking_deadline) : "未设置"}</td></tr>
              </tbody>
            </table>
          </div>
          <p>团期业务时区：{payload.business_timezone} · 团期版本：{payload.expected_version}</p>
          <p>修改原因：{payload.reason}</p>
        </div>
      )}
      {change.kind === "excel_import" && (
        <>
          <p>
            共 {rows.length}{" "}
            条团期。首次交接设置期初余额；已有团期保留销售与停售流水，更新线路信息和库存总量。销售规则空白保留现值；明确更改的规则在下表单独核对。
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>线路编号 / 名称</th>
                  <th>天数 / 出发地</th>
                  <th>团期编号</th>
                  <th>出发 / 返回</th>
                  <th>总量</th>
                  <th>期初已售</th>
                  <th>期初停售</th>
                  <th>云仓销售规则变更</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(page * 50, (page + 1) * 50).map((row, i) => (
                  <tr key={i}>
                    <td>
                      {row.product_code}
                      <br />
                      {row.product_name}
                    </td>
                    <td>
                      {row.days} 天 / {row.gateway}
                    </td>
                    <td>{row.departure_code}</td>
                    <td>
                      {row.depart_date}
                      <br />
                      {row.return_date}
                    </td>
                    <td>{row.total}</td>
                    <td>{row.initial_sold ?? "保留现值"}</td>
                    <td>{row.initial_blocked ?? "保留现值"}</td>
                    <td>
                      {salesRules.has(String(row.departure_code)) ? <ImportSalesPreview rule={salesRules.get(String(row.departure_code))!} /> : "不修改销售规则"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > 50 && (
            <div className="row">
              <button
                className="btn"
                disabled={!page}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </button>
              <span>
                {page + 1} / {Math.ceil(rows.length / 50)}
              </span>
              <button
                className="btn"
                disabled={(page + 1) * 50 >= rows.length}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
      {change.kind === "inventory" && (
        <>
          <div className="form-grid">
            <div>
              <span className="muted">业务操作</span>
              <p>{operations[payload.action] ?? payload.action}</p>
            </div>
            <div>
              <span className="muted">数量</span>
              <p>
                {payload.action === "reverse"
                  ? "按原流水冲回"
                  : payload.quantity}
              </p>
            </div>
            <div>
              <span className="muted">业务编号</span>
              <p>{payload.business_key}</p>
            </div>
            <div>
              <span className="muted">原因</span>
              <p>{payload.reason}</p>
            </div>
          </div>
          {payload.action === "open" && (
            <p>
              期初已售 {payload.initial_sold}，期初停售{" "}
              {payload.initial_blocked}。
            </p>
          )}
          {movement && (
            <div className="notice warning">
              冲正原业务 {movement.business_key}：总量变化{" "}
              {movement.delta_total}、已售变化 {movement.delta_sold}、停售变化{" "}
              {movement.delta_blocked}。本次将以上变化反向登记。
            </div>
          )}
        </>
      )}
      {payload.kind === "price_update" && (
        <p>
          <strong>
            适用采购组织：
            {[...priceBuyers]
              .map((id) => change.buyer_names?.[id] ?? "身份待核对")
              .join("、")}
          </strong>
        </p>
      )}
      {change.kind === "merchant" && (
        <>
          <p>
            {payload.summary}
            {payload.currency ? ` · 币种 ${payload.currency}` : ""}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>对象</th>
                  <th>字段</th>
                  <th>原值</th>
                  <th>修改为</th>
                </tr>
              </thead>
              <tbody>
                {(payload.items ?? []).map(
                  (
                    item: {
                      target: string;
                      field: string;
                      before: unknown;
                      after: unknown;
                    },
                    i: number,
                  ) => (
                    <tr key={i}>
                      <td>
                        {change.target_names?.[item.target] ??
                          `对象 ${item.target}`}
                      </td>
                      <td>{fields[item.field] ?? item.field}</td>
                      <td style={{ whiteSpace: "pre-wrap" }}>
                        {display(item.before)}
                      </td>
                      <td style={{ whiteSpace: "pre-wrap" }}>
                        {display(item.after)}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
      {change.kind === "route_content" && <RouteContentApproval api={api} change={change} onReady={setDocumentReady}/>}
      {change.kind === "route_tags" && <div className="stack"><p>下列为审批后的完整标签设置；未公开标签仅用于内部维护。</p><div className="table-wrap"><table><thead><tr><th>标签</th><th>状态</th><th>顾问搜索</th><th>客户可见</th></tr></thead><tbody>{payload.tags.map((tag:Record<string,any>)=><tr key={tag.id}><td>{tag.label}</td><td>{tag.state==="confirmed"?"确认":"排除"}</td><td>{tag.search_enabled&&tag.state==="confirmed"?"是":"否"}</td><td>{tag.customer_visible&&tag.state==="confirmed"?"是":"否"}</td></tr>)}</tbody></table></div><p>{payload.note}</p></div>}
      {change.kind === "product_display" && <DisplayPreview payload={payload} />}
      {change.kind === "merchant" && (payload.commands ?? []).filter((c: {body?: {display?: unknown}})=>c.body?.display).map((c: {body: {display: Record<string, any>}}, i: number)=><DisplayPreview key={i} payload={c.body.display} />)}
      {change.kind === "catalog" && (
        <div className="stack">
          {payload.name != null && <p>线路名称：{payload.name}</p>}
          {payload.description != null && (
            <p style={{ whiteSpace: "pre-wrap" }}>
              线路介绍：{payload.description}
            </p>
          )}
          {payload.status != null && <p>修改状态：{display(payload.status)}</p>}
        </div>
      )}
      {change.kind === "offer" && <div className="stack">
        <p>方案编号：{payload.code}</p><p>变更说明：{payload.note}</p>
        <h3>修改前</h3>{payload.before ? <><p>{payload.before.name} · {payload.before.active ? "启用" : "停用"}</p><p style={{whiteSpace:"pre-wrap"}}>{payload.before.service_description || "服务说明未填写"}</p></> : <p>新增报价方案</p>}
        <h3>修改后</h3><p>{payload.name} · {payload.active ? "启用" : "停用"}</p><p style={{whiteSpace:"pre-wrap"}}>{payload.service_description || "服务说明未填写"}</p>
        <p className="notice">关联以上团期的现有库存池，与其他方案共享名额。本次操作不改变库存或价表；新方案须单独维护价格。停用后不能继续询价。</p>
      </div>}
      {change.kind === "price_book" && (
        <div className="stack">
          <p>适用对象：{change.buyer_name ?? (payload.layer === "grade" ? `采购等级 ${payload.grade_key}` : "此供应源的全部已授权采购方")}</p>
          <p>变更说明：{payload.note}</p>
          {payload.action === "buyer_grade" ? <>
            <p>修改前：{payload.before ? `${payload.before.grade_key} · ${payload.before.active ? "启用" : "停用"}` : "未分配等级"}</p>
            <p>修改后：{payload.grade_key} · {payload.active ? "启用" : "停用"}</p>
            <p className="notice warning">影响此供应源全部 Excel 团期的等级报价；已有采购协议价仍优先使用。</p>
          </> : <>
            <p>价表层级：{{ contract: "采购协议价", grade: "采购等级价", standard: "标准价" }[payload.layer as string]}</p>
            <h3>修改前</h3>
            {payload.before ? <>
              <p>{payload.before.active ? "启用" : "停用"} · 价格依据：{payload.before.source_ref}</p>
              <p>{businessTime(payload.before.valid_from)} 至 {businessTime(payload.before.valid_until)}（北京时间 UTC+8）</p>
              <SchedulePreview schedule={payload.before.schedule} />
            </> : <p>新增价表，原记录不存在。</p>}
            <h3>修改后</h3>
            <p>{payload.active ? "启用" : "停用"} · 价格依据：{payload.source_ref}</p>
            <p>{businessTime(payload.valid_from)} 至 {businessTime(payload.valid_until)}（北京时间 UTC+8，截止时刻不包含）</p>
            <SchedulePreview schedule={payload.schedule} />
            <p className="notice">按询价时刻选用完整价表：协议价优先，其次等级价，最后标准价。停用或到期可能改用下一层价表；旧报价需重新核价。</p>
          </>}
        </div>
      )}
      {change.kind === "pricing" && (
        <div className="stack">
          <p>
            <strong>
              适用采购组织：{change.buyer_name ?? "名称未获取，不能审批"}
            </strong>
          </p>
          {payload.action === "contract" && (
            <>
              <p>协议依据：{payload.source_ref}</p>
              <p>
                有效期：{businessTime(payload.valid_from)} 至{" "}
                {businessTime(payload.valid_until)}（北京时间 UTC+8）
              </p>
              <SchedulePreview schedule={payload.schedule} />
            </>
          )}
          {payload.action === "binding" && (
            <p className="notice warning">
              将当前采购组织对应到以上上游客户。供应商批准后，采购方管理员仍须确认本版本；未经采购方确认，不会用于上游同业价查询。
            </p>
          )}
        </div>
      )}
      {change.kind === "document" && change.document_source && <DocumentApproval api={api} change={change} onReady={setDocumentReady} />}
      {!supported && (
        <p className="notice warning">
          完整审批依据尚未就绪，请核对上方提示，当前不能确认应用。
        </p>
      )}
      {!approver && change.status === "staged" && (
        <p className="muted">请由供应商管理员核对并审批。</p>
      )}
      {approver && change.status === "staged" && (
        <>
          <label className="row">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={busy || !supported}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            {change.kind === "document" ? "我已核对原文件、产品版本和全部文档内容" : "我已核对对象和全部变更内容"}
          </label>
          <div className="row">
            <button
              className="btn approve"
              disabled={busy || !confirmed || !supported}
              onClick={onApply}
            >
              {busy ? "处理中…" : "确认审批并应用"}
            </button>
            <button className="btn danger" disabled={busy} onClick={onDiscard}>
              作废此变更
            </button>
          </div>
        </>
      )}
      {change.status !== "staged" && <Badge status={change.status} />}
    </section>
  );
}

function ImportSalesPreview({ rule }: { rule: ImportSalesRule }) {
  return <div className="stack">
    <p>停售：{rule.before_paused ? "已停售" : "未停售"} → {rule.sales_paused ? "停售" : "未停售"}</p>
    <p>截止（北京时间）：{rule.before_deadline ? businessTime(rule.before_deadline) : "未设置"} → {rule.local_booking_deadline ? businessTime(rule.local_booking_deadline) : "清空 / 未设置"}</p>
    <p>业务时区：{rule.business_timezone}</p>
    <p>说明：{rule.reason}</p>
  </div>;
}

function DisplayPreview({payload}: {payload: Record<string, any>}) {
  return <section className="stack" aria-label="展示补充前后对照">
    <p className="notice">以下为云仓展示补充。上游事实不变；恢复沿用后将展示最新上游内容。</p>
    <p>变更说明：{payload.note}</p>
    <h3>上游原始内容</h3><p>{payload.source.name}</p><p style={{whiteSpace:"pre-wrap"}}>{payload.source.description || "未提供介绍"}</p>
    <div className="table-wrap"><table><thead><tr><th>展示字段</th><th>修改前</th><th>修改后</th></tr></thead><tbody>
      {([['name_override','名称','name'],['description_override','介绍','description']] as const).map(([key,label,source])=><tr key={key}>
        <th>{label}</th>{[payload.before[key],payload[key]].map((value,index)=><td key={index} style={{whiteSpace:"pre-wrap"}}>{value === null ? `沿用上游：${payload.source[source] || "未提供"}` : `云仓补充：${value || "明确留空"}`}</td>)}
      </tr>)}
    </tbody></table></div>
  </section>;
}
