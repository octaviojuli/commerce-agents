"use client";
import { useState, type FormEvent } from "react";
import { Sheet } from "./mobile";
import { bedText, type BriefEnvelope, type BriefField } from "@/lib/warehouse";

export const fieldNames: Record<string, string> = {
  themes: "旅行主题",
  destinations: "必需目的地",
  destination_examples: "举例目的地",
  destination_regions: "目的地区域",
  excluded_destinations: "不去的目的地",
  window: "出行时间",
  days: "天数",
  depart_city: "出发城市",
  party_total: "总人数",
  adults: "成人",
  children: "儿童",
  seniors: "老人",
  child_ages: "儿童年龄",
  rooms: "房型 / 儿童占床",
  preferences: "偏好",
  budget: "预算",
  "rooms.child_bed": "儿童占床",
  party_total_mismatch: "人数构成与总人数不一致",
  rooms_exceed_party: "单房数量超过人数",
};
const prefs = {
  no_shopping: "不进购物店",
  no_self_pay: "无自费",
  slow_pace: "慢节奏",
  family: "亲子",
};
export function fieldText(key: string, value: any): string {
  if (value == null) return "未提";
  if (key === "window") return `${value.start} 至 ${value.end}`;
  if (key === "days") return `${value.min}–${value.max} 天`;
  if (key === "rooms")
    return [
      value.total != null ? `共 ${value.total} 间` : "",
      value.doubles ? `双人房 ${value.doubles} 间` : "",
      value.twins ? `双床房 ${value.twins} 间` : "",
      value.singles ? `单人房 ${value.singles} 间` : "",
      bedText(value, "儿童占床未定"),
    ]
      .filter(Boolean)
      .join(" · ");
  if (key === "budget") return `${value.currency} ${value.max_per_person} / 人`;
  if (key === "preferences")
    return value.map((p: { label: string }) => p.label).join("、") || "未提";
  if (Array.isArray(value)) return value.join("、") || "无";
  return String(value);
}
export default function BriefPanel({
  brief,
  busy,
  save,
}: {
  brief: BriefEnvelope | null;
  busy: boolean;
  save: (fields: Record<string, unknown>) => Promise<boolean>;
}) {
  const [edit, setEdit] = useState<string | null>(null);
  const b = brief?.body;
  return (
    <section aria-label="需求单">
      <div className="aw-section-head">
        <h2>需求单</h2>
        <span>随会话保存</span>
      </div>
      <div className="aw-readiness">
        {(["search", "quote"] as const).map((level) => {
          const r = brief?.readiness[level];
          return (
            <div key={level}>
              <b>{level === "search" ? "可以检索" : "可以询价"}</b>
              <span
                className={r?.ready && !r.inferred.length ? "aw-ok" : "aw-warn"}
              >
                {r?.ready
                  ? `✓ 已就绪${r.inferred.length ? ` · ⚠ ${r.inferred.map((k) => fieldNames[k] ?? k).join("、")}为推断，待确认` : ""}`
                  : `待补：${(r?.missing ?? (level === "search" ? ["destinations", "window"] : ["destinations", "window", "adults", "children", "rooms"])).map((k) => fieldNames[k] ?? k).join("、")}`}
              </span>
            </div>
          );
        })}
      </div>
      <div className="cp-brief-known">
        {Object.keys(fieldNames)
          .filter(
            (key) =>
              ![
                "rooms.child_bed",
                "party_total_mismatch",
                "rooms_exceed_party",
              ].includes(key),
          )
          .filter((key) => b?.[key]?.value != null)
          .map((key) => {
            const field = b?.[key] as BriefField | undefined;
            const source =
              field?.value == null
                ? "未提"
                : field.source === "said"
                  ? "客人说"
                  : field.source === "inferred"
                    ? "推断·待确认"
                    : field.source === "explore"
                      ? "探索"
                      : "顾问改";
            return (
              <div className="aw-field" key={key}>
                <label>{fieldNames[key]}</label>
                <div>
                  <strong>{fieldText(key, field?.value)}</strong>
                  {field?.hint && <p className="aw-warn">{field.hint}</p>}
                  {field?.evidence && (
                    <p className="aw-evidence" title={field.evidence}>
                      “{field.evidence}”
                    </p>
                  )}
                </div>
                <div className="aw-field-actions">
                  <span
                    className={`aw-source ${field?.source === "inferred" ? "aw-inferred" : ""}`}
                  >
                    {source}
                  </span>
                  <button
                    disabled={busy}
                    onClick={() => setEdit(key)}
                    aria-label={`修改${fieldNames[key]}`}
                  >
                    修改
                  </button>
                </div>
              </div>
            );
          })}
      </div>
      <div className="cp-missing-fields">
        <span>还没提：</span>
        {Object.keys(fieldNames)
          .filter(
            (key) =>
              ![
                "rooms.child_bed",
                "party_total_mismatch",
                "rooms_exceed_party",
              ].includes(key) && b?.[key]?.value == null,
          )
          .map((key) => (
            <button key={key} disabled={busy} onClick={() => setEdit(key)}>
              {fieldNames[key]}
            </button>
          ))}
      </div>
      {edit && (
        <FieldEditor
          key={edit}
          name={edit}
          value={(b?.[edit] as BriefField)?.value}
          close={() => setEdit(null)}
          busy={busy}
          save={async (value) => {
            if (await save({ [edit]: value })) setEdit(null);
            else throw new Error("保存未完成，请核对输入或刷新会话后重试。");
          }}
        />
      )}
    </section>
  );
}
function FieldEditor({
  name,
  value,
  close,
  busy,
  save,
}: {
  name: string;
  value: any;
  close: () => void;
  busy: boolean;
  save: (value: unknown) => Promise<void>;
}) {
  const [error, setError] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    const data = new FormData(e.currentTarget);
    const get = (key: string) => String(data.get(key) ?? "").trim();
    const number = (key: string) => (get(key) === "" ? null : Number(get(key)));
    let next: any;
    if (name === "window") next = { start: get("start"), end: get("end") };
    else if (name === "days") next = { min: number("min"), max: number("max") };
    else if (name === "rooms")
      next = {
        total: number("total"),
        doubles: number("doubles") ?? 0,
        twins: number("twins") ?? 0,
        singles: number("singles") ?? 0,
        child_bed: get("child_bed") === "" ? null : get("child_bed") === "true",
        raw: get("raw"),
      };
    else if (name === "budget")
      next = get("amount")
        ? { max_per_person: get("amount"), currency: get("currency") }
        : null;
    else if (name === "preferences")
      next = data
        .getAll("preference")
        .map((key) => ({ key, label: prefs[key as keyof typeof prefs] }));
    else if (
      [
        "themes",
        "destinations",
        "destination_examples",
        "destination_regions",
        "excluded_destinations",
      ].includes(name)
    )
      next = get("value") ? get("value").split(/[、,，\s]+/) : null;
    else if (name === "child_ages") next = data.getAll("age").map(Number);
    else if (["party_total", "adults", "children", "seniors"].includes(name))
      next = number("value");
    else next = get("value") || null;
    try {
      await save(next);
    } catch (err) {
      setError(String(err));
    }
  }
  const input = (
    key: string,
    label: string,
    type = "number",
    v: any = value?.[key],
  ) => (
    <label key={key}>
      {label}
      {type === "number" && name !== "budget" ? (
        <Stepper
          name={key}
          value={v}
          required={name === "days"}
          min={name === "days" ? 1 : 0}
        />
      ) : (
        <input
          name={key}
          type={type}
          defaultValue={v ?? ""}
          min={type === "number" ? 0 : undefined}
          max={type === "number" ? 365 : undefined}
          required={name === "window" || name === "days"}
        />
      )}
    </label>
  );
  return (
    <Sheet title={`修改${fieldNames[name]}`} close={close}>
      <section className="aw-dialog">
        <form onSubmit={submit}>
          <p>保存后标记为“顾问改”，影响报价的修改需要重新询价。</p>
          {name === "window" ? (
            <>
              {input("start", "最早出发", "date")}
              {input("end", "最晚出发", "date")}
            </>
          ) : name === "days" ? (
            <>
              {input("min", "最少天数")}
              {input("max", "最多天数")}
            </>
          ) : name === "rooms" ? (
            <>
              {input("total", "房间总数")}
              {input("doubles", "双人房间数")}
              {input("twins", "双床房间数")}
              {input("singles", "单人房间数")}
              <BedSwitch value={value?.child_bed ?? null} />
              {input("raw", "房型补充", "text")}
            </>
          ) : name === "child_ages" ? (
            <AgeEditor values={value ?? []} />
          ) : name === "budget" ? (
            <>
              {input("amount", "每人最高预算", "number", value?.max_per_person)}
              {input("currency", "币种", "text", value?.currency ?? "CNY")}
            </>
          ) : name === "preferences" ? (
            <fieldset>
              {Object.entries(prefs).map(([key, label]) => (
                <label className="aw-check" key={key}>
                  <input
                    name="preference"
                    type="checkbox"
                    value={key}
                    defaultChecked={value?.some((p: any) => p.key === key)}
                  />
                  {label}
                </label>
              ))}
            </fieldset>
          ) : (
            input(
              "value",
              fieldNames[name],
              ["party_total", "adults", "children", "seniors"].includes(name)
                ? "number"
                : "text",
              Array.isArray(value) ? value.join("，") : value,
            )
          )}
          {error && <p role="alert">{error}</p>}
          <div className="aw-dialog-actions">
            <button type="button" onClick={close} disabled={busy}>
              取消
            </button>
            <button className="aw-primary" disabled={busy}>
              保存修改
            </button>
          </div>
        </form>
      </section>
    </Sheet>
  );
}

