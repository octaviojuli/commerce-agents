"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ErrorBox, Loading, Top, Track, useToast } from "@/components/ui";
import { api } from "@/lib/api";

type Doc = { id: string; traveler: number | null; filename: string; status: string; name: string | null; number: string | null; birthday: string | null; expiry: string | null; checks: { ok: boolean; text: string }[]; recognized: boolean };
type Overview = { travellers: { slot: number; role: string; age: number | null; document: Doc | null; state: "confirmed" | "review" | "missing" }[]; unmatched: Doc[]; done: number; total: number };

export default function DocsPage() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const [data, setData] = useState<Overview | null>(null);
  const [open, setOpen] = useState<Doc | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const load = () => api.get<Overview>(`/deals/${id}/documents`).then(setData).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      let last: Doc | null = null;
      for (const f of Array.from(files)) {
        const form = new FormData();
        form.append("file", f);
        last = await api.upload<Doc>(`/deals/${id}/documents`, form);
      }
      await load();
      if (last) setOpen(last);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const review = data?.travellers.filter((t) => t.state === "review").length ?? 0;
  const missing = data?.travellers.filter((t) => t.state === "missing").length ?? 0;
  const pct = data && data.total ? Math.round((data.done / data.total) * 100) : 0;
  return (
    <div className="app">
      <Top title="收证件" sub={data ? `${data.total} 位出行人` : ""} back={`/deals/${id}`} />
      <Track stage={4} />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!data && !error && <Loading />}
          {data && (
            <>
              <div className="card bigp">
                <div className="ringp" style={{ background: `conic-gradient(var(--brand) 0 ${pct}%,var(--well) ${pct}% 100%)` }}>
                  <div>
                    {data.done}/{data.total}
                  </div>
                </div>
                <div className="grow">
                  <b style={{ fontSize: 15 }}>
                    已确认 {data.done} 位 · {review} 位待核对 · {missing} 位未收到
                  </b>
                  <div className="lbl">识别结果要顾问核对后才算完成</div>
                </div>
              </div>
              <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <b style={{ fontSize: 14 }}>客人发来的证件扫描件，丢进来就行</b>
                <span className="lbl">可以一次选多张：JPG、PNG、PDF。在本机识别护照机读区，自动对应到出行人，不发给任何外部服务。</span>
                <input ref={input} type="file" accept="image/jpeg,image/png,application/pdf" multiple hidden onChange={(e) => upload(e.target.files)} />
                <button className="b b-br" disabled={busy} onClick={() => input.current?.click()}>
                  {busy ? "正在识别…" : "选择扫描件"}
                </button>
              </div>
              <div className="card">
                {data.travellers.map((t) => (
                  <div className="tv" key={t.slot}>
                    <span className={"av" + (t.state === "review" ? " v" : t.state === "missing" ? " g" : "")}>{t.state === "missing" ? "+" : t.state === "review" ? "识" : (t.document?.name ?? "✓").slice(0, 1)}</span>
                    <div>
                      <b>{t.document?.name ?? `${t.role}${t.age != null ? ` · ${t.age} 岁` : ""}`}</b>
                      <small>
                        {t.document ? `${t.role} · 护照到 ${t.document.expiry ?? "—"}` : "还没收到扫描件"}
                      </small>
                    </div>
                    {t.state === "confirmed" && <span className="tag t-ok">✓</span>}
                    {t.state === "review" && (
                      <button className="tag t-ai" style={{ border: 0, cursor: "pointer", minHeight: 30 }} onClick={() => setOpen(t.document)}>
                        请核对
                      </button>
                    )}
                    {t.state === "missing" && <span className="tag t-gray">待收</span>}
                  </div>
                ))}
                {data.unmatched.map((d) => (
                  <div className="tv" key={d.id}>
                    <span className="av o">?</span>
                    <div>
                      <b>{d.name ?? d.filename}</b>
                      <small>{d.recognized ? "没对上出行人，请手动指定" : "没识别出机读区，请对照原件录入"}</small>
                    </div>
                    <button className="tag t-sun" style={{ border: 0, cursor: "pointer", minHeight: 30 }} onClick={() => setOpen(d)}>
                      处理
                    </button>
                  </div>
                ))}
              </div>
              {open && (
                <div className="card" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <div className="row">
                    <b style={{ fontSize: 13.5 }} className="grow">
                      识别结果 · {open.name ?? open.filename}
                    </b>
                  </div>
                  <div className="kv">
                    <span>姓名拼音</span>
                    <span>{open.name ?? "—"}</span>
                  </div>
                  <div className="kv">
                    <span>护照号</span>
                    <span className="num">{open.number ?? "—"}</span>
                  </div>
                  <div className="kv">
                    <span>出生日期</span>
                    <span className="num">{open.birthday ?? "—"}</span>
                  </div>
                  <div className="kv">
                    <span>有效期</span>
                    <span className="num">{open.expiry ?? "—"}</span>
                  </div>
                  {open.checks.map((c) => (
                    <div key={c.text} className={c.ok ? "lbl" : "alert"} style={c.ok ? { color: "var(--ok)" } : undefined}>
                      {c.ok ? "✓ " : "⚠ "}
                      {c.text}
                    </div>
                  ))}
                  {open.traveler === null && (
                    <label className="field">
                      这是谁的证件
                      <select id="slot">
                        {data.travellers.map((t) => (
                          <option key={t.slot} value={t.slot}>
                            {t.role}
                            {t.age != null ? ` ${t.age} 岁` : ""} · 第 {t.slot + 1} 位
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <button
                    className="b sm b-br"
                    onClick={async () => {
                      const form = new FormData();
                      const slot = (document.getElementById("slot") as HTMLSelectElement | null)?.value;
                      if (slot) form.append("traveler", slot);
                      setData(await api.upload<Overview>(`/deals/${id}/documents/${open.id}/confirm`, form));
                      setOpen(null);
                    }}
                  >
                    确认无误
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
      <div className="dock">
        <span className="b b-line" style={{ opacity: 0.55 }}>
          占位倒计时 · 二期
        </span>
        <div className="grow">
          <button className="b b-br" disabled={!data || data.done < data.total}>
            {data && data.done < data.total ? `还差 ${data.total - data.done} 位` : "证件已齐"}
          </button>
        </div>
      </div>
    </div>
  );
}
