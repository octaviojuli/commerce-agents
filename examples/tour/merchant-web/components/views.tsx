"use client";
import { ProductDisplay } from "./product-display";

import { useState, type FormEvent } from "react";
import {
  Connection,
  Listing,
  Partner,
  Pool,
  WarehouseClient,
  message,
  money,
  time,
} from "../lib/api";
import { Badge, Field, LoadState, Pagination, useData } from "./common";
import { businessTime } from "./prices";
import { OfferManager } from "./offers";

type Props = { api: WarehouseClient; revision: number };
type EditorProps = Props & { writable: boolean; onProposed: () => void };
type Page<T> = { items: T[]; next_cursor: string | null };

export function Overview({
  api,
  revision,
  onNavigate,
}: Props & {
  onNavigate: (view: "catalog" | "imports" | "approvals") => void;
}) {
  const state = useData<Record<string, number>>(
    api,
    "/merchant/overview",
    revision,
  );
  const metrics = [
    ["products", "线路产品"],
    ["departures", "已发布团期"],
    ["managed_pools", "自管库存池"],
    ["pending_changes", "待审批变更"],
  ];
  return (
    <>
      <p className="muted">从数据来源到发布审批，在同一处核对。</p>
      <LoadState {...state} />
      {state.data && (
        <div className="metrics">
          {metrics.map(([key, label]) => (
            <div key={key} className="panel metric">
              <span className="muted">{label}</span>
              <strong>{state.data![key].toLocaleString("zh-CN")}</strong>
            </div>
          ))}
        </div>
      )}
      <section className="panel stack">
        <div className="eyebrow">今日工作入口</div>
        <h2>先核对，再发布。</h2>
        <p>
          API 团期保留上游来源与有效时间。Excel
          团期由云仓管理库存，每次调整留下审批和流水记录。
        </p>
        <div className="row">
          <button className="btn primary" onClick={() => onNavigate("catalog")}>
            查看线路与团期
          </button>
          <button className="btn" onClick={() => onNavigate("imports")}>
            导入供应商团期
          </button>
          <button className="btn" onClick={() => onNavigate("approvals")}>
            处理待审批变更
          </button>
        </div>
      </section>
      <p className="muted">
        当前提供产品、报价与库存查询。顾问占位、下单和支付尚未开放。
      </p>
    </>
  );
}

