"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ErrorBox, Loading, useToast } from "@/components/ui";
import { api, cover } from "@/lib/api";
import type { Answer } from "@/lib/types";

type RouteView = {
  product_id: string;
  title: string;
  days: number | null;
  depart_city: string;
  published: boolean;
  reviewed: boolean;
  notice: string;
  grid: { label: string; value: string }[];
  watch: { text: string; section: string }[];
  days_outline: { day: number; title: string; detail: string }[];
};

const ASKS = ["住宿", "购物自费", "儿童政策", "签证", "退改", "餐食"];

function RoutePage() {
  const { pid } = useParams<{ pid: string }>();
  const deal = useSearchParams().get("deal");
  const router = useRouter();
  const toast = useToast();
  const [route, setRoute] = useState<RouteView | null>(null);
  const [q, setQ] = useState("");
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [all, setAll] = useState(false);
  useEffect(() => {
    api.get<RouteView>(`/routes/${pid}${deal ? `?deal=${deal}` : ""}`).then(setRoute).catch((e) => setError(e.message));
  }, [pid, deal]);
  async function ask(question: string) {
    if (!question.trim()) return;
    setAsking(true);
    try {
      const r = await api.post<{ items: Answer[] }>(`/routes/${pid}/ask`, { question, deal_id: deal });
      setAnswers((a) => [...r.items, ...a]);
      setQ("");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setAsking(false);
    }
  }
  if (!route) return <div className="app">{error ? <ErrorBox error={error} /> : <Loading />}</div>;
  return (
    <div className="app">
      <div className="sc">
        <div className="rh">
          <div className={"cv " + cover(route.title)} style={{ height: 170 }}>
            <button className="bk" aria-label="返回" onClick={() => router.back()} style={{ position: "absolute", left: 8, top: "calc(env(safe-area-inset-top) + 8px)", zIndex: 2, color: "#fff", background: "rgba(0,0,0,.25)", borderRadius: 12, width: 44, height: 44, fontSize: 26 }}>
              ‹
            </button>
            <div className="ttl">
              <small>
                {route.days ?? "—"} 天 · {route.depart_city || "出发地待核实"}出发 · {route.published ? (route.reviewed ? "已复核" : "未人工审核") : "行程未发布"}
              </small>
              <b>{route.title}</b>
            </div>
          </div>
        </div>
        <div className="pad" style={{ paddingTop: 12 }}>
          <form
            className="ask"
            onSubmit={(e) => {
              e.preventDefault();
              ask(q);
            }}
          >
            <i>✦</i>
            <input
              aria-label="问这条线"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="问这条线：酒店、购物、儿童、签证…"
              style={{ flex: 1, border: 0, outline: "none", font: "inherit", fontSize: 16, background: "none" }}
            />
            {q && (
              <button className="b sm b-soft" disabled={asking}>
                问
              </button>
            )}
          </form>
          <div className="chips">
            {ASKS.map((a) => (
              <button className="chip" key={a} onClick={() => ask(a + "怎么安排？")} disabled={asking}>
                {a}
              </button>
            ))}
          </div>
          {asking && <div className="working"><i />正在查这条线的资料…</div>}
          {answers.length > 0 && (
            <div className="card qa">
              {answers.map((a, i) => (
                <div className={"it" + (a.kind === "unknown" ? " unk" : "")} key={i}>
                  <span className="q">{a.question}</span>
                  <span className="a">{a.answer}</span>
                  <span className="ev">
                    {[...new Set(a.facts.map((f) => f.section))].map((s) => (
                      <span className="src" key={s}>
                        {s}
                      </span>
                    ))}
                    {a.kind === "unknown" && <span className="tag t-sun">待核实</span>}
                  </span>
                </div>
              ))}
            </div>
          )}
          {route.notice && <div className="lbl">ⓘ {route.notice}</div>}
          <div className="facts8">
            {route.grid.map((g) => (
              <div key={g.label}>
                <small>{g.label}</small>
                <b>{g.value}</b>
              </div>
            ))}
          </div>
          {route.watch.length > 0 && (
            <div className="watch">
              <b>⚠ 要提前告诉客人的</b>
              {route.watch.slice(0, 5).map((w) => (
                <span key={w.text}>· {w.text.split("：").slice(-1)[0]}</span>
              ))}
            </div>
          )}
          <div className="card">
            <div className="sec" style={{ margin: "0 0 6px" }}>
              <b>{route.days_outline.length} 天怎么走</b>
              {route.days_outline.length > 5 && (
                <button className="linkish" onClick={() => setAll(!all)}>
                  {all ? "收起" : "看完整行程"}
                </button>
              )}
            </div>
            {!route.days_outline.length && <div className="lbl">这条线路还没有已发布的行程。</div>}
            <ul className="days">
              {route.days_outline.slice(0, all ? undefined : 5).map((d) => (
                <li key={d.day}>
                  <span className="dn">D{d.day}</span>
                  <span>
                    {d.title}
                    {d.detail && <small>{d.detail}</small>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
      {deal && (
        <div className="dock">
          <button
            className="b b-line"
            onClick={async () => {
              await api.post(`/deals/${deal}/route`, { product_id: route.product_id, title: route.title });
              toast("已选定这条线");
              router.push(`/deals/${deal}/plan?ids=${route.product_id}`);
            }}
          >
            ＋ 加入方案
          </button>
          <div className="grow">
            <button
              className="b b-br"
              onClick={async () => {
                await api.post(`/deals/${deal}/route`, { product_id: route.product_id, title: route.title });
                router.push(`/deals/${deal}/dates`);
              }}
            >
              选这条 · 看团期
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <RoutePage />
    </Suspense>
  );
}
