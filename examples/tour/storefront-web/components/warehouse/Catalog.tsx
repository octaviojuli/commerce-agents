"use client";

import { useEffect, useState, type FormEvent } from "react";
import { WarehouseClient, message } from "web-shared/warehouse-client";
import {
  cardStyle,
  inputStyle,
  stamp,
  type Page,
  type WarehouseProduct,
} from "@/lib/warehouse";

export function ProductCard({
  product,
  select,
  historical = false,
}: {
  product: WarehouseProduct;
  historical?: boolean;
  select: (p: WarehouseProduct) => void;
}) {
  const attrs = product.attributes;
  const departure = product.product_id.startsWith("WD-");
  return (
    <article className={`${cardStyle} flex flex-col gap-3`}>
      <div className="text-xs font-semibold text-(--accent)">
        {attrs.source_name ?? "已授权供应源"}
      </div>
      <h3 className="text-base font-semibold leading-snug">{product.title}</h3>
      <p className="text-sm text-(--ink-soft)">
        {departure
          ? `${attrs.depart_date} → ${attrs.return_date}`
          : `${attrs.days ?? "未知"} 天 · ${attrs.depart_city ?? "出发地待核实"}`}
      </p>
      {product.short_description && (
        <p className="text-sm text-(--ink-2)">{product.short_description}</p>
      )}
      {(attrs.name_origin === "warehouse_display" || attrs.description_origin === "warehouse_display") && <p className="text-xs text-(--ink-soft)">云仓展示补充：{[attrs.name_origin === "warehouse_display" ? "名称" : "", attrs.description_origin === "warehouse_display" ? "介绍" : ""].filter(Boolean).join("、")}；行程以已复核版本为准。</p>}
      {historical ? <p className="text-sm text-(--warn)">历史查询结果，价格和库存需重新核实</p> : departure && <Stock product={product} />}
      <button
        className="btn-primary mt-auto self-start"
        disabled={departure && !historical && attrs.can_quote === "false"}
        onClick={() => select(product)}
      >
        {departure ? (!historical && attrs.can_quote === "false" ? "当前不可询价" : "选择团期询价") : "查看团期"}
      </button>
    </article>
  );
}

export function Stock({ product }: { product: WarehouseProduct }) {
  const a = product.attributes;
  const stale = a.inventory_status === "stale";
  return (
    <div className="text-sm">
      {a.sales_status_label && <p className={a.can_quote === "false" ? "text-(--warn)" : "text-(--ink-soft)"}>{a.sales_status_label}</p>}
      {a.local_booking_deadline && <p className="text-xs text-(--ink-soft)">云仓报名截止：{stamp(a.local_booking_deadline)}（北京时间）</p>}
      <span
        className={
          stale || (!a.availability || a.availability === "unknown")
            ? "text-(--warn)"
            : "text-(--ink)"
        }
      >
        {stale
          ? "待确认 · 库存已过期"
          : (!a.availability || a.availability === "unknown")
            ? "待确认"
            : a.availability === "available" ? "有位" : "无位"}
      </span>
      {a.inventory_status !== "managed" && a.inventory_observed_at && (
        <p className="text-xs text-(--ink-soft)">
          来源查询时间 {stamp(a.inventory_observed_at)}（北京时间）
        </p>
      )}
    </div>
  );
}

export default function Catalog({
  api,
  onSelect,
  initialRoute = null,
}: {
  api: WarehouseClient;
  onSelect: (p: WarehouseProduct) => void;
  initialRoute?: WarehouseProduct | null;
}) {
  const [query, setQuery] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [filters, setFilters] = useState({ query: "", start: "", end: "" });
  const [route, setRoute] = useState<WarehouseProduct | null>(initialRoute);
  const [cursors, setCursors] = useState<string[]>([""]);
  const [page, setPage] = useState<Page<WarehouseProduct> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const cursor = cursors.at(-1)!;
  useEffect(() => {
    const cancel = new AbortController();
    setBusy(true);
    setError("");
    setPage(null);
    const params = new URLSearchParams({ limit: "12" });
    if (cursor) params.set("after", cursor);
    if (filters.start) params.set("start", filters.start);
    if (filters.end) params.set("end", filters.end);
    if (route) params.set("product_id", route.product_id);
    else params.set("query", filters.query);
    api
      .get<Page<WarehouseProduct>>(
        `/advisor/${route ? "departures" : "products"}?${params}`,
        cancel.signal,
      )
      .then((result) => {
        if (!cancel.signal.aborted) setPage(result);
      })
      .catch((err) => {
        if (!cancel.signal.aborted) setError(message(err));
      })
      .finally(() => {
        if (!cancel.signal.aborted) setBusy(false);
      });
    return () => cancel.abort();
  }, [api, route, cursor, filters]);
  function submit(event: FormEvent) {
    event.preventDefault();
    setRoute(null);
    setCursors([""]);
    setFilters({ query, start, end });
  }
  return (
    <section className="mx-auto flex max-w-[1000px] flex-col gap-5 p-4 sm:p-6">
      <div>
        <p className="text-xs font-semibold tracking-[.12em] text-(--accent)">
          TOUR / 云仓选团
        </p>
        <h1 className="mt-1 text-2xl font-semibold">先找线路，再核对团期。</h1>
        <p className="mt-2 text-sm text-(--ink-soft)">
          查询上线线路，选定团期与人数后核对同行结算价。
        </p>
      </div>
      <form
        onSubmit={submit}
        className={`${cardStyle} grid gap-3 sm:grid-cols-2`}
      >
        <label className="text-sm sm:col-span-2">
          线路名称或出发地
          <input
            className={`${inputStyle} mt-1`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={500}
            placeholder="输入线路名称、编号或出发地"
          />
        </label>
        <label className="text-sm">
          最早出发日期
          <input
            type="date"
            className={`${inputStyle} mt-1`}
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label className="text-sm">
          最晚出发日期
          <input
            type="date"
            min={start || undefined}
            className={`${inputStyle} mt-1`}
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <button className="btn-primary justify-self-start" disabled={busy}>
          查询线路
        </button>
      </form>
      {route && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">{route.title} · 团期</h2>
          <button
            className="chip"
            onClick={() => {
              setRoute(null);
              setCursors([""]);
            }}
          >
            返回线路
          </button>
        </div>
      )}
      {busy && <p role="status">正在读取云仓…</p>}
      {error && (
        <p
          role="alert"
          className="rounded-xl bg-(--danger-soft) p-3 text-(--danger)"
        >
          {error}
        </p>
      )}
      {page && !page.items.length && (
        <div className={`${cardStyle} text-(--ink-soft)`}>
          当前条件下没有可见{route ? "团期" : "线路"}
          。可以简化目的地关键词、调整日期，或联系管理员核对产品上架状态。
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {page?.items.map((product) => (
          <ProductCard
            key={product.product_id}
            product={product}
            select={(p) => {
              if (p.product_id.startsWith("WD-")) onSelect(p);
              else {
                setRoute(p);
                setCursors([""]);
              }
            }}
          />
        ))}
      </div>
      {page && (
        <nav
          aria-label="目录分页"
          className="flex items-center justify-between gap-2 text-sm"
        >
          <button
            className="chip"
            disabled={busy || cursors.length === 1}
            onClick={() => setCursors((all) => all.slice(0, -1))}
          >
            上一页
          </button>
          <span>第 {cursors.length} 页</span>
          <button
            className="chip"
            disabled={busy || !page.next_cursor}
            onClick={() => setCursors((all) => [...all, page.next_cursor!])}
          >
            下一页
          </button>
        </nav>
      )}
    </section>
  );
}