export function Catalog({ api, revision, writable, onProposed }: EditorProps) {
  const [query, setQuery] = useState("");
  const [buyer, setBuyer] = useState("");
  const [cursors, setCursors] = useState<string[]>([""]);
  const [selected, setSelected] = useState("");
  const partnerState = useData<{ items: Partner[] }>(
    api,
    "/merchant/partners",
    revision,
  );
  const partners = [
    ...new Map(
      (partnerState.data?.items ?? [])
        .filter((row) => row.valid)
        .map((row) => [row.buyer_org_id, row]),
    ).values(),
  ];
  const suffix = buyer ? `&buyer_org_id=${buyer}` : "";
  const state = useData<Page<Listing>>(
    api,
    `/merchant/catalog?query=${encodeURIComponent(query)}&limit=25${cursors.at(-1) ? `&after=${cursors.at(-1)}` : ""}${suffix}`,
    revision,
  );
  return (
    <>
      <form
        className="row panel"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(
            String(new FormData(event.currentTarget).get("query") ?? ""),
          );
          setCursors([""]);
          setSelected("");
        }}
      >
        <Field label="线路名称或编号">
          <input
            className="input"
            name="query"
            placeholder="搜索已接入的线路"
            maxLength={500}
          />
        </Field>
        <Field label="协议价格适用采购组织">
          <select
            className="input"
            value={buyer}
            onChange={(event) => {
              setBuyer(event.target.value);
              setSelected("");
              setCursors([""]);
            }}
          >
            <option value="">未选择采购组织</option>
            {partners.map((row) => (
              <option key={row.buyer_org_id} value={row.buyer_org_id}>
                {row.buyer_name}
              </option>
            ))}
          </select>
        </Field>
        <button className="btn primary" type="submit">
          查询
        </button>
      </form>
      {partnerState.error && (
        <p className="notice error">采购组织读取失败：{partnerState.error}</p>
      )}
      <LoadState {...state} />
      {state.data && (
        <section className="panel stack">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>线路</th>
                  <th>天数 / 出发地</th>
                  <th>状态</th>
                  <th>基础方案成人结算价起</th>
                  <th>账面余位</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {state.data.items.map((row) => (
                  <tr key={row.listing_id}>
                    <td>{row.title}</td>
                    <td>
                      {row.attributes["天数"]} 天 / {row.attributes["出发地"]}
                    </td>
                    <td>
                      <Badge status={row.status} />
                    </td>
                    <td>{money(row.price, row.currency)}</td>
                    <td>{row.stock ?? "待确认"}</td>
                    <td>
                      <button
                        className="link"
                        onClick={() => setSelected(row.listing_id)}
                      >
                        查看团期
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {state.data.items.length === 0 && (
            <p className="empty">没有符合条件的线路。</p>
          )}
          <Pagination
            next={state.data.next_cursor}
            previous={cursors.length > 1}
            onNext={() => setCursors([...cursors, state.data!.next_cursor!])}
            onPrevious={() => setCursors(cursors.slice(0, -1))}
          />
        </section>
      )}
      {selected && (
        <ListingDetails
          key={`${selected}:${buyer}:${revision}`}
          api={api}
          id={selected}
          buyer={buyer}
          writable={writable}
          onProposed={onProposed}
          onClose={() => setSelected("")}
        />
      )}
    </>
  );
}

function ListingDetails({
  api,
  id,
  buyer,
  writable,
  onProposed,
  onClose,
}: {
  api: WarehouseClient;
  id: string;
  buyer: string;
  writable: boolean;
  onProposed: () => void;
  onClose: () => void;
}) {
  const state = useData<Listing>(
    api,
    `/merchant/listings/${id}${buyer ? `?buyer_org_id=${buyer}` : ""}`,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [salesTarget, setSalesTarget] = useState("");
  const [offerTarget, setOfferTarget] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      await api.post("/merchant/content/proposals", {
        listing_id: id,
        title: form.get("title"),
        long_description: form.get("description"),
        note: form.get("note") || null,
      });
      onProposed();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel details stack">
      <div className="row between">
        <h2>{state.data?.title ?? "线路详情"}</h2>
        <button className="btn" onClick={onClose}>
          收起详情
        </button>
      </div>
      <LoadState {...state} />
      {state.data && (
        <>
          <p style={{ whiteSpace: "pre-wrap" }}>
            {state.data.long_description || "暂无线路介绍"}
          </p>
          <p className="muted">名称来源：{state.data.attributes["线路名称来源"]}；介绍来源：{state.data.attributes["线路介绍来源"]}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>团期编号</th>
                  <th>出发 / 返回</th>
                  <th>状态</th>
                  <th>基础方案成人结算价</th>
                  <th>账面余位</th>
                  <th>库存来源</th>
                  <th>销售规则</th>
                </tr>
              </thead>
              <tbody>
                {state.data.variants?.map((row) => (
                  <tr key={row.listing_id}>
                    <td>{row.option_values?.["团期"]}</td>
                    <td>
                      {row.attributes["出发日期"]}
                      <br />
                      {row.attributes["返回日期"]}
                    </td>
                    <td>
                      <Badge status={row.status} />
                    </td>
                    <td>{money(row.price, row.currency)}</td>
                    <td>{row.stock ?? "待确认"}</td>
                    <td>{row.attributes["库存口径"]}</td>
                    <td>
                      <button className="link" onClick={() => setOfferTarget(row.listing_id)}>报价方案</button>
                      <p>{row.attributes["销售状态"]}</p>
                      <p className="muted">云仓截止：{Number.isFinite(Date.parse(row.attributes["云仓报名截止"])) ? `${businessTime(row.attributes["云仓报名截止"])}（北京时间）` : "未设置"}</p>
                      {writable && <button className="link" onClick={() => setSalesTarget(row.listing_id)}>设置销售规则</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!state.data.variants?.length && (
            <p className="empty">此线路暂无团期。</p>
          )}
          {offerTarget && <OfferManager key={offerTarget} api={api} departure={offerTarget} writable={writable} onProposed={onProposed} onClose={() => setOfferTarget("")} />}
          {salesTarget && writable && <SalesEditor key={salesTarget} api={api} id={salesTarget} onProposed={onProposed} onClose={() => setSalesTarget("")} />}
          {state.data.attributes["来源类型"] !== "excel" && <ProductDisplay key={id} api={api} id={id} writable={writable} onProposed={onProposed} />}
          {writable && state.data.attributes["来源类型"] === "excel" && (
            <details>
              <summary>提交线路内容修改</summary>
              <p className="muted">
                仅支持云仓管理的 Excel 线路；API 来源请在上游系统维护。
              </p>
              <form className="stack" onSubmit={submit}>
                <Field label="线路名称">
                  <input
                    className="input"
                    name="title"
                    defaultValue={state.data.title}
                    required
                    maxLength={300}
                  />
                </Field>
                <Field label="线路介绍">
                  <textarea
                    className="input"
                    name="description"
                    defaultValue={state.data.long_description ?? ""}
                    maxLength={10000}
                  />
                </Field>
                <Field label="修改说明">
                  <input className="input" name="note" maxLength={200} />
                </Field>
                {error && (
                  <p className="notice error" role="alert">
                    {error}
                  </p>
                )}
                <button className="btn primary" disabled={busy}>
                  {busy ? "提交中…" : "生成修改预览"}
                </button>
              </form>
            </details>
          )}
        </>
      )}
    </section>
  );
}

type SalesRules = {
  id: string;
  version: number;
  product_name: string;
  code: string;
  depart_date: string;
  business_timezone: string;
  sales_paused: boolean;
  local_booking_deadline: string | null;
  sales_status_label: string;
};

export function SalesEditor({ api, id, onProposed, onClose }: {
  api: WarehouseClient;
  id: string;
  onProposed: () => void;
  onClose: () => void;
}) {
  const state = useData<SalesRules>(api, `/merchant/departures/${id}/sales`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const cutoff = state.data?.local_booking_deadline;
  const defaultDeadline = cutoff ? new Date(new Date(cutoff).getTime() + 8 * 60 * 60 * 1000).toISOString().slice(0, 19) : "";
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!state.data) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const deadline = String(form.get("deadline") || "");
      const unchangedDeadline = cutoff && deadline && new Date(`${deadline}+08:00`).getTime() === Math.floor(new Date(cutoff).getTime() / 1000) * 1000;
      await api.post("/merchant/departure-sales/preview", {
        target_id: id,
        expected_version: state.data.version,
        sales_paused: form.get("paused") === "on",
        local_booking_deadline: unchangedDeadline ? cutoff : deadline ? new Date(`${deadline}+08:00`).toISOString() : null,
        reason: form.get("reason"),
      });
      onProposed();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel stack" aria-label="团期销售规则">
      <div className="row between"><h3>团期销售规则</h3><button className="btn" disabled={busy} onClick={onClose}>收起规则</button></div>
      <LoadState {...state} />
      {state.data && <form className="stack" onSubmit={submit}>
        <p>{state.data.product_name} · {state.data.code} · {state.data.depart_date}</p>
        <p className="muted">当前：{state.data.sales_status_label}。团期业务时区：{state.data.business_timezone}。</p>
        <p className="notice warning">规则经管理员审批后生效，不改变账面余位。解除停售或清空截止时间后，仍需核实供应商实际报名条件。</p>
        <label className="row"><input name="paused" type="checkbox" defaultChecked={state.data.sales_paused} disabled={busy} />云仓停售，暂停新报价</label>
        <Field label="云仓报名截止时间（北京时间 UTC+8；留空表示未设置）">
          <input className="input" name="deadline" type="datetime-local" step="1" defaultValue={defaultDeadline} disabled={busy} />
        </Field>
        <Field label="修改原因"><input className="input" name="reason" required maxLength={500} disabled={busy} /></Field>
        {error && <p className="notice error" role="alert">{error}</p>}
        <button className="btn primary" disabled={busy}>{busy ? "提交中…" : "提交销售规则审批"}</button>
      </form>}
    </section>
  );
}

export function Inventory({
  api,
  revision,
  writable,
  onProposed,
}: EditorProps) {
  const [cursors, setCursors] = useState<string[]>([""]);
  const [selected, setSelected] = useState<Pool | null>(null);
  const state = useData<Page<Pool>>(
    api,
    `/merchant/inventory?limit=50${cursors.at(-1) ? `&after=${cursors.at(-1)}` : ""}`,
    revision,
  );
  return (
    <>
      <p className="muted">
        此处仅显示由云仓管理的库存。API 来源的余位在“线路与团期”中查看。
      </p>
      <LoadState {...state} />
      {state.data && (
        <section className="panel stack">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>线路 / 团期</th>
                  <th>出发日期</th>
                  <th>总量</th>
                  <th>已售</th>
                  <th>停售</th>
                  <th>占用</th>
                  <th>可用</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {state.data.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      {row.product_name}
                      <br />
                      <span className="muted">{row.code}</span>
                    </td>
                    <td>{row.depart_date}</td>
                    <td>{row.total}</td>
                    <td>{row.sold}</td>
                    <td>{row.blocked}</td>
                    <td>{row.held}</td>
                    <td>
                      <strong>{row.available}</strong>
                    </td>
                    <td>
                      <button className="link" onClick={() => setSelected(row)}>
                        查看与登记
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!state.data.items.length && (
            <p className="empty">
              暂无云仓托管库存。完成 Excel 导入审批后显示。
            </p>
          )}
          <Pagination
            next={state.data.next_cursor}
            previous={cursors.length > 1}
            onNext={() => setCursors([...cursors, state.data!.next_cursor!])}
            onPrevious={() => setCursors(cursors.slice(0, -1))}
          />
        </section>
      )}
      {selected && state.data?.items.some((row) => row.id === selected.id) && (
        <InventoryEditor
          key={`${selected.id}:${revision}`}
          api={api}
          pool={
            state.data?.items.find((row) => row.id === selected.id) ?? selected
          }
          writable={writable}
          onProposed={onProposed}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

type Movement = {
  id: string;
  kind: string;
  delta_total: number;
  delta_sold: number;
  delta_blocked: number;
  business_key: string;
  reason: string;
  occurred_at: string;
  reverses_id: string | null;
  reverses_business_key: string | null;
  reversed_by_id: string | null;
};
const actions: Record<string, string> = {
  sale: "登记线下销售",
  adjust: "调整库存总量",
  block: "登记停售",
  reverse: "冲正原流水",
  open: "期初交接",
};
function InventoryEditor({
  api,
  pool,
  writable,
  onProposed,
  onClose,
}: {
  api: WarehouseClient;
  pool: Pool;
  writable: boolean;
  onProposed: () => void;
  onClose: () => void;
}) {
  const [cursors, setCursors] = useState<string[]>([""]);
  const state = useData<Page<Movement>>(
    api,
    `/inventory/${pool.id}/movements?limit=25${cursors.at(-1) ? `&before=${cursors.at(-1)}` : ""}`,
  );
  const [action, setAction] = useState("sale");
  const [reverses, setReverses] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      await api.post("/inventory/proposals", {
        action,
        target_id: pool.id,
        expected_version: pool.version,
        quantity: action === "reverse" ? 0 : Number(form.get("quantity")),
        business_key: form.get("business_key"),
        reason: form.get("reason"),
        ...(action === "reverse" ? { reverses_id: reverses } : {}),
      });
      onProposed();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel details stack">
      <div className="row between">
        <div>
          <h2>{pool.product_name}</h2>
          <p className="muted">
            {pool.code} · {pool.depart_date} · 当前可用 {pool.available} 位
          </p>
        </div>
        <button className="btn" onClick={onClose}>
          收起
        </button>
      </div>
      {writable && (
        <form className="stack" onSubmit={submit}>
          <div className="form-grid">
            <Field label="业务操作">
              <select
                className="input"
                value={action}
                onChange={(event) => setAction(event.target.value)}
              >
                {["sale", "adjust", "block", "reverse"].map((key) => (
                  <option key={key} value={key}>
                    {actions[key]}
                  </option>
                ))}
              </select>
            </Field>
            {action !== "reverse" ? (
              <Field
                label={
                  action === "adjust" ? "增减数量（减少填负数）" : "登记数量"
                }
              >
                <input
                  className="input"
                  name="quantity"
                  type="number"
                  step="1"
                  min={action === "adjust" ? -1000000 : 1}
                  max={1000000}
                  required
                />
              </Field>
            ) : (
              <Field label="待冲正流水">
                <select
                  className="input"
                  value={reverses}
                  onChange={(event) => setReverses(event.target.value)}
                  required
                >
                  <option value="">选择原流水</option>
                  {state.data?.items
                    .filter(
                      (row) =>
                        ["sale", "adjust", "block"].includes(row.kind) &&
                        !row.reversed_by_id,
                    )
                    .map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.business_key} · {actions[row.kind]} · 总量{" "}
                        {row.delta_total} / 已售 {row.delta_sold} / 停售{" "}
                        {row.delta_blocked}
                      </option>
                    ))}
                </select>
                <span className="muted">可在下方翻页查找较早流水，翻页后重新选择。</span>
              </Field>
            )}
            <Field label="业务编号（同一笔业务使用同一编号）">
              <input
                className="input"
                name="business_key"
                maxLength={128}
                required
              />
            </Field>
            <Field label="登记原因">
              <input
                className="input"
                name="reason"
                maxLength={1000}
                required
              />
            </Field>
          </div>
          {error && (
            <p className="notice error" role="alert">
              {error}
            </p>
          )}
          <button className="btn primary" disabled={busy}>
            {busy ? "提交中…" : "提交库存变更审批"}
          </button>
        </form>
      )}
      <h3>库存流水</h3>
      <LoadState {...state} />
      {state.data && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>时间 / 业务编号</th>
                  <th>操作</th>
                  <th>总量变化</th>
                  <th>已售变化</th>
                  <th>停售变化</th>
                  <th>原因</th>
                </tr>
              </thead>
              <tbody>
                {state.data.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      {time(row.occurred_at)}
                      <br />
                      {row.business_key}
                    </td>
                    <td>
                      {actions[row.kind] ?? row.kind}
                      {row.reversed_by_id ? "（已冲正）" : ""}
                    </td>
                    <td>{row.delta_total}</td>
                    <td>{row.delta_sold}</td>
                    <td>{row.delta_blocked}</td>
                    <td>
                      {row.reason}
                      {row.reverses_business_key && (
                        <>
                          <br />
                          <span className="muted">原业务：{row.reverses_business_key}</span>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            next={state.data.next_cursor}
            previous={cursors.length > 1}
            onNext={() => {
              setReverses("");
              setCursors([...cursors, state.data!.next_cursor!]);
            }}
            onPrevious={() => {
              setReverses("");
              setCursors(cursors.slice(0, -1));
            }}
          />
        </>
      )}
    </section>
  );
}

type ImportBatch = {
  id: string;
  file_name: string;
  status: string;
  row_count: number;
  created_at: string;
  errors: { row: number; code: string; field?: string }[];
  published_change_id: string | null;
};
export function Imports({
  api,
  revision,
  writable,
  onProposed,
  onRefresh,
}: EditorProps & { onRefresh: () => void }) {
  const connections = useData<{ items: Connection[] }>(
    api,
    "/merchant/connections",
    revision,
  );
  const [cursors, setCursors] = useState<string[]>([""]);
  const batches = useData<Page<ImportBatch>>(
    api,
    `/merchant/imports?limit=25${cursors.at(-1) ? `&before=${cursors.at(-1)}` : ""}`,
    revision,
  );
  const choices =
    connections.data?.items.filter(
      (row) =>
        row.active &&
        row.connector_type === "excel" &&
        row.capabilities.inventory_owner === "warehouse",
    ) ?? [];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get("file") as File;
    if (!file?.size || file.size > 10 * 1024 * 1024) {
      setError("请选择不超过 10 MB 的 Excel 文件。");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await api.response(
        `/imports?connection_id=${data.get("connection")}`,
        {
          method: "POST",
          headers: {
            "Content-Type":
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "X-File-Name": encodeURIComponent(file.name),
          },
          body: file,
        },
      );
      const result = await response.json();
      setNotice(
        result.status === "validated"
          ? "文件校验通过，请生成预览并提交审批。"
          : "文件已接收，请核对下方校验结果。",
      );
      form.reset();
      setCursors([""]);
      onRefresh();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  async function preview(id: string) {
    setBusy(true);
    setError("");
    try {
      await api.post(`/imports/${id}/preview`);
      onProposed();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="panel stack">
        <div className="row between">
          <div>
            <h2>导入线路与团期</h2>
            <p className="muted">Excel → 校验 → 预览 → 人工审批 → 发布</p>
          </div>
          <button
            className="btn"
            onClick={() =>
              api
                .download("/import-template", "供应商团期导入模板.xlsx")
                .catch((error) => setError(message(error)))
            }
          >
            下载标准模板
          </button>
        </div>
        <p className="notice warning">
          首次交接必须填写期初已售与停售数量，零也须填写；更新已有库存时，这两列应留空。库存总量不能小于已售、停售与占用之和。
          销售规则为选填：空白保留现值；停售填写“停售”或“解除停售”，截止明确移除时填写“清空”，更改规则须填写说明并具备产品编辑权限。
        </p>
        <details className="document-section">
          <summary>第一次导入：需要填写什么？</summary>
          <div className="stack">
            <p>先在“供应源管理”创建或选择 Excel 供应源。每行填写一个团期，同一线路使用相同的线路编号；上传前替换模板中的 ACME 示例。</p>
            <p><strong>必填：</strong>线路编号、线路名称、天数、出发城市、团期编号、出发日期、返回日期、总库存。编号保留为文本，日期用 YYYY-MM-DD；天数包含出发与返回当天。</p>
            <p><strong>首次交接：</strong>期初已售、期初停售必须填写，零也填写。总库存表示容量，不是剩余余位。更新已有团期时，期初两列留空。</p>
            <p><strong>选填：</strong>云仓停售、报名截止时间；修改这两项时必须填写销售规则说明。表头及顺序保持不变，不支持公式、宏或外部链接。</p>
            <p>此模板只导入线路基础资料、团期和库存。同行结算价及市场价在“价表与等级”维护；逐日行程从线路“图片与附件”上传文档、复核后发布。</p>
            <p>上传校验通过后，生成预览，再由管理员审批发布。遗漏某行不会删除已有团期；采购方可见性由供采授权控制。</p>
          </div>
        </details>
        <LoadState {...connections} />
        {connections.data && choices.length === 0 && (
          <p className="empty">
            尚无已交接的 Excel 数据源，请联系平台管理员配置。
          </p>
        )}
        {writable && choices.length > 0 && (
          <form onSubmit={upload} className="stack">
            <Field label="供应商数据源">
              <select className="input" name="connection">
                {choices.map((row) => (
                  <option value={row.id} key={row.id}>
                    {row.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="团期文件（.xlsx，最多 10 MB / 5000 行）">
              <input
                className="input"
                type="file"
                name="file"
                accept=".xlsx"
                required
              />
            </Field>
            <button className="btn primary" disabled={busy}>
              {busy ? "处理中…" : "上传并校验"}
            </button>
          </form>
        )}
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
      </section>
      <section className="panel stack">
        <h2>导入记录</h2>
        <LoadState {...batches} />
        {batches.data?.items.map((batch) => (
          <article
            key={batch.id}
            className="stack"
            style={{ borderTop: "1px solid var(--line)", paddingTop: 16 }}
          >
            <div className="row between">
              <div>
                <strong>{batch.file_name}</strong>
                <p className="muted">
                  {time(batch.created_at)} · {batch.row_count} 行
                </p>
              </div>
              <Badge status={batch.status} />
            </div>
            {batch.errors.length > 0 && (
              <ul className="notice error">
                {batch.errors.map((error, i) => (
                  <li key={i}>
                    {error.row ? `第 ${error.row} 行：` : "文件："}
                    {importError(error.code)}
                    {error.field ? `（${importField(error.field)}）` : ""}
                  </li>
                ))}
              </ul>
            )}
            {writable && batch.status === "validated" && (
              <button
                className="btn"
                disabled={busy}
                onClick={() => preview(batch.id)}
              >
                生成发布预览
              </button>
            )}
          </article>
        ))}
        {batches.data?.items.length === 0 && (
          <p className="empty">暂无导入记录。</p>
        )}
        {batches.data && !busy && (
          <Pagination
            next={batches.data.next_cursor}
            previous={cursors.length > 1}
            onNext={() => setCursors([...cursors, batches.data!.next_cursor!])}
            onPrevious={() => setCursors(cursors.slice(0, -1))}
          />
        )}
      </section>
    </>
  );
}

function importField(field: string) {
  return (
    (
      {
        product_code: "线路编号",
        product_name: "线路名称",
        days: "天数",
        gateway: "出发地",
        departure_code: "团期编号",
        depart_date: "出发日期",
        return_date: "返回日期",
        total: "库存总量",
        initial_sold: "期初已售",
        initial_blocked: "期初停售",
        sales_paused: "云仓停售",
        booking_deadline: "云仓报名截止",
        sales_reason: "销售规则说明",
      } as Record<string, string>
    )[field] ?? field
  );
}
function importError(code: string) {
  return (
    (
      {
        FORMULA_OR_ERROR_CELL: "请将公式或错误单元格改为明确的数值或文字",
        DUPLICATE_DEPARTURE_CODE: "团期编号重复",
        CONFLICTING_PRODUCT_FIELDS: "同一线路的名称、天数或出发地不一致",
        EMPTY_FILE: "文件没有团期数据",
        HEADERS_MUST_MATCH_TEMPLATE: "表头与模板不一致",
        INVALID_SALES_STATE: "云仓停售只允许填写停售、解除停售或留空",
        INVALID_BOOKING_DEADLINE: "截止须填写完整北京时间、带时区时间或清空，不能只填日期",
        MISSING_IMPORT_SHEET: "缺少“团期导入”工作表",
        SHEET_LIMIT_EXCEEDED: "超出 5000 行或模板列数限制",
        INVALID_XLSX: "无法读取此 Excel 文件",
        INVALID_SHEET_XML: "Excel 内容损坏",
        int_type: "须填写整数",
        string_type: "须填写文字",
        missing: "缺少必填字段",
        greater_than_equal: "数值低于允许下限",
        less_than_equal: "数值超过允许上限",
        date_from_datetime_parsing: "日期格式不正确",
        value_error: "字段值或日期顺序不符合规则",
      } as Record<string, string>
    )[code] ?? `字段校验未通过（${code}）`
  );
}
