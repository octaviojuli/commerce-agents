"use client";

import { useState, type FormEvent } from "react";
import { WarehouseClient, Partner, message, money } from "../lib/api";
import { Field, LoadState, Pagination, useData } from "./common";

export type Units = {
  adult: string | null;
  child: string | null;
  senior: string | null;
  single_room: string | null;
};
export type Charge = {
  code: string;
  label: string;
  basis: "per_person" | "per_booking";
  market: string | null;
  settlement: string | null;
};
export type Schedule = {
  currency: string;
  market: Units;
  settlement: Units;
  charges: Charge[];
  included_items: string[];
  fees_complete: boolean;
  child_occupies_seat: boolean | null;
  child_age_min: number | null;
  child_age_max: number | null;
  room_types: string[];
};
type Offer = {
  id: string;
  name: string;
  connection_id: string;
  departure_id: string;
  departure_code: string;
  depart_date: string;
  product_name: string;
  connector_type: string;
  source_name: string;
};
type PriceWindow = {
  id: string;
  layer: "contract" | "grade" | "standard";
  buyer_org_id: string | null;
  grade_key: string | null;
  version: number;
  schedule: Schedule;
  source_ref: string;
  valid_from: string;
  valid_until: string;
  active: boolean;
};
const unitLabels = {
  adult: "成人",
  child: "儿童",
  senior: "老人",
  single_room: "单房差",
};
const emptyUnits: Units = {
  adult: null,
  child: null,
  senior: null,
  single_room: null,
};
const emptySchedule: Schedule = {
  currency: "CNY",
  market: emptyUnits,
  settlement: emptyUnits,
  charges: [],
  included_items: [],
  fees_complete: false,
  child_occupies_seat: null,
  child_age_min: null,
  child_age_max: null,
  room_types: [],
};
export function businessTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}
function inputTime(value: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(value);
  const part = (key: string) => parts.find((row) => row.type === key)?.value;
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}

export function Prices({
  api,
  revision,
  writable,
  administrator,
  onProposed,
}: {
  api: WarehouseClient;
  revision: number;
  writable: boolean;
  administrator: boolean;
  onProposed: () => void;
}) {
  const [query, setQuery] = useState("");
  const [cursors, setCursors] = useState([""]);
  const [selected, setSelected] = useState<Offer | null>(null);
  const offers = useData<{ items: Offer[]; next_cursor: string | null }>(
    api,
    `/merchant/offers?query=${encodeURIComponent(query)}&limit=25${cursors.at(-1) ? `&after=${cursors.at(-1)}` : ""}`,
    revision,
  );
  const partners = useData<{ items: Partner[] }>(
    api,
    "/merchant/partners",
    revision,
  );
  const buyers =
    partners.data?.items.filter(
      (row) => row.connection_id === selected?.connection_id,
    ) ?? [];
  return (
    <>
      <p className="muted">
        Excel 团期按协议价、等级价、标准价的顺序选用完整价表。API 来源价格由上游系统提供。
      </p>
      <form
        className="panel row"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(
            String(new FormData(event.currentTarget).get("query") ?? ""),
          );
          setCursors([""]);
          setSelected(null);
        }}
      >
        <Field label="线路或团期编号">
          <input className="input" name="query" maxLength={500} />
        </Field>
        <button className="btn primary">查询</button>
      </form>
      <LoadState {...offers} />
      <LoadState {...partners} />
      {offers.data && (
        <section className="panel stack">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>线路 / 团期</th>
                  <th>出发日期</th>
                  <th>价格方案</th>
                  <th>来源</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {offers.data.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      {row.product_name}
                      <br />
                      {row.departure_code}
                    </td>
                    <td>{row.depart_date}</td>
                    <td>{row.name}</td>
                    <td>{row.source_name}</td>
                    <td>
                      <button
                        className="link"
                        onClick={() => {
                          setSelected(row);
                        }}
                      >
                        管理价格
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!offers.data.items.length && (
            <p className="empty">暂无价格方案。先接入或发布团期。</p>
          )}
          <Pagination
            next={offers.data.next_cursor}
            previous={cursors.length > 1}
            onPrevious={() => setCursors(cursors.slice(0, -1))}
            onNext={() => setCursors([...cursors, offers.data!.next_cursor!])}
          />
        </section>
      )}
      {selected && (
        <section className="panel details stack">
          <h2>
            {selected.product_name} · {selected.departure_code} · {selected.name}
          </h2>
          {selected.connector_type !== "excel" ? (
            <p className="notice">
              此团期从 API
              获取报价。请在“客户映射”中核对采购组织对应的上游客户；云仓不覆盖上游价格。
            </p>
          ) : (
            <PriceBooks key={`${selected.id}:${revision}`} api={api} offer={selected}
              buyers={buyers} writable={writable} administrator={administrator} onProposed={onProposed} />
          )}
        </section>
      )}
    </>
  );
}

