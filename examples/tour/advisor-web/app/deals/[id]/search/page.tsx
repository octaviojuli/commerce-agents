"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Chips, ErrorBox, Loading, Supplier, Top } from "@/components/ui";
import QueryBar from "@/components/QueryBar";
import { RouteReadCard } from "@/components/Cards";
import { api, cover, money } from "@/lib/api";
import type { Deal, Search } from "@/lib/types";

export default function SearchPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [deal, setDeal] = useState<Deal | null>(null);
  const [search, setSearch] = useState<Search | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function refresh() {
    const d = await api.get<Deal>(`/deals/${id}`);
    setDeal(d);
    setSearch(d.search);
  }
  async function relax(field: string) {
    setBusy(true);
    setError("");
    try {
      await api.post(`/deals/${id}/query/relax`, { field, amount: field === "days" ? 2 : 10 });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      await refresh();
      setBusy(false);
    }
  }
  async function run(suppliers: string[] = search?.supplier_filter ?? []) {
    setBusy(true);
    try {
      const r = await api.post<{ body: Search }>(`/deals/${id}/search`, { suppliers });
      setSearch(r.body);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    api.get<Deal>(`/deals/${id}`).then((d) => {
      setDeal(d);
      if (d.search) setSearch(d.search);
      else run();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  const ids = picked.length >= 2 ? picked : (search?.cards ?? []).slice(0, 2).map((c) => c.product_id);
  return (
    <div className="app">
      <Top
        title={`按需求 v${deal?.version ?? ""} 找线`}
        sub={deal ? `${deal.title} · ${deal.need.summary}` : ""}
        back={`/deals/${id}`}
        right={
          <button className="ic" aria-label="重新找线" onClick={() => run()} disabled={busy}>
            ↻
          </button>
        }
      />
      <QueryBar deal={id} query={deal?.query ?? null} onDone={refresh} />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {(!search || busy) && <Loading />}
          {search && !busy && (
            <>
              <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <span className="lbl">必须满足</span>
                <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                  {search.must.map((m) => (
                    <span className="tag t-gray" key={m}>
                      {m}
                    </span>
                  ))}
                </div>
                <span className="lbl">尽量满足（影响排序）</span>
                <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                  {search.nice.length ? search.nice.map((m) => <span className="tag t-br" key={m}>{m}</span>) : <span className="lbl">无</span>}
                </div>
              </div>
              <div className="card funnel2">
                {search.steps.map((s, i) => (
                  <div className={"fl" + (i === search.steps.length - 1 ? " last" : "")} key={s.label} style={i > 0 && i < 3 ? { margin: `0 ${i * 8}px` } : i >= 3 ? { margin: "0 24px" } : undefined}>
                    <span className="grow">{s.label}</span>
                    <b>{s.count === null ? "—" : `${s.count} 条`}</b>
                    {s.note && <span className="lbl">{s.note}</span>}
                    {s.over && s.over.length > 0 && <span className="lbl">+ {s.over[0].title} 超 ¥{s.over[0].by}</span>}
                  </div>
                ))}
              </div>
              {(search.suppliers?.length ?? 0) > 1 && (
                <div className="facet" role="group" aria-label="按供应商看">
                  <button className={"b sm " + (search.supplier_filter?.length ? "b-soft" : "b-br")} onClick={() => run([])}>
                    全部供应商
                  </button>
                  {search.suppliers!.map((s) => {
                    const on = search.supplier_filter?.includes(s.id);
                    return (
                      <button key={s.id} className={"b sm " + (on ? "b-br" : "b-soft")} onClick={() => run(on ? [] : [s.id])}>
                        {s.name} {s.count}
                        {s.stance_text && ` · ${s.stance_text}`}
                      </button>
                    );
                  })}
                </div>
              )}
              <div className="card res">
                {!search.cards.length && <div className="empty">没有找到合适的线路</div>}
                {search.cards.map((c) => (
                  <div className="rr" key={c.product_id}>
                    <div className={"cv " + cover(c.title)} />
                    <div className="grow">
                      <Link href={`/routes/${c.product_id}?deal=${id}`}>
                        <b>{c.title}</b>
                      </Link>
                      <div className="row" style={{ gap: 6, flexWrap: "wrap", margin: "3px 0" }}>
                        <Supplier s={c.supplier} note />
                        {c.also && c.also.length > 0 && <span className="lbl">别家也在卖：{c.also.map((a) => a.supplier.name).join("、")}</span>}
                      </div>
                      <div className="mbar">
                        <i style={{ width: `${c.score}%` }} />
                      </div>
                      <small>
                        匹配 {c.score}% · <span className="y">{c.yes.map((y) => "✓ " + y).join(" ")}</span> <span className="n">{c.no.map((n) => "✕ " + n).join(" ")}</span>
                        {c.unknown.length > 0 && <span style={{ color: "var(--sun-ink)" }}> {c.unknown.map((u) => "? " + u).join(" ")}</span>}
                      </small>
                      <label className="lbl row" style={{ gap: 6, marginTop: 4 }}>
                        <input
                          type="checkbox"
                          checked={picked.includes(c.product_id)}
                          onChange={() => setPicked((p) => (p.includes(c.product_id) ? p.filter((x) => x !== c.product_id) : [...p, c.product_id].slice(-3)))}
                        />
                        加入比较
                      </label>
                    </div>
                    <div className="pr2">
                      <b>{c.price ? money(c.price.per_person) : "待询价"}</b>
                      <small>{c.days} 天 · {c.depart_city}</small>
                    </div>
                  </div>
                ))}
              </div>
              {(search.explain || search.relax.length > 0) && (
                <div className="card explain">
                  <b>为什么只有这几条？</b>
                  <span>{search.explain || "条件都满足的线路不多。"}</span>
                  <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                    {search.relax.map((r) => (
                      <button key={r.field} className="b sm b-soft" disabled={busy} onClick={() => relax(r.field)}>
                        {r.text}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {search.nearby?.map(item => <RouteReadCard key={item.product_id} deal={id} item={item} />)}
              {search.cards.length > 0 && <Chips
                items={[
                  ...(search.cards.length >= 2
                    ? [{ label: picked.length >= 2 ? `比较选中的 ${picked.length} 条` : "比较前两条", primary: true }]
                    : []),
                  { label: "做方案", primary: search.cards.length < 2 },
                  { label: "看第 1 条行程" },
                ]}
                onPick={(i) => {
                  const k = search.cards.length >= 2 ? i : i + 1;
                  if (k === 0) router.push(`/deals/${id}/compare?ids=${ids.join(",")}`);
                  if (k === 1) router.push(`/deals/${id}/plan?ids=${ids.join(",")}`);
                  if (k === 2 && search.cards[0]) router.push(`/routes/${search.cards[0].product_id}?deal=${id}`);
                }}
              />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
