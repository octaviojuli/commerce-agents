"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Draft, ErrorBox, Loading, Sheet, Top, Track, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import type { Confirmation, Deal } from "@/lib/types";

export default function ConfirmPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const [deal, setDeal] = useState<Deal | null>(null);
  const [c, setC] = useState<Confirmation | null>(null);
  const [error, setError] = useState("");
  const [reply, setReply] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    const d = await api.get<Deal>(`/deals/${id}`);
    setDeal(d);
    const current = (await api.get<{ current: Confirmation | null }>(`/deals/${id}/confirmation`)).current;
    if (current && current.current) setC(current);
    else setC(await api.post<Confirmation>(`/deals/${id}/confirmation`));
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  const missing = c ? c.missing.length + (c.status === "confirmed" ? 0 : 1) : 0;
  return (
    <div className="app">
      <Top title={`确认单 · ${deal?.title ?? ""}`} sub={deal ? `需求 v${deal.version} · ${deal.route?.title ?? ""} · ${deal.departure?.date ?? ""}` : ""} back={`/deals/${id}`} />
      <Track stage={2} />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!c && !error && <Loading />}
          {c && (
            <>
              <div className="card cf">
                {c.items.map((i) => (
                  <div className={"ci " + (i.ok ? "ok" : "wait")} key={i.key}>
                    <span className="ck">{i.ok ? "✓" : "?"}</span>
                    <div className="grow">
                      <b>{i.label}</b>
                      <small>{i.text}</small>
                    </div>
                    {!i.ok && <span className="tag t-sun">没定</span>}
                  </div>
                ))}
                <div className={"ci " + (c.status === "confirmed" ? "ok" : "wait")}>
                  <span className="ck">{c.status === "confirmed" ? "✓" : "?"}</span>
                  <div className="grow">
                    <b>客人确认</b>
                    <small>{c.status === "confirmed" ? `原话：${c.evidence}` : "把确认单发给客人，粘贴她的回复"}</small>
                  </div>
                </div>
              </div>
              <Draft title="✎ 发给客人核对" text={c.draft} />
              <div className="card row" style={{ gap: 10 }}>
                <span className="gi ok" style={{ flex: "0 0 26px" }}>
                  ↩
                </span>
                <div className="grow">
                  <b style={{ fontSize: 13.5 }}>客人回复了？</b>
                  <div className="lbl">粘贴她的回复，助手判断是否全部确认</div>
                </div>
                <button className="b sm b-soft" onClick={() => setReply(true)}>
                  粘贴回复
                </button>
              </div>
            </>
          )}
        </div>
      </div>
      {c && (
        <div className="dock">
          <span className="lbl" style={{ flex: 1 }}>
            {missing ? `还差 ${missing} 项确认` : "全部确认"}
          </span>
          <button className="b b-br" disabled={c.status !== "confirmed"} onClick={() => router.push(`/deals/${id}/quote`)}>
            生成正式报价
          </button>
        </div>
      )}
      <Sheet open={reply} onClose={() => setReply(false)} title="客人的回复">
        <label className="field">
          粘贴客人原话
          <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <button
          className="b b-br"
          disabled={!text.trim() || busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api.post(`/deals/${id}/turns`, { text });
              await load();
              setReply(false);
              setText("");
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "助手在看…" : "交给助手判断"}
        </button>
        {c && c.status !== "confirmed" && (
          <button
            className="b b-line"
            disabled={!text.trim()}
            onClick={async () => {
              try {
                setC(await api.post<Confirmation>(`/deals/${id}/confirmation/${c.id}`, { evidence: text, confirmed: true }));
                setReply(false);
              } catch (e) {
                toast((e as Error).message);
              }
            }}
          >
            我已核对，记为全部确认
          </button>
        )}
      </Sheet>
    </div>
  );
}
