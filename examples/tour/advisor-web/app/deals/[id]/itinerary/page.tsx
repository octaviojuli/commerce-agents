"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorBox, Loading, Sheet, Top, WxIcon, useToast } from "@/components/ui";
import { api, copy } from "@/lib/api";

type Note = { id: string; day: number | null; node: string; category: "fee" | "special" | "advisor" | "question"; text: string; amount: string | null; quantity: number; flow: string };
type Day = { day: number; title: string; detail: string; items: { node_id: string; type: string; name: string; detail: string; paid: boolean; shopping: boolean }[]; meals: Record<string, string>; stay: string };
type Itinerary = { product_id: string; title: string; days: Day[]; notes: Note[]; questions: { question: string; answer: string; status: string }[]; flow: Record<string, string> };

const KIND: Record<Note["category"], [string, string]> = {
  question: ["🗨 客人问", "n-q"],
  advisor: ["📝 顾问备注", "n-a"],
  special: ["📦 特殊需求", "n-s"],
  fee: ["💰 费用", "n-m"],
};

export default function ItineraryPage() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const [data, setData] = useState<Itinerary | null>(null);
  const [day, setDay] = useState(1);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState<{ node: string } | null>(null);
  const [form, setForm] = useState({ category: "advisor", text: "", amount: "", quantity: "1" });
  const load = () => api.get<Itinerary>(`/deals/${id}/itinerary`).then(setData).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  const current = data?.days.find((d) => d.day === day);
  const notesFor = (node: string | null) => (data?.notes ?? []).filter((n) => n.day === day && (node === null ? !n.node : n.node === node));
  const count = (d: number) => (data?.notes ?? []).filter((n) => n.day === d).length;
  return (
    <div className="app">
      <Top title={data ? `${data.title} · 行程标注` : "行程标注"} sub={data ? `${data.notes.length} 条备注 · ${data.notes.filter((n) => n.category === "fee" || n.category === "special").length} 条会带进报价和确认单` : ""} back={`/deals/${id}`} />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!data && !error && <Loading />}
          {data && (
            <>
              <div className="daytabs2">
                {data.days.map((d) => (
                  <span key={d.day} className={d.day === day ? "on" : ""} onClick={() => setDay(d.day)} role="button" tabIndex={0}>
                    D{d.day}
                    {count(d.day) ? ` ·${count(d.day)}` : ""}
                  </span>
                ))}
              </div>
              {current && (
                <>
                  <div className="card">
                    <b style={{ fontSize: 15 }}>
                      第 {current.day} 天 · {current.title}
                    </b>
                    <div className="lbl">
                      {Object.entries(current.meals)
                        .map(([k, v]) => `${{ breakfast: "早", lunch: "午", dinner: "晚" }[k] ?? k} ${v}`)
                        .join(" · ")}
                      {current.stay ? ` · 住 ${current.stay}` : ""}
                    </div>
                  </div>
                  {(() => {
                    const names = new Set(current.items.map((n) => n.name));
                    const loose = (data?.notes ?? []).filter((n) => n.day === day && !names.has(n.node));
                    return loose.length ? (
                      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <span className="lbl">当天备注</span>
                        {loose.map((note) => (
                          <div className={"note " + KIND[note.category][1]} key={note.id}>
                            <span>{KIND[note.category][0]}</span>
                            {note.node ? `${note.node}：` : ""}
                            {note.text}
                            {note.amount ? ` · ¥${note.amount} × ${note.quantity}` : ""}
                            <em>→ {note.flow}</em>
                          </div>
                        ))}
                      </div>
                    ) : null;
                  })()}
                  <ul className="nodes">
                    {(current.items.length ? current.items : [{ node_id: "day", type: "", name: current.title, detail: current.detail, paid: false, shopping: false }]).map((n) => (
                      <li key={n.node_id}>
                        <span className={"ni" + (n.paid || n.shopping ? " w" : "")}>{n.shopping ? "购" : n.paid ? "费" : "行"}</span>
                        <div className={"nb" + (n.paid ? " w" : "")}>
                          <b>{n.name || "行程"}</b>
                          {n.detail && <small>{n.detail}</small>}
                          {(current.items.length ? notesFor(n.name) : []).map((note) => (
                            <div className={"note " + KIND[note.category][1]} key={note.id}>
                              <span>{KIND[note.category][0]}</span>
                              {note.text}
                              {note.amount ? ` · ¥${note.amount} × ${note.quantity}` : ""}
                              <em>→ {note.flow}</em>
                            </div>
                          ))}
                          <button className="linkish" onClick={() => setAdding({ node: n.name })} style={{ alignSelf: "flex-start" }}>
                            ＋ 在这里加备注
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <div className="card flowto">
                <b>这些备注会去哪</b>
                {Object.entries(data.flow).map(([k, v]) => (
                  <div className="kv" key={k}>
                    <span>{KIND[k as Note["category"]][0]}</span>
                    <span>{v}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
      {data && (
        <div className="dock">
          <button className="b b-line" onClick={() => setAdding({ node: "" })}>
            ＋ 加备注
          </button>
          <div className="grow">
            <button
              className="b b-wx"
              onClick={async () => {
                const text =
                  `${data.title} · 给您标注好的行程\n` +
                  data.days
                    .map((d) => {
                      const ns = data.notes.filter((n) => n.day === d.day && n.category !== "fee");
                      return `D${d.day} ${d.title}` + (ns.length ? "\n  " + ns.map((n) => n.text).join("\n  ") : "");
                    })
                    .join("\n");
                if (await copy(text)) toast("标注版行程已复制");
              }}
            >
              <WxIcon />
              发标注版行程
            </button>
          </div>
        </div>
      )}
      <Sheet open={!!adding} onClose={() => setAdding(null)} title={`第 ${day} 天 · ${adding?.node || "当天"}`}>
        <label className="field">
          类型
          <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
            <option value="advisor">顾问备注（进行前提醒）</option>
            <option value="special">特殊需求（交给供应商）</option>
            <option value="fee">费用（进报价单另付）</option>
            <option value="question">客人问题（进问答簿）</option>
          </select>
        </label>
        <label className="field">
          内容
          <textarea rows={3} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} />
        </label>
        {form.category === "fee" && (
          <div className="row">
            <label className="field grow">
              单价（元）
              <input type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
            </label>
            <label className="field grow">
              人数
              <input type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} />
            </label>
          </div>
        )}
        <button
          className="b b-br"
          disabled={!form.text.trim()}
          onClick={async () => {
            await api.post(`/deals/${id}/notes`, {
              product_id: data!.product_id,
              day,
              node: adding?.node ?? "",
              category: form.category,
              text: form.text,
              amount: form.category === "fee" && form.amount ? Number(form.amount) : null,
              quantity: Number(form.quantity || 1),
            });
            setAdding(null);
            setForm({ category: "advisor", text: "", amount: "", quantity: "1" });
            load();
          }}
        >
          保存备注
        </button>
      </Sheet>
    </div>
  );
}
