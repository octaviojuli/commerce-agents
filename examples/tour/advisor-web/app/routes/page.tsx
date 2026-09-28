"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Loading, Supplier, Tabs } from "@/components/ui";
import { api, cover } from "@/lib/api";
import type { SupplierView } from "@/lib/types";

type Item = { product_id: string; title: string; days: number | null; depart_city: string; supplier: SupplierView };
type Facet = SupplierView & { count: number };

export default function RoutesPage() {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Item[] | null>(null);
  const [facet, setFacet] = useState<Facet[]>([]);
  const [only, setOnly] = useState("");
  async function run(q: string) {
    setItems(null);
    const r = await api.get<{ items: Item[]; suppliers: Facet[] }>("/routes?query=" + encodeURIComponent(q));
    setItems(r.items);
    setFacet(r.suppliers);
    setOnly("");
  }
  const shown = (items ?? []).filter((i) => !only || i.supplier.id === only);
  useEffect(() => {
    run("");
  }, []);
  return (
    <div className="app">
      <div className="top">
        <div className="tt">
          <b style={{ fontSize: 21 }}>线路</b>
          <small>云仓里已上线的线路</small>
        </div>
      </div>
      <form
        className="pad"
        style={{ paddingTop: 0, paddingBottom: 8 }}
        onSubmit={(e) => {
          e.preventDefault();
          run(query);
        }}
      >
        <div className="ask">
          <i>⌕</i>
          <input
            aria-label="搜线路"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="目的地、线路名，比如 德法意瑞"
            style={{ flex: 1, border: 0, outline: "none", font: "inherit", fontSize: 16, background: "none" }}
          />
        </div>
      </form>
      <div className="sc">
        <div className="pad" style={{ paddingTop: 0 }}>
          {!items && <Loading />}
          {items && !items.length && <div className="empty">没有找到线路</div>}
          {facet.length > 1 && (
            <div className="facet" role="group" aria-label="按供应商看">
              <button className={"b sm " + (only ? "b-soft" : "b-br")} onClick={() => setOnly("")}>
                全部
              </button>
              {facet.map((f) => (
                <button key={f.id} className={"b sm " + (only === f.id ? "b-br" : "b-soft")} onClick={() => setOnly(only === f.id ? "" : f.id)}>
                  {f.name} {f.count}
                  {f.stance_text && ` · ${f.stance_text}`}
                </button>
              ))}
            </div>
          )}
          <div className="card res">
            {items && shown.map((i) => (
              <Link className="rr" key={i.product_id} href={`/routes/${i.product_id}`}>
                <div className={"cv " + cover(i.title)} />
                <div className="grow">
                  <b>{i.title}</b>
                  <small>
                    {i.days ?? "—"} 天 · {i.depart_city || "出发地待核实"}出发 <Supplier s={i.supplier} />
                  </small>
                </div>
                <span className="lbl">›</span>
              </Link>
            ))}
          </div>
        </div>
      </div>
      <Tabs />
    </div>
  );
}
