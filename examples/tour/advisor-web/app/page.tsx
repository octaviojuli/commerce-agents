"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Paste from "@/components/Paste";
import { Tabs, Loading } from "@/components/ui";
import { initial } from "@/components/DealList";
import { ago, api, clock, left } from "@/lib/api";

type Today = {
  date: string;
  advisor: string;
  urgent: { deal_id: string; title: string; text: string; until: string; kind: string }[];
  waiting: { deal_id: string; title: string; at: string; message: string; hint: string; action: string }[];
  timeline: { deal_id: string; title: string; text: string; until: string; kind: string }[];
  chances: { deal_id: string; title: string; text: string; at: string }[];
};

export default function TodayPage() {
  const [data, setData] = useState<Today | null>(null);
  useEffect(() => {
    api.get<Today>("/today").then(setData).catch(() => {});
  }, []);
  const d = new Date();
  const week = "日一二三四五六"[d.getDay()];
  return (
    <div className="app">
      <div className="sc" style={{ paddingTop: "calc(env(safe-area-inset-top) + 12px)" }}>
        <div className="hi">
          <small>
            {d.getMonth() + 1} 月 {d.getDate()} 日 周{week}
            {data?.advisor ? ` · ${data.advisor}` : ""}
          </small>
          <h3>
            {data ? (
              <>
                {data.urgent.length > 0 ? (
                  <>
                    先处理这 <em>{data.urgent.length} 件</em>，<br />
                  </>
                ) : (
                  <>
                    没有急事，<br />
                  </>
                )}
                {data.waiting.length > 0 ? `${data.waiting.length} 位客人等你回复` : "客人都回过了"}
              </>
            ) : (
              "今天先做什么"
            )}
          </h3>
        </div>
        <div className="pad">
          {!data && <Loading />}
          {data && data.urgent.length > 0 && (
            <div className="card urgent">
              {data.urgent.map((u) => (
                <div className="urow" key={u.deal_id + u.text}>
                  <div className={"clock" + (u.kind === "quote" ? " s" : "")}>
                    <b>{left(u.until)}</b>
                    <small>{u.kind === "quote" ? "失效" : "到期"}</small>
                  </div>
                  <div>
                    <b className="t">
                      {u.title} · {u.text}
                    </b>
                    <small className="t">{clock(u.until)} 前</small>
                    <div className="acts">
                      <Link className="b sm b-soft" href={`/deals/${u.deal_id}${u.kind === "quote" ? "/quote" : "/after"}`}>
                        去处理
                      </Link>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
          {data && (
            <>
              <div className="sec">
                <b>等你回复 · {data.waiting.length}</b>
                <Link href="/deals">全部</Link>
              </div>
              <div className="card inbox">
                {!data.waiting.length && <div className="empty">没有等你回复的客人</div>}
                {data.waiting.map((w) => (
                  <div className="it" key={w.deal_id}>
                    <span className="av">{initial(w.title)}</span>
                    <div style={{ minWidth: 0 }}>
                      <div className="row">
                        <b style={{ fontSize: 14 }}>{w.title}</b>
                        <span className="lbl">{ago(w.at)}</span>
                      </div>
                      <div className="q">{w.message}</div>
                      <div className="dr">
                        ✦ {w.hint}
                        <Link className="b sm b-soft" href={`/deals/${w.deal_id}`}>
                          {w.action}
                        </Link>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="sec">
                <b>今天的时间点</b>
              </div>
              <div className="card">
                {!data.timeline.length && <div className="empty">今天没有到期的事</div>}
                <ul className="tl">
                  {data.timeline.map((t) => (
                    <li key={t.deal_id + t.text}>
                      <time>{new Date(t.until).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}</time>
                      <i className={t.kind === "balance" ? "s" : t.kind === "notice" ? "w" : "bl"} />
                      <span>
                        <Link href={`/deals/${t.deal_id}/after`}>
                          {t.title} · {t.text}
                        </Link>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              {data.chances.length > 0 && (
                <>
                  <div className="sec">
                    <b>机会</b>
                  </div>
                  <div className="card">
                    {data.chances.map((c) => (
                      <div className="chance" key={c.deal_id + c.at}>
                        <div>
                          <b>{c.title}</b>
                          <small>
                            <span className="hot">{ago(c.at)}</span> · {c.text}
                          </small>
                        </div>
                        <Link className="b sm b-soft" href={`/deals/${c.deal_id}`}>
                          去看看
                        </Link>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
      <Paste />
      <Tabs waiting={data?.waiting.length ?? 0} />
    </div>
  );
}
