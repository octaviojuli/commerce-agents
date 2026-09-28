"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ErrorBox, Loading, Sheet, Top, Track, WxIcon, useToast } from "@/components/ui";
import { api, copy, money } from "@/lib/api";
import type { Deal } from "@/lib/types";

type After = {
  departure: { date?: string; return_date?: string };
  days_left: number | null;
  money: { receivable: string; received: string; outstanding: string; payable: string; profit: string | null; margin: string | null };
  tasks: { id: string; kind: string; text: string; due: string | null; status: string }[];
};

const MESSAGE: Record<string, string> = {
  balance: "您好，出发前需要付清尾款，方便的时候转一下，我这边给您开收据～",
  documents: "您好，签证材料清单发您，按清单准备好发我就行～",
  brief: "出发前提醒：注意查看天气、带好转换插头和少量外币，护照随身放好～",
  followup: "旅途还顺利吗？回来有什么感受都可以跟我说，下次想去哪也告诉我～",
};

export default function AfterPage() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const [deal, setDeal] = useState<Deal | null>(null);
  const [data, setData] = useState<After | null>(null);
  const [error, setError] = useState("");
  const [pay, setPay] = useState(false);
  const [amount, setAmount] = useState("");
  const payKey = useRef(crypto.randomUUID());
  const load = () =>
    Promise.all([api.get<Deal>(`/deals/${id}`).then(setDeal), api.get<After>(`/deals/${id}/after`).then(setData)]).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  const date = data?.departure.date ? new Date(data.departure.date + "T00:00:00Z") : null;
  return (
    <div className="app">
      <Top title={`${deal?.title ?? ""}${deal?.route ? " · " + deal.route.title : ""}`} sub={deal?.need.fields.find((f) => f.field === "party")?.text} back={`/deals/${id}`} />
      <Track stage={deal?.stage ?? 4} />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!data && !error && <Loading />}
          {data && (
            <>
              {date && (
                <div className="card dep">
                  <div className="cnt">
                    <div>
                      <b>{data.days_left}</b>
                      <small>天后出发</small>
                    </div>
                  </div>
                  <div className="grow">
                    <b style={{ fontSize: 15 }}>
                      {date.getUTCMonth() + 1}/{date.getUTCDate()} 周{"日一二三四五六"[date.getUTCDay()]} 出发
                    </b>
                    <div className="lbl">集合时间等出团通知</div>
                  </div>
                </div>
              )}
              <div className="card">
                {!data.tasks.length && <div className="empty">登记成交后，这里会按时间列出尾款、材料、出团通知、行前提醒和回访。</div>}
                <ul className="plan">
                  {data.tasks.map((t, i) => {
                    const overdue = t.status === "open" && t.due && t.due <= new Date().toISOString().slice(0, 10);
                    return (
                      <li key={t.id} className={t.status === "done" ? "ok" : overdue ? "now" : ""}>
                        <i>{t.status === "done" ? "✓" : overdue ? "!" : i + 1}</i>
                        <div>
                          <b>{t.text}</b>
                          <small>{t.due ? `${t.due.slice(5).replace("-", "/")} 前` : ""}</small>
                          {t.status === "open" && (
                            <div className="act">
                              {MESSAGE[t.kind] && (
                                <button
                                  className="b sm b-wx"
                                  onClick={async () => {
                                    if (await copy(MESSAGE[t.kind])) toast("已复制，去微信粘贴");
                                  }}
                                >
                                  <WxIcon />
                                  发提醒
                                </button>
                              )}
                              <button
                                className="b sm b-ghost"
                                onClick={async () => {
                                  await api.post(`/tasks/${t.id}/done`);
                                  load();
                                }}
                              >
                                做完了
                              </button>
                            </div>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
              <div className="card">
                <div className="sec" style={{ margin: "0 0 6px" }}>
                  <b>钱</b>
                  <span className="lbl">顾问可见，客人看不到</span>
                </div>
                <div className="kv">
                  <span>应收客人</span>
                  <span className="num">{money(data.money.receivable)}</span>
                </div>
                <div className="kv">
                  <span>已收 / 待收</span>
                  <span className="num">
                    {money(data.money.received)} / <b style={{ color: "var(--sun-ink)" }}>{money(data.money.outstanding)}</b>
                  </span>
                </div>
                <div className="kv">
                  <span>应付供应商</span>
                  <span className="num">{money(data.money.payable)}</span>
                </div>
                <div className="kv">
                  <span>毛利 · 毛利率</span>
                  <span className="num" style={{ color: "var(--ok)", fontWeight: 700 }}>
                    {money(data.money.profit)} · {data.money.margin ?? "—"}%
                  </span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
      <div className="dock">
        <button className="b b-line" onClick={() => setPay(true)}>
          登记收款
        </button>
        <div className="grow">
          <button
            className="b b-wx"
            onClick={async () => {
              if (await copy(MESSAGE.balance)) toast("付款提醒已复制");
            }}
          >
            <WxIcon />
            发付款提醒
          </button>
        </div>
      </div>
      <Sheet open={pay} onClose={() => setPay(false)} title="登记收款">
        <label className="field">
          金额（元）
          <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
        <button
          className="b b-br"
          disabled={!amount}
          onClick={async () => {
            // One key per entry: a retried save finds the receipt it already made.
            await api.post(`/deals/${id}/receipts`, { amount: Number(amount), note: "收款" }, { "Idempotency-Key": payKey.current });
            payKey.current = crypto.randomUUID();
            setPay(false);
            setAmount("");
            load();
          }}
        >
          保存
        </button>
        <span className="lbl">线下收款只做登记，不代表平台收款或供应商确认。</span>
      </Sheet>
    </div>
  );
}
