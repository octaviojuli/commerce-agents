"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Draft, ErrorBox, Loading, Sheet, Top, Track, useToast } from "@/components/ui";
import { ApiError, api, money } from "@/lib/api";
import type { QuoteView } from "@/lib/types";

type Dep = {
  departure_id: string;
  offer_id: string | null;
  date: string;
  weekday: string;
  return_date: string;
  return_weekday: string;
  availability: string;
  sales_status: string;
  in_window: boolean;
  leave_days: number | null;
  price: QuoteView | null;
};
type Dates = { route: { product_id: string; title: string; supplier?: string }; items: Dep[]; compare: Dep[]; formation: { text: string } | null; window: string };

export default function DatesPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const [data, setData] = useState<Dates | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [pricing, setPricing] = useState("");
  // A departure sold as several offers: the advisor picks one, and that pick goes with it.
  const [offers, setOffers] = useState<{ dep: Dep; items: { offer_id: string; name: string; text: string }[] } | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const offerOf = (d: Dep) => picked[d.departure_id] ?? d.offer_id;
  const load = async (ids: string[] = chosen) => {
    try {
      const d = await api.get<Dates>(`/deals/${id}/dates${ids.length ? "?compare=" + ids.join(",") : ""}`);
      setData(d);
      if (!ids.length) setChosen(d.compare.map((c) => c.departure_id));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    load([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  const compare = (data?.items ?? []).filter((d) => chosen.includes(d.departure_id)).slice(0, 2);
  const pick = compare[compare.length - 1];
  async function price(d: Dep, offer: string | null = offerOf(d)) {
    setPricing(d.departure_id);
    try {
      await api.post(`/deals/${id}/dates/${d.departure_id}/price${offer ? `?offer_id=${encodeURIComponent(offer)}` : ""}`);
      await load(chosen);
    } catch (e) {
      if (e instanceof ApiError && e.data?.offers) setOffers({ dep: d, items: e.data.offers });
      else toast((e as Error).message);
    } finally {
      setPricing("");
    }
  }
  const status = (d: Dep) => (d.availability === "available" ? "有位" : d.availability === "sold_out" ? "已满" : d.availability || "待查");
  const draft =
    compare.length === 2
      ? `这条线${data?.window ? data.window : ""}有两个团：${compare
          .map((d) => `${d.date.slice(5).replace("-", "/")}（周${d.weekday}出发，${d.return_date.slice(5).replace("-", "/")}回${d.price?.per_person ? `，每人约 ${money(d.price.per_person)}` : ""}）`)
          .join("和")}。您看选哪个？`
      : "";
  // Calendar: two weeks around the first departure in view.
  // Dates are calendar days, so all arithmetic stays in UTC.
  const first = data?.items[0] ? new Date(data.items[0].date + "T00:00:00Z") : null;
  const start = first ? new Date(first.getTime() - first.getUTCDay() * 86400000 - 7 * 86400000) : null;
  const cells = start ? Array.from({ length: 35 }, (_, i) => new Date(start.getTime() + i * 86400000)) : [];
  const byDate = Object.fromEntries((data?.items ?? []).map((d) => [d.date, d]));
  return (
    <div className="app">
      <Top title={data ? `选了${data.route.title}` : "团期选择"} sub={[data?.route.supplier, data?.window ? `需求时间：${data.window}` : ""].filter(Boolean).join(" · ")} back={`/deals/${id}`} />
      <Track stage={2} />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!data && !error && <Loading />}
          {data && (
            <>
              <div className="card">
                <div className="cal">
                  {"日一二三四五六".split("").map((w) => (
                    <span className="w" key={w}>
                      {w}
                    </span>
                  ))}
                  {cells.map((c) => {
                    const key = c.toISOString().slice(0, 10);
                    const dep = byDate[key];
                    const cls = dep ? (chosen.includes(dep.departure_id) ? "sel" : dep.availability === "sold_out" ? "fu" : dep.in_window ? "ok" : "lo") : "";
                    return (
                      <span
                        key={key}
                        className={"d " + cls}
                        role={dep ? "button" : undefined}
                        onClick={() => dep && setChosen((c2) => (c2.includes(dep.departure_id) ? c2.filter((x) => x !== dep.departure_id) : [...c2, dep.departure_id].slice(-2)))}
                      >
                        {c.getUTCDate()}
                        {dep && <small>{dep.price?.per_person ? `¥${(dep.price.per_person / 10000).toFixed(2)}万` : dep.availability === "sold_out" ? "满" : "有团"}</small>}
                      </span>
                    );
                  })}
                </div>
                <div className="lg2" style={{ marginTop: 6 }}>
                  <span>
                    <i style={{ background: "var(--ok-soft)" }} />
                    需求时间内
                  </span>
                  <span>
                    <i style={{ background: "var(--sun-soft)" }} />
                    时间外
                  </span>
                  <span>
                    <i style={{ background: "var(--brand)" }} />
                    在比较
                  </span>
                </div>
              </div>
              {compare.length > 0 && (
                <div className="card twod">
                  <div className="hd2" style={{ gridTemplateColumns: `48px repeat(${compare.length}, 1fr)` }}>
                    <span />
                    {compare.map((d, i) => (
                      <b key={d.departure_id} className={i === compare.length - 1 ? "on" : ""}>
                        {d.date.slice(5).replace("-", "/")} 周{d.weekday}
                      </b>
                    ))}
                  </div>
                  {(
                    [
                      ["客人价", (d: Dep) => (d.price?.per_person ? <span className="y">{money(d.price.per_person)}/人</span> : <button className="b sm b-soft" disabled={!!pricing} onClick={() => price(d)}>{pricing === d.departure_id ? "核价中…" : "核价"}</button>)],
                      ["成团", (d: Dep) => <span className={d.sales_status.includes("核实") ? "n" : "y"}>{d.sales_status || "—"}</span>],
                      ["返程", (d: Dep) => `${d.return_date.slice(5).replace("-", "/")} 周${d.return_weekday}`],
                      ["请假", (d: Dep) => (d.leave_days != null ? `${d.leave_days} 天` : "—")],
                      ["余位", (d: Dep) => <span className="y">{status(d)}</span>],
                    ] as [string, (d: Dep) => React.ReactNode][]
                  ).map(([label, f]) => (
                    <div className="r" key={label} style={{ gridTemplateColumns: `48px repeat(${compare.length}, 1fr)` }}>
                      <span>{label}</span>
                      {compare.map((d) => (
                        <span key={d.departure_id}>{f(d)}</span>
                      ))}
                    </div>
                  ))}
                </div>
              )}
              {data.formation && (
                <div className="card risk">
                  <b>成团规则</b>
                  <span>{data.formation.text}</span>
                  <span className="src">成团规则</span>
                </div>
              )}
              {draft && <Draft text={draft} />}
            </>
          )}
        </div>
      </div>
      {pick && (
        <div className="dock">
          <div className="grow">
            <button
              className="b b-br"
              onClick={async () => {
                await api.post(`/deals/${id}/departure`, { departure_id: pick.departure_id, offer_id: offerOf(pick), date: pick.date, return_date: pick.return_date });
                router.push(`/deals/${id}/confirm`);
              }}
            >
              客人选 {pick.date.slice(5).replace("-", "/")} · 去确认
            </button>
          </div>
        </div>
      )}
      <Sheet open={!!offers} onClose={() => setOffers(null)} title="这个团期有几个套餐">
        {offers?.items.map((o) => (
          <button
            key={o.offer_id}
            className="b b-soft"
            style={{ display: "block", width: "100%", textAlign: "left", marginBottom: 8 }}
            onClick={() => {
              const dep = offers.dep;
              setPicked((p) => ({ ...p, [dep.departure_id]: o.offer_id }));
              setOffers(null);
              price(dep, o.offer_id);
            }}
          >
            <b>{o.name}</b>
            {o.text && <small style={{ display: "block" }}>{o.text}</small>}
          </button>
        ))}
        <span className="lbl">按客人要的套餐核价；换套餐要重新核价。</span>
      </Sheet>
    </div>
  );
}
