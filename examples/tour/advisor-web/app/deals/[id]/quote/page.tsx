"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorBox, Loading, Top, Track, WxIcon, useToast } from "@/components/ui";
import { ago, api, clock, copy, money } from "@/lib/api";
import type { Deal, QuoteView } from "@/lib/types";

export default function QuotePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const [deal, setDeal] = useState<Deal | null>(null);
  const [q, setQ] = useState<QuoteView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sales, setSales] = useState("");
  const [deposit, setDeposit] = useState("");
  async function load() {
    const d = await api.get<Deal>(`/deals/${id}`);
    setDeal(d);
    const list = await api.get<{ items: QuoteView[] }>(`/deals/${id}/quotes`);
    const formal = list.items.find((x) => x.kind === "formal" && x.status === "active");
    setQ(formal ?? null);
    if (formal) setSales(formal.sales_total ?? formal.market_total ?? "");
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  async function make() {
    setBusy(true);
    setError("");
    try {
      const r = await api.post<QuoteView>(`/deals/${id}/quotes/formal`);
      setQ(r);
      setSales(r.sales_total ?? r.market_total ?? "");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const people = deal?.need.fields.find((f) => f.field === "party")?.text ?? "";
  const rooms = deal?.need.fields.find((f) => f.field === "rooms")?.text ?? "";
  return (
    <div className="app">
      <Top title={`正式报价 · ${deal?.title ?? ""}`} sub={deal ? `按确认单 · ${deal.departure?.date ?? ""} · ${people} · ${rooms}` : ""} back={`/deals/${id}`} />
      <Track stage={3} />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!deal && <Loading />}
          {deal && !q && (
            <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <b>从确认单生成正式报价</b>
              <span className="lbl">人数、房间、另付都来自客人确认过的确认单，不用重新输入。</span>
              <button className="b b-br" disabled={busy} onClick={make}>
                {busy ? "正在向云仓核价…" : "核价并生成正式报价"}
              </button>
            </div>
          )}
          {q && (
            <>
              {!q.valid && <div className="err">{q.reason}</div>}
              <div className="card bd2">
                {q.lines.map((l) => (
                  <div className="l" key={l.label}>
                    <span>
                      {l.label} {l.unit ? money(l.unit) : "待定"} × {l.quantity}
                    </span>
                    <span>{money(l.total)}</span>
                  </div>
                ))}
                <div className="hr" style={{ margin: "6px 0" }} />
                <div className="l">
                  <span>
                    <b>团费合计</b>
                  </span>
                  <span>
                    <b>{money(q.sales_total ?? q.market_total)}</b>
                  </span>
                </div>
                {q.extras.map((e) => (
                  <div className="l x" key={e.text}>
                    <span>{e.text}</span>
                    <span>{e.amount ? `${money(e.amount)} × ${e.quantity}` : "按实际"}</span>
                  </div>
                ))}
              </div>
              <div className="tri">
                <div>
                  <small>同业价</small>
                  <b>{money(q.settlement_total)}</b>
                </div>
                <div>
                  <small>客人价</small>
                  <b>{money(q.sales_total ?? q.market_total)}</b>
                </div>
                <div className="me">
                  <small>预计毛利 · {q.margin ?? "—"}%</small>
                  <b>{money(q.profit)}</b>
                </div>
              </div>
              <div className="card" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div className="kv">
                  <span>价格核实</span>
                  <span className="live" style={{ display: "inline-flex" }}>
                    <i />
                    {ago(q.created_at)}
                  </span>
                </div>
                <div className="kv">
                  <span>报价有效</span>
                  <span>到 {clock(q.valid_until)}</span>
                </div>
                <label className="field" style={{ marginTop: 6 }}>
                  销售价（全家，默认用市场价）
                  <div className="row">
                    <input type="number" value={sales} onChange={(e) => setSales(e.target.value)} style={{ flex: 1 }} />
                    <button
                      className="b sm b-soft"
                      onClick={async () => {
                        try {
                          setQ(await api.put<QuoteView>(`/deals/${id}/quotes/${q.id}`, { sales_total: Number(sales) }));
                          toast("销售价已更新，毛利已重算");
                        } catch (e) {
                          toast((e as Error).message);
                        }
                      }}
                    >
                      改价
                    </button>
                  </div>
                </label>
              </div>
              <div className="radio">
                <label className="on">
                  <i />
                  <span>
                    发正式报价<small>复制报价单文字到微信；另付不计入合计</small>
                  </span>
                </label>
                <label style={{ opacity: 0.55 }}>
                  <i />
                  <span>
                    报价并占位 <span className="phase" style={{ fontSize: 10 }}>二期</span>
                    <small>向供应商占位，到期自动释放</small>
                  </span>
                </label>
              </div>
              <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <b style={{ fontSize: 14 }}>客人付了定金？</b>
                <div className="row">
                  <input className="grow" aria-label="定金" type="number" placeholder="定金金额（元）" value={deposit} onChange={(e) => setDeposit(e.target.value)} style={{ font: "inherit", fontSize: 16, padding: "10px 12px", borderRadius: 12, border: "1px solid var(--line)", background: "var(--well)", minHeight: 44 }} />
                  <button
                    className="b sm b-br"
                    onClick={async () => {
                      try {
                        await api.post(`/deals/${id}/sale`, { quote_id: q.id, deposit: deposit ? Number(deposit) : null });
                        router.push(`/deals/${id}/after`);
                      } catch (e) {
                        toast((e as Error).message);
                      }
                    }}
                  >
                    登记成交
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
      {q && q.valid && (
        <div className="dock">
          <div className="grow">
            <button
              className="b b-wx"
              onClick={async () => {
                try {
                  const r = await api.post<{ text: string }>(`/deals/${id}/quotes/${q.id}/send`);
                  if (await copy(r.text)) toast("正式报价已复制，去微信粘贴");
                } catch (e) {
                  toast((e as Error).message);
                }
              }}
            >
              <WxIcon />
              发正式报价
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