function Stepper({
  name,
  value,
  required,
  min,
}: {
  name: string;
  value: any;
  required: boolean;
  min: number;
}) {
  const [n, setN] = useState(value == null ? "" : String(value));
  return (
    <span className="aw-stepper">
      <button
        type="button"
        aria-label="减少"
        disabled={n === "" || Number(n) <= min}
        onClick={() => setN(String(Math.max(min, Number(n) - 1)))}
      >
        −
      </button>
      <input
        name={name}
        type="number"
        inputMode="numeric"
        min={min}
        max={365}
        required={required}
        value={n}
        onChange={(e) => setN(e.target.value)}
      />
      <button
        type="button"
        aria-label="增加"
        disabled={Number(n) >= 365}
        onClick={() => setN(String(Math.max(min, Number(n) + 1)))}
      >
        ＋
      </button>
    </span>
  );
}
function AgeEditor({ values }: { values: number[] }) {
  const [ages, setAges] = useState(values);
  return (
    <fieldset>
      <legend>每位儿童的出行年龄</legend>
      {ages.map((age, i) => (
        <label className="aw-age-row" key={i}>
          儿童 {i + 1}
          <select
            name="age"
            value={age}
            onChange={(e) =>
              setAges((a) =>
                a.map((v, j) => (j === i ? Number(e.target.value) : v)),
              )
            }
          >
            {Array.from({ length: 18 }, (_, n) => (
              <option key={n} value={n}>
                {n} 岁
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setAges((a) => a.filter((_, j) => i !== j))}
          >
            移除
          </button>
        </label>
      ))}
      <button
        type="button"
        disabled={ages.length >= 50}
        onClick={() => setAges((a) => [...a, 0])}
      >
        添加儿童年龄
      </button>
    </fieldset>
  );
}

function BedSwitch({ value }: { value: boolean | null }) {
  const [bed, setBed] = useState(value);
  return (
    <fieldset className="aw-bed">
      <legend>儿童占床</legend>
      <input
        type="hidden"
        name="child_bed"
        value={bed === null ? "" : String(bed)}
      />
      <button
        type="button"
        role="switch"
        aria-checked={bed === true}
        aria-label="儿童占床"
        onClick={() => setBed(bed !== true)}
      >
        {bed === true ? "● 占床" : bed === false ? "○ 不占床" : "○ 尚未确认"}
      </button>
      <button
        type="button"
        aria-pressed={bed === null}
        onClick={() => setBed(null)}
      >
        待确认
      </button>
    </fieldset>
  );
}