const priceLayers = { contract: "采购协议价", grade: "采购等级价", standard: "标准价" };
function PriceBooks({ api, offer, buyers, writable, administrator, onProposed }: {
  api: WarehouseClient; offer: Offer; buyers: Partner[]; writable: boolean; administrator: boolean; onProposed: () => void;
}) {
  const [cursors, setCursors] = useState([""]);
  const [editing, setEditing] = useState<PriceWindow | "new" | null>(null);
  const [layer, setLayer] = useState<PriceWindow["layer"]>("standard");
  const [buyer, setBuyer] = useState("");
  const [grade, setGrade] = useState("");
  const prices = useData<{ items: PriceWindow[]; next_cursor: string | null }>(api,
    `/merchant/price-books?offer_id=${offer.id}&limit=25${cursors.at(-1) ? `&after=${cursors.at(-1)}` : ""}`);
  const buyerName = (id: string | null) => buyers.find(row => row.buyer_org_id === id)?.buyer_name ?? "采购组织名称待核实";
  const saved = editing && editing !== "new" ? editing : null;
  const effectiveLayer = saved?.layer ?? layer;
  const effectiveBuyer = saved?.buyer_org_id ?? buyer;
  const effectiveGrade = saved?.grade_key ?? grade.trim();
  return <div className="stack">
    <p className="notice">按询价时刻选择：采购协议价 → 采购等级价 → 标准价。整份价表一起选用；高优先级价表缺项时提示补充，不拼接其他价表。等级仅在当前供应源内适用。</p>
    <div className="row between"><h3>价表与有效期</h3>{writable && <button className="btn" onClick={() => { setEditing("new"); setLayer("standard"); setBuyer(""); setGrade(""); }}>新增价表</button>}</div>
    <LoadState {...prices} />
    {prices.data && <>
      {!prices.data.items.length && <p className="empty">尚无价表，请新增完整的市场价和结算价。</p>}
      {prices.data.items.map(row => <article className="panel stack" key={row.id}>
        <strong>{priceLayers[row.layer]} · {row.layer === "contract" ? buyerName(row.buyer_org_id) : row.layer === "grade" ? row.grade_key : "全部已授权采购方"}</strong>
        <span>{businessTime(row.valid_from)} 至 {businessTime(row.valid_until)}（北京时间）</span>
        <span>{!row.active ? "已停用" : new Date(row.valid_from).getTime() > Date.now() ? "待生效" : new Date(row.valid_until).getTime() <= Date.now() ? "已到期" : "有效期内"} · {row.source_ref}</span>
        <button className="btn" onClick={() => setEditing(row)}>{writable ? "查看与编辑价表" : "查看价表"}</button>
      </article>)}
      <Pagination next={prices.data.next_cursor} previous={cursors.length > 1}
        onPrevious={() => { setCursors(cursors.slice(0,-1)); setEditing(null); }}
        onNext={() => { setCursors([...cursors, prices.data!.next_cursor!]); setEditing(null); }} />
    </>}
    {editing && <section className="panel stack" aria-label="价表编辑">
      <div className="row between"><h3>{saved ? "编辑已有价表" : "新增价表"}</h3><button className="btn" onClick={() => setEditing(null)}>关闭价表</button></div>
      {saved ? <p>{priceLayers[saved.layer]} · {saved.layer === "contract" ? buyerName(saved.buyer_org_id) : saved.layer === "grade" ? saved.grade_key : "全部已授权采购方"}。已有价表的适用对象不可更换。</p> : <div className="form-grid">
        <Field label="价格层级"><select className="input" value={layer} onChange={e => setLayer(e.target.value as PriceWindow["layer"])}>
          {Object.entries(priceLayers).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
        </select></Field>
        {layer === "contract" && <Field label="适用采购组织"><select className="input" value={buyer} onChange={e => setBuyer(e.target.value)}><option value="">选择已授权采购组织</option>{buyers.filter(row => row.valid).map(row => <option key={row.id} value={row.buyer_org_id}>{row.buyer_name}</option>)}</select></Field>}
        {layer === "grade" && <Field label="适用等级"><input className="input" value={grade} maxLength={80} onChange={e => setGrade(e.target.value)} placeholder="填写与采购方等级一致的名称" /></Field>}
      </div>}
      {(effectiveLayer === "standard" || (effectiveLayer === "grade" && effectiveGrade) || (effectiveLayer === "contract" && effectiveBuyer)) ?
        <PriceForm key={`${saved?.id ?? "new"}:${effectiveLayer}:${effectiveBuyer}:${effectiveGrade}`} api={api} offer={offer} saved={saved} layer={effectiveLayer} buyer={effectiveBuyer} grade={effectiveGrade} writable={writable} onProposed={onProposed} /> : <p className="muted">先选择价表的适用对象。</p>}
    </section>}
    <BuyerGrades api={api} connection={offer.connection_id} buyers={buyers} writable={administrator} onProposed={onProposed} />
  </div>;
}

type BuyerGrade = { id: string; buyer_org_id: string; grade_key: string; active: boolean; version: number };
function BuyerGrades({ api, connection, buyers, writable, onProposed }: {
  api: WarehouseClient; connection: string; buyers: Partner[]; writable: boolean; onProposed: () => void;
}) {
  const [cursors, setCursors] = useState([""]);
  const [selected, setSelected] = useState<BuyerGrade | "new" | null>(null);
  const state = useData<{ items: BuyerGrade[]; next_cursor: string | null }>(api,
    `/merchant/buyer-grades?connection_id=${connection}&limit=25${cursors.at(-1) ? `&after=${cursors.at(-1)}` : ""}`);
  return <section className="stack" aria-label="采购等级管理">
    <div className="row between"><h3>当前供应源的采购等级</h3>{writable && <button className="btn" onClick={() => setSelected("new")}>分配采购等级</button>}</div>
    <p className="muted">等级影响此供应源全部 Excel 团期的报价。已有协议价仍优先使用；停用等级后重新按适用价表选择。变更须管理员审批。</p>
    <LoadState {...state} />
    {state.data && <>{!state.data.items.length && <p className="empty">尚未分配采购等级。</p>}
      {state.data.items.map(row => <article className="panel row between" key={row.id}>
        <span>{buyers.find(b => b.buyer_org_id === row.buyer_org_id)?.buyer_name ?? "采购组织名称待核实"} · {row.grade_key} · {row.active ? "已启用" : "已停用"}</span>
        {writable && <button className="btn" onClick={() => setSelected(row)}>修改采购等级</button>}
      </article>)}
      <Pagination next={state.data.next_cursor} previous={cursors.length > 1} onPrevious={() => {setCursors(cursors.slice(0,-1));setSelected(null);}} onNext={() => {setCursors([...cursors,state.data!.next_cursor!]);setSelected(null);}} />
    </>}
    {selected && writable && <GradeForm key={selected === "new" ? "new" : selected.id} api={api} connection={connection} buyers={buyers} saved={selected === "new" ? null : selected} onProposed={onProposed} onClose={() => setSelected(null)} />}
  </section>;
}
function GradeForm({ api, connection, buyers, saved, onProposed, onClose }: {
  api: WarehouseClient; connection: string; buyers: Partner[]; saved: BuyerGrade | null; onProposed: () => void; onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy(true); setError("");
    try { await api.post("/merchant/buyer-grades/proposals", {
      connection_id: connection, buyer_org_id: saved?.buyer_org_id ?? data.get("buyer"),
      grade_key: String(data.get("grade") ?? "").trim(), expected_version: saved?.version ?? 0,
      active: data.get("active") === "on", note: String(data.get("note") ?? "").trim(),
    }); onProposed(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return <form className="panel stack" onSubmit={submit}>
    <h4>{saved ? "修改采购等级" : "分配采购等级"}</h4>
    <fieldset className="stack" disabled={busy} style={{ border: 0, margin: 0, padding: 0 }}>
      {saved ? <p>采购组织：{buyers.find(b => b.buyer_org_id === saved.buyer_org_id)?.buyer_name ?? "名称待核实"}</p> : <Field label="采购组织"><select className="input" name="buyer" required defaultValue=""><option value="">选择已授权采购组织</option>{buyers.filter(b => b.valid).map(b => <option key={b.id} value={b.buyer_org_id}>{b.buyer_name}</option>)}</select></Field>}
      <Field label="采购等级名称"><input className="input" name="grade" maxLength={80} required defaultValue={saved?.grade_key ?? ""} /></Field>
      <Field label="等级变更说明"><input className="input" name="note" maxLength={500} required /></Field>
      <label className="row"><input type="checkbox" name="active" defaultChecked={saved?.active ?? true} />启用此采购等级</label>
      <div className="row"><button className="btn primary">{busy ? "生成中…" : "生成等级审批预览"}</button><button className="btn" type="button" onClick={onClose}>取消</button></div>
    </fieldset>
    {error && <p className="notice error" role="alert">{error}</p>}
  </form>;
}

function PriceForm({ api, offer, buyer, grade, layer, saved, writable, onProposed }: {
  api: WarehouseClient;
  offer: Offer;
  buyer: string;
  grade: string;
  layer: PriceWindow["layer"];
  saved: PriceWindow | null;
  writable: boolean;
  onProposed: () => void;
}) {
  const [identifier] = useState(() => saved?.id ?? crypto.randomUUID());
  const schedule = saved?.schedule ?? emptySchedule;
  const [charges, setCharges] = useState<Charge[]>(schedule.charges);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const updateCharge = (index: number, field: keyof Charge, value: string) =>
    setCharges((rows) =>
      rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)),
    );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const value = (key: string) => String(data.get(key) ?? "").trim();
    const decimal = (key: string) => value(key) || null;
    const optionalInt = (key: string) =>
      value(key) === "" ? null : Number(value(key));
    const lines = (key: string) =>
      value(key)
        .split("\n")
        .map((row) => row.trim())
        .filter(Boolean);
    const units = (kind: string) =>
      Object.fromEntries(
        Object.keys(unitLabels).map((key) => [key, decimal(`${kind}.${key}`)]),
      );
    setBusy(true);
    setError("");
    try {
      await api.post("/merchant/price-books/proposals", {
        offer_id: offer.id,
        price_id: identifier,
        layer,
        buyer_org_id: layer === "contract" ? buyer : null,
        grade_key: layer === "grade" ? grade : null,
        note: value("note"),
        active: data.get("active") === "on",
        expected_version: saved?.version ?? 0,
        source_ref: value("source_ref"),
        valid_from: saved && value("valid_from") === inputTime(new Date(saved.valid_from)) ? saved.valid_from : `${value("valid_from")}:00+08:00`,
        valid_until: saved && value("valid_until") === inputTime(new Date(saved.valid_until)) ? saved.valid_until : `${value("valid_until")}:00+08:00`,
        schedule: {
          currency: value("currency"),
          market: units("market"),
          settlement: units("settlement"),
          charges: charges.map((row) => ({
            ...row,
            code: row.code.trim(),
            label: row.label.trim(),
            market: row.market?.trim() || null,
            settlement: row.settlement?.trim() || null,
          })),
          included_items: lines("included_items"),
          room_types: lines("room_types"),
          fees_complete: data.get("fees_complete") === "on",
          child_occupies_seat:
            value("child_occupies_seat") === ""
              ? null
              : value("child_occupies_seat") === "true",
          child_age_min: optionalInt("child_age_min"),
          child_age_max: optionalInt("child_age_max"),
        },
      });
      onProposed();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  if (!writable)
    return saved ? (
      <>
        <p>协议依据：{saved.source_ref}</p>
        <p>
          {businessTime(saved.valid_from)} 至 {businessTime(saved.valid_until)}
          （北京时间）
        </p>
        <SchedulePreview schedule={schedule} />
      </>
    ) : (
      <p className="empty">尚未设置价表。</p>
    );
  return (
    <form className="stack" onSubmit={submit}>
      <p className="notice warning">
        空白表示未知，0 表示已确认免费。价表有效期按询价时刻判断，截止时刻不包含在内；填写完整条款后生成预览，审批前不会生效。
      </p>
      <fieldset
        disabled={busy}
        className="stack"
        style={{ border: 0, padding: 0, margin: 0 }}
      >
        <div className="form-grid">
          <Field label="变更说明">
            <input className="input" name="note" required maxLength={500} />
          </Field>
          <label className="row"><input type="checkbox" name="active" defaultChecked={saved?.active ?? true} />启用此价表</label>
          <Field label="币种">
            <input
              className="input"
              name="currency"
              defaultValue={schedule.currency}
              pattern="[A-Z]{3}"
              maxLength={3}
              required
            />
          </Field>
          <Field label="协议依据 / 价单编号">
            <input
              className="input"
              name="source_ref"
              defaultValue={saved?.source_ref ?? ""}
              maxLength={500}
              required
            />
          </Field>
          <Field label="生效时间（北京时间 UTC+8）">
            <input
              className="input"
              type="datetime-local"
              name="valid_from"
              defaultValue={inputTime(
                saved ? new Date(saved.valid_from) : new Date(),
              )}
              required
            />
          </Field>
          <Field label="截止时间（北京时间 UTC+8）">
            <input
              className="input"
              type="datetime-local"
              name="valid_until"
              defaultValue={inputTime(
                saved
                  ? new Date(saved.valid_until)
                  : new Date(Date.now() + 30 * 86400000),
              )}
              required
            />
          </Field>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>计价项</th>
                <th>市场价</th>
                <th>采购结算价</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(unitLabels).map(([key, label]) => (
                <tr key={key}>
                  <th>{label}</th>
                  {(["market", "settlement"] as const).map((kind) => (
                    <td key={kind}>
                      <input
                        aria-label={`${label}${kind === "market" ? "市场价" : "结算价"}`}
                        className="input"
                        type="number"
                        min="0"
                        step="0.01"
                        name={`${kind}.${key}`}
                        defaultValue={schedule[kind][key as keyof Units] ?? ""}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>适用条件</h3>
        <div className="form-grid">
          <Field label="儿童年龄下限">
            <input
              className="input"
              name="child_age_min"
              type="number"
              min={0}
              max={17}
              step={1}
              defaultValue={schedule.child_age_min ?? ""}
            />
          </Field>
          <Field label="儿童年龄上限">
            <input
              className="input"
              name="child_age_max"
              type="number"
              min={0}
              max={17}
              step={1}
              defaultValue={schedule.child_age_max ?? ""}
            />
          </Field>
          <Field label="儿童是否占位">
            <select
              className="input"
              name="child_occupies_seat"
              defaultValue={
                schedule.child_occupies_seat == null
                  ? ""
                  : String(schedule.child_occupies_seat)
              }
            >
              <option value="">待确认</option>
              <option value="true">占位</option>
              <option value="false">不占位</option>
            </select>
          </Field>
          <Field label="可选房型（每行一个）">
            <textarea
              className="input"
              name="room_types"
              defaultValue={schedule.room_types.join("\n")}
            />
          </Field>
        </div>
        <div className="row between">
          <h3>附加费用</h3>
          <button
            className="btn"
            type="button"
            disabled={charges.length >= 50}
            onClick={() =>
              setCharges([
                ...charges,
                {
                  code: "",
                  label: "",
                  basis: "per_booking",
                  market: null,
                  settlement: null,
                },
              ])
            }
          >
            添加费用
          </button>
        </div>
        {charges.map((charge, i) => (
          <div className="panel stack" key={i}>
            <div className="form-grid">
              <Field label="费用编号">
                <input
                  className="input"
                  required
                  maxLength={60}
                  value={charge.code}
                  onChange={(event) =>
                    updateCharge(i, "code", event.target.value)
                  }
                />
              </Field>
              <Field label="费用名称">
                <input
                  className="input"
                  required
                  maxLength={200}
                  value={charge.label}
                  onChange={(event) =>
                    updateCharge(i, "label", event.target.value)
                  }
                />
              </Field>
              <Field label="计费方式">
                <select
                  className="input"
                  value={charge.basis}
                  onChange={(event) =>
                    updateCharge(i, "basis", event.target.value)
                  }
                >
                  <option value="per_booking">每单</option>
                  <option value="per_person">每人</option>
                </select>
              </Field>
              <Field label="市场费用">
                <input
                  className="input"
                  type="number"
                  min="0"
                  step="0.01"
                  value={charge.market ?? ""}
                  onChange={(event) =>
                    updateCharge(i, "market", event.target.value)
                  }
                />
              </Field>
              <Field label="结算费用">
                <input
                  className="input"
                  type="number"
                  min="0"
                  step="0.01"
                  value={charge.settlement ?? ""}
                  onChange={(event) =>
                    updateCharge(i, "settlement", event.target.value)
                  }
                />
              </Field>
            </div>
            <button
              className="btn danger"
              type="button"
              onClick={() =>
                setCharges(charges.filter((_, index) => index !== i))
              }
            >
              移除此项费用
            </button>
          </div>
        ))}
        <Field label="已包含项目（每行一项）">
          <textarea
            className="input"
            name="included_items"
            defaultValue={schedule.included_items.join("\n")}
          />
        </Field>
        <label className="row">
          <input
            type="checkbox"
            name="fees_complete"
            defaultChecked={schedule.fees_complete}
          />
          已核对全部费用，当前价格和附加费已完整列出
        </label>
      </fieldset>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      <button className="btn primary" disabled={busy}>
        {busy ? "生成中…" : "生成价表审批预览"}
      </button>
    </form>
  );
}

export function SchedulePreview({ schedule }: { schedule: Schedule }) {
  const amount = (value: string | null) =>
    money(value == null ? null : Number(value), schedule.currency);
  return (
    <div className="stack">
      <p>
        币种：{schedule.currency} · 费用
        {schedule.fees_complete ? "已确认完整" : "尚未确认完整"}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>计价项</th>
              <th>市场价</th>
              <th>采购结算价</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(unitLabels).map(([key, label]) => (
              <tr key={key}>
                <td>{label}</td>
                <td>{amount(schedule.market[key as keyof Units])}</td>
                <td>{amount(schedule.settlement[key as keyof Units])}</td>
              </tr>
            ))}
            {schedule.charges.map((row) => (
              <tr key={row.code}>
                <td>
                  {row.label}（{row.code} ·{" "}
                  {row.basis === "per_person" ? "每人" : "每单"}）
                </td>
                <td>{amount(row.market)}</td>
                <td>{amount(row.settlement)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        儿童年龄：
        {schedule.child_age_min == null
          ? "待确认"
          : `${schedule.child_age_min} 至 ${schedule.child_age_max} 岁`}
        ；
        {schedule.child_occupies_seat == null
          ? "是否占位待确认"
          : schedule.child_occupies_seat
            ? "儿童占位"
            : "儿童不占位"}
        。
      </p>
      <p>可选房型：{schedule.room_types.join("、") || "待确认"}</p>
      <p>已包含项目：{schedule.included_items.join("、") || "未填写"}</p>
    </div>
  );
}
