"use client";
import { useEffect, useRef, useState } from "react";
import { amount } from "@/lib/warehouse";
import "@/components/warehouse/copilot.css";
export default function SharedPlan() {
  const [data, setData] = useState<any>(null),
    [notice, setNotice] = useState("正在读取方案…"),
    [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  async function read(signal?: string, route = 0) {
    pending.current?.abort();
    const request = new AbortController();
    pending.current = request;
    const token = location.hash.slice(1);
    if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
      setData(null);
      setNotice("方案已更新，请联系顾问");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/warehouse-api/v1/public/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, signal, route }),
        cache: "no-store",
        credentials: "omit",
        referrerPolicy: "no-referrer",
        signal: request.signal,
      });
      if (!r.ok) throw new Error("方案已更新，请联系顾问");
      const result = await r.json();
      if (request.signal.aborted || location.hash.slice(1) !== token) return;
      setData(result);
      setNotice(signal ? "已通知顾问，请通过原有联系方式继续沟通。" : "");
    } catch (e) {
      if (request.signal.aborted) return;
      setData(null);
      setNotice(e instanceof Error ? e.message : "暂时无法读取方案");
    } finally {
      if (!request.signal.aborted) setBusy(false);
    }
  }
  useEffect(() => {
    void read();
    const refresh = () => { setData(null); void read(); };
    const timer = setInterval(refresh, 30000);
    window.addEventListener("focus", refresh);
    window.addEventListener("hashchange", refresh);
    return () => {
      pending.current?.abort();
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("hashchange", refresh);
    };
  }, []);
  return (
    <div className="cp-app">
      <main className="cp-home" style={{ maxWidth: 780 }}>
        <h1>为您整理的旅行方案</h1>
        {notice && (
          <p className="cp-notice" role="status">
            {notice}
          </p>
        )}
        {data && (
          <>
            <p className="cp-muted">
              方案 v{data.version} · {data.limitation}
            </p>
            {data.routes.map((r: any, i: number) => (
              <article key={i} className="cp-panel" style={{ marginTop: 18 }}>
                <h2>{r.title}</h2>
                <p>
                  {r.days} 天 · {r.depart_city || "出发地待确认"}
                </p>
                {r.dimensions.map((d: any) => (
                  <div className="cp-record" key={d.concern}>
                    <b>{d.concern}</b>
                    <p>{d.text}</p>
                  </div>
                ))}
                <div className="cp-drawback">
                  <b>也请留意</b>
                  {r.drawbacks.map((d: string, n: number) => (
                    <p key={n}>{d}</p>
                  ))}
                </div>
                <p>全家费用：{amount(r.sales_total, r.currency)}</p>
                <div className="cp-row">
                  <button
                    className="cp-primary"
                    disabled={busy}
                    onClick={() => void read("select", i)}
                  >
                    就选这个
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => void read("question", i)}
                  >
                    问问顾问
                  </button>
                </div>
                <small>仅通知顾问，不代表预订或付款。</small>
              </article>
            ))}
          </>
        )}
      </main>
    </div>
  );
}
