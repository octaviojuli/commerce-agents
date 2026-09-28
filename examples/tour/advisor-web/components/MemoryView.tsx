"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { NeedField } from "@/lib/types";
import { show } from "./Cards";
import { Loading, Sheet, Tag, useToast } from "./ui";

type Memory = {
  need: { fields: NeedField[]; beds: string; summary: string };
  version: number;
  versions: { version: number; fields: string[]; reason: string; at: string }[];
  turns: number;
  salutation: string;
  concerns: { id: string; topic: string; text: string; count: number; latest: string; turns: number[] }[];
  avoid: { id: string; text: string }[];
  habits: { id: string; text: string }[];
  said: { id: string; text: string; status: string; at: string }[];
};

export default function MemoryView({ id, compact = false }: { id: string; compact?: boolean }) {
  const toast = useToast();
  const [data, setData] = useState<Memory | null>(null);
  const [edit, setEdit] = useState<NeedField | null>(null);
  const load = useCallback(() => api.get<Memory>(`/deals/${id}/memory`).then(setData).catch(() => {}), [id]);
  useEffect(() => {
    load();
    if (!compact) return;
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load, compact]);
  if (!data) return <Loading />;
  const known = data.need.fields.filter((f) => f.text);
  const missing = data.need.fields.filter((f) => !f.text);
  return (
    <div className="sc">
      <div className="pad" style={{ paddingTop: 12 }}>
        {compact && (
          <div className="sec" style={{ margin: "4px 2px 0" }}>
            <b>这一单记住的事</b>
            <span className="lbl">{data.turns} 轮 · v{data.version}</span>
          </div>
        )}
        <div className="card">
          <div className="sec" style={{ margin: "0 0 8px" }}>
            <b>当前需求 v{data.version}</b>
            <span className="lbl">点一项可以改</span>
          </div>
          <div className="understand">
            <div className="grid">
              {known.map((f) => (
                <button
                  key={f.field}
                  className={"fact" + (f.source === "inferred" ? " inf" : "")}
                  title={f.evidence ? `原话：“${f.evidence}”` : f.hint}
                  onClick={() => setEdit(f)}
                  style={{ cursor: "pointer", font: "inherit", minHeight: 36 }}
                >
                  {f.text}
                  {f.source === "inferred" && <small>推断</small>}
                  {f.source === "advisor" && <small>顾问改</small>}
                </button>
              ))}
              {data.need.beds && <span className="fact">{data.need.beds}</span>}
              {missing.map((f) => (
                <button key={f.field} className="fact miss" onClick={() => setEdit(f)} style={{ cursor: "pointer", font: "inherit", minHeight: 36 }}>
                  {f.label}：还没提
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="sec" style={{ margin: "0 0 8px" }}>
            <b>她最在意的</b>
            <span className="lbl">按提到次数</span>
          </div>
          {!data.concerns.length && <div className="lbl">客人提到的顾虑会记在这里。</div>}
          {data.concerns.map((c, i) => (
            <div className="care" key={c.id}>
              <span className="n">{i + 1}</span>
              <div className="grow">
                <b>{c.topic === "其他" ? c.text : c.topic}</b>
                <small>
                  提到 {c.count} 次 · 最近一次：“{c.latest}”
                </small>
              </div>
              {c.count >= 3 ? <Tag tone="sun">核心</Tag> : null}
            </div>
          ))}
        </div>
        {data.avoid.length > 0 && (
          <div className="card">
            <div className="sec" style={{ margin: "0 0 6px" }}>
              <b>要避开的</b>
            </div>
            {data.avoid.map((a) => (
              <div className="kv" key={a.id}>
                <span>不要</span>
                <span>{a.text}</span>
              </div>
            ))}
          </div>
        )}
        <div className="card">
          <div className="sec" style={{ margin: "0 0 6px" }}>
            <b>我说过的话</b>
            <span className="lbl">防止前后不一致</span>
          </div>
          {!data.said.length && <div className="lbl">复制发出的回复里有依据的说法，会记在这里；资料更新后提醒更正。</div>}
          {data.said.map((s) => (
            <div className="said" key={s.id}>
              <Tag tone={s.status === "outdated" ? "bad" : "ok"}>{s.status === "outdated" ? "要更正" : "已答"}</Tag>
              <span className="grow">{s.text}</span>
              <span className="lbl">{s.at.slice(5, 10)}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="sec" style={{ margin: "0 0 6px" }}>
            <b>怎么和她沟通</b>
          </div>
          <div className="kv">
            <span>称呼</span>
            <span>{data.salutation || "还不知道"}</span>
          </div>
          {data.habits.map((h) => (
            <div className="kv" key={h.id}>
              <span>习惯</span>
              <span>{h.text}</span>
            </div>
          ))}
        </div>
        <div className="card vers">
          <span className="lbl">需求演变</span>
          <div className="vl" style={{ flexWrap: "wrap" }}>
            {data.versions.slice(-4).map((v) => (
              <span key={v.version} className={v.version === data.version ? "cur" : ""}>
                v{v.version}
                <small>
                  {v.at.slice(5, 10).replace("-", "/")} {v.fields.join("·") || v.reason}
                </small>
              </span>
            ))}
          </div>
        </div>
      </div>
      <Sheet open={!!edit} onClose={() => setEdit(null)} title={edit ? `改${edit.label}` : ""}>
        {edit && (
          <FieldEditor
            field={edit}
            onSave={async (value) => {
              try {
                const r = await api.put<{ version: number; effects: string[] }>(`/deals/${id}/need/${edit.field}`, { value });
                toast(`已改为 v${r.version}` + (r.effects.length ? "：" + r.effects.join("；") : ""));
                setEdit(null);
                load();
              } catch (e) {
                toast((e as Error).message);
              }
            }}
          />
        )}
      </Sheet>
    </div>
  );
}

function FieldEditor({ field, onSave }: { field: NeedField; onSave: (v: unknown) => void }) {
  const v = (field.value ?? {}) as Record<string, any>;
  const [state, setState] = useState<Record<string, any>>(() => {
    switch (field.field) {
      case "party":
        return {
          adults: v.adults ?? "",
          children: (v.children ?? []).map((c: any) => `${c.age ?? ""}${c.bed === true ? "占" : c.bed === false ? "不占" : ""}`).join(" "),
          seniors: (v.seniors ?? []).map((s: any) => s.age ?? "").join(" "),
          noKids: v.children && v.children.length === 0,
        };
      case "destinations":
        return { must: (v.must ?? []).join(" "), examples: (v.examples ?? []).join(" "), exclude: (v.exclude ?? []).join(" ") };
      default:
        return { ...v, text: typeof field.value === "string" ? field.value : Array.isArray(field.value) ? (field.value as string[]).join(" ") : "" };
    }
  });
  const set = (k: string) => (e: { target: { value: string } }) => setState({ ...state, [k]: e.target.value });
  const input = (k: string, label: string, type = "text") => (
    <label className="field" key={k}>
      {label}
      <input type={type} value={state[k] ?? ""} onChange={set(k)} />
    </label>
  );
  function value(): unknown {
    const words = (x: string) => String(x ?? "").split(/[\s,，、]+/).filter(Boolean);
    switch (field.field) {
      case "destinations":
        return { must: words(state.must), examples: words(state.examples), regions: [], exclude: words(state.exclude) };
      case "window":
        return { start: state.start, end: state.end, label: state.label ?? "" };
      case "days":
        return { min: Number(state.min), max: Number(state.max) };
      case "party":
        return {
          adults: Number(state.adults),
          children: state.noKids
            ? []
            : words(state.children).map((c) => ({ age: Number(c.replace(/\D/g, "")) || null, bed: c.includes("不占") ? false : c.includes("占") ? true : null })),
          seniors: words(state.seniors).map((a) => ({ age: Number(a) || null })),
        };
      case "rooms":
        return { doubles: Number(state.doubles || 0), twins: Number(state.twins || 0), singles: Number(state.singles || 0), note: state.note ?? "" };
      case "budget":
        return { per_person: Number(state.per_person), currency: "CNY" };
      case "preferences":
      case "themes":
        return words(state.text);
      default:
        return state.text;
    }
  }
  return (
    <>
      {field.evidence && <div className="lbl">客人原话：“{field.evidence}”</div>}
      {field.field === "destinations" && [input("must", "必去（空格分隔）"), input("examples", "比如"), input("exclude", "不去")]}
      {field.field === "window" && [input("start", "最早出发", "date"), input("end", "最晚出发", "date"), input("label", "说法（如 元旦前后）")]}
      {field.field === "days" && [input("min", "最少天数", "number"), input("max", "最多天数", "number")]}
      {field.field === "party" && [
        input("adults", "成人", "number"),
        input("children", "孩子（每个写年龄，占床写“占”，如：8占 5不占）"),
        input("seniors", "长辈年龄（空格分隔）"),
      ]}
      {field.field === "rooms" && [input("doubles", "大床房", "number"), input("twins", "双床房", "number"), input("singles", "单间", "number"), input("note", "说明")]}
      {field.field === "budget" && input("per_person", "每人预算（元）", "number")}
      {field.field === "depart_city" && input("text", "出发城市")}
      {field.field === "preferences" && input("text", "偏好：slow_pace no_shopping no_self_pay family senior")}
      {field.field === "themes" && input("text", "主题（空格分隔）")}
      {field.value != null && <div className="lbl">现在：{show(field.field, field.value)}</div>}
      <button className="b b-br" onClick={() => onSave(value())}>
        保存为新版本
      </button>
    </>
  );
}
