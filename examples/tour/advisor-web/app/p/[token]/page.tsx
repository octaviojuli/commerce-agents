"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { cover, money } from "@/lib/api";

type Public = {
  status: "sent" | "void";
  message?: string;
  advisor: string;
  org: string;
  version: number;
  hello: string;
  routes: { title: string; days: string | null; why: string; tell: string; dates: string[]; per_person: number | null; total: string | null; valid_until: string | null; price_note?: string; includes: string }[];
  notice: string;
};

// The customer's page, opened from WeChat. No trade price, margin or supplier appears here.
export default function CustomerPlan() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<Public | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  async function load(signal?: string, route = 0) {
    const r = await fetch(`/api/public/plans/${token}` + (signal ? `?signal=${signal}&route=${route}` : ""));
    if (!r.ok) {
      setError("方案不存在或已失效，请联系顾问");
      return;
    }
    setData(await r.json());
    if (signal) setDone(signal === "select" ? "已告诉顾问您选了这条，顾问会尽快联系您" : "已通知顾问，顾问会尽快联系您");
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);
  return (
    <div className="app" style={{ background: "#fff" }}>
      <div className="wxbar" style={{ paddingTop: "calc(env(safe-area-inset-top) + 10px)" }}>
        <b>{data ? `${data.advisor}给您的旅行方案` : "旅行方案"}</b>
      </div>
      <div className="sc cp">
        {error && <div className="empty">{error}</div>}
        {data?.status === "void" && <div className="empty">{data.message}</div>}
        {data?.status === "sent" && (
          <>
            <div className="adv">
              <span className="av" style={{ background: "var(--ink)", color: "#fff" }}>
                {data.advisor.slice(0, 1)}
              </span>
              <div className="grow">
                <b>
                  {data.advisor}
                  {data.org ? ` · ${data.org}` : ""}
                </b>
                <small>第 {data.version} 版方案</small>
              </div>
            </div>
            <div className="hello2">{data.hello}</div>
            {data.routes.map((r, i) => (
              <div className="cr" key={r.title}>
                <div className={"cv " + cover(r.title)} style={{ height: i === 0 ? 150 : 110 }}>
                  {i === 0 && <span className="badge">顾问首推</span>}
                </div>
                <div className="in">
                  <h4>
                    {r.title}
                    {r.days ? ` · ${r.days} 天` : ""}
                  </h4>
                  {r.why && (
                    <div className="fy">
                      <b>为什么适合您家</b>
                      {r.why}
                    </div>
                  )}
                  {r.dates.length > 0 && (
                    <>
                      <span className="lbl">可选出发日期</span>
                      <div className="dl">
                        {r.dates.map((d, n) => (
                          <span key={d} className={n === 0 ? "on" : ""}>
                            {d}
                          </span>
                        ))}
                      </div>
                    </>
                  )}
                  {r.per_person ? (
                    <div className="money">
                      <span>
                        <b>{money(r.per_person)}</b>
                        <small> /人起</small>
                      </span>
                      <small>
                        全家约 {money(r.total)}
                        {r.valid_until && ` · ${new Date(r.valid_until).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })} 前有效`}
                      </small>
                    </div>
                  ) : (
                    <span className="lbl">{r.price_note || "价格由顾问确认后告诉您"}</span>
                  )}
                  {r.includes && <span className="lbl">{r.includes}</span>}
                  {r.tell && <span className="lbl">要提前知道：{r.tell}</span>}
                  <div className="two">
                    <button className="b b-ghost" onClick={() => load("question", i)}>
                      问问顾问
                    </button>
                    <button className="b b-br" onClick={() => load("select", i)}>
                      就选这个
                    </button>
                  </div>
                </div>
              </div>
            ))}
            {done && (
              <p className="lbl" style={{ textAlign: "center", color: "var(--ok)" }}>
                {done}
              </p>
            )}
            <p className="lbl" style={{ textAlign: "center", margin: "0 0 16px" }}>
              {data.notice}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
