"use client";

import Link from "next/link";
import { useState } from "react";
import { api, cover, money } from "@/lib/api";
import type { Answer, Card, ChangeItem, Gates, Impact, RouteCard, RouteRead, Search } from "@/lib/types";
import { Supplier, Tag, useToast } from "./ui";

const LABELS: Record<string, string> = {
  destinations: "目的地",
  window: "时间",
  days: "天数",
  depart_city: "出发",
  party: "人数",
  rooms: "房间",
  budget: "预算",
  preferences: "偏好",
  themes: "主题",
};
const PREFS: Record<string, string> = {
  slow_pace: "节奏别太累",
  no_shopping: "不进购物店",
  no_self_pay: "少自费",
  family: "适合孩子",
  senior: "照顾老人",
};
const MISSING: Record<string, string> = {
  destinations: "往哪个方向",
  window: "什么时候",
  adults: "几位大人",
  children: "有没有孩子",
  child_ages: "孩子几岁",
  child_beds: "孩子是否占床",
  senior_ages: "老人年龄",
  party_total: "核对总人数与人员构成",
  rooms: "房型",
  passports: "护照是否齐全（下单前）",
};

/** Display a field value the way the need card shows it. */
export function show(field: string, value: unknown): string {
  if (value === null || value === undefined) return "未提";
  const v = value as Record<string, any>;
  switch (field) {
    case "destinations": {
      const parts = [];
      if (v.must?.length) parts.push(v.must.join(" · "));
      else if (v.regions?.length) parts.push(v.regions.join(" · "));
      if (v.examples?.length) parts.push("比如" + v.examples.join("、"));
      if (v.exclude?.length) parts.push("不去" + v.exclude.join("、"));
      return parts.join("，") || "未提";
    }
    case "window": {
      const [, sm, sd] = String(v.start).split("-").map(Number);
      const [, em, ed] = String(v.end).split("-").map(Number);
      return `${sm}/${sd}–${em}/${ed}${v.label ? " " + v.label : ""}`;
    }
    case "days":
      return v.min === v.max ? `${v.min} 天` : `${v.min}–${v.max} 天`;
    case "party": {
      if (v.adults == null) return v.total_count ? `共 ${v.total_count} 人 · 成人构成待确认${v.children?.length === 0 ? " · 无儿童" : ""}` : "人数未定";
      let t = `${v.adults} 大`;
      if (v.children === null || v.children === undefined) t += " · 孩子未问";
      else if (v.children.length) {
        const ages = v.children.filter((c: any) => c.age != null).map((c: any) => `${c.age} 岁`);
        t += ` ${v.children.length} 小` + (ages.length ? `（${ages.join("、")}）` : "");
      }
      if (v.seniors?.length) {
        const ages = v.seniors.filter((s: any) => s.age != null).map((s: any) => `${s.age} 岁`);
        t += ` + ${v.seniors.length} 老` + (ages.length ? `（${ages.join("、")}）` : "");
      }
      return (v.total_count ? `共 ${v.total_count} 人 · ` : "") + t;
    }
    case "rooms": {
      const parts = [];
      if (v.doubles) parts.push(`大床 ${v.doubles} 间`);
      if (v.twins) parts.push(`双床 ${v.twins} 间`);
      if (v.singles) parts.push(`单间 ${v.singles} 间`);
      return parts.join(" + ") || "未提";
    }
    case "budget":
      return `每人 ≤ ${money(v.per_person)}`;
    case "preferences":
      return (value as string[]).map((p) => PREFS[p] ?? p).join("、");
    case "themes":
      return (value as string[]).join("、");
    default:
      return String(value);
  }
}

export function Who({ children, tone }: { children: React.ReactNode; tone?: "sun" }) {
  return (
    <div className="who" style={tone === "sun" ? { color: "var(--sun-ink)" } : undefined}>
      <i style={tone === "sun" ? { background: "var(--sun)" } : undefined}>{tone === "sun" ? "⚡" : "✦"}</i>
      {children}
    </div>
  );
}

export function ReadCard({ items }: { items: ChangeItem[] }) {
  return (
    <>
      <Who>从这段话里读到 {items.length} 项</Who>
      <div className="card fields">
        {items.map((i) => (
          <div className="fr" key={i.field}>
            <span className="k">{LABELS[i.field] ?? i.field}</span>
            <span className="v">
              {show(i.field, i.new)}
              <small>{i.source === "inferred" ? i.hint || "推断" : `“${i.evidence}”`}</small>
            </span>
            <span className={"s " + (i.source === "inferred" ? "inf" : "said")}>{i.source === "inferred" ? "推断" : "原话"}</span>
          </div>
        ))}
      </div>
    </>
  );
}

export function GatesCard({ gates }: { gates: Gates }) {
  const quote = gates.quote.missing.filter((k) => !gates.search.missing.includes(k));
  return (
    <div className="card gates">
      <div className="gt">
        <span className={"gi " + (gates.search.ready ? "ok" : "no")}>{gates.search.ready ? "✓" : gates.search.missing.length}</span>
        <span className="grow">
          <b>{gates.search.ready ? "可以找线" : "还不能找线"}</b>
          <small>{gates.search.ready ? "目的地、时间都有了" : "缺：" + gates.search.missing.map((k) => MISSING[k]).join("、")}</small>
        </span>
      </div>
      <div className="gt">
        <span className={"gi " + (gates.quote.ready ? "ok" : "no")}>{gates.quote.ready ? "✓" : quote.length}</span>
        <span className="grow">
          <b>{gates.quote.ready ? "可以报价" : "还不能报价"}</b>
          <small>{gates.quote.ready ? "人数、年龄、占床、房间都齐了" : "缺：" + quote.map((k) => MISSING[k]).join("、") + "。找线不需要，报价前再问"}</small>
        </span>
      </div>
      <div className="gt">
        <span className="gi wait">·</span>
        <span className="grow">
          <b>以后要问</b>
          <small>{gates.later.map((k) => MISSING[k]).join("、")}</small>
        </span>
      </div>
    </div>
  );
}

export function ClarityCard({ card }: { card: Extract<Card, { type: "clarity" }> }) {
  return (
    <>
      <Who>现在知道的很少</Who>
      <div className="card clarity">
        <div className="meter">
          <i style={{ width: `${Math.max(card.clarity, 6)}%` }} />
        </div>
        {card.known.length > 0 && (
          <div className="kn">
            <Tag tone="ok">知道</Tag>
            <span>{card.known.join(" · ")}</span>
          </div>
        )}
        <div className="kn">
          <Tag tone="gray">不知道</Tag>
          <span>{card.unknown.join(" · ")}</span>
        </div>
        <div className="lbl">找线至少要知道“什么时候”和“大概方向”；其他的可以边推荐边问。</div>
      </div>
    </>
  );
}

export function DirectionsCard({ items, deal, onPicked }: { items: Extract<Card, { type: "directions" }>["items"]; deal?: string; onPicked?: () => void }) {
  const toast = useToast();
  return (
    <>
      <Who>客人不想多说，先给 {items.length} 个方向</Who>
      <div className="dirs">
        {items.map((d) => (
          <div className="dir" key={d.name}>
            <div className={"cv " + cover(d.label)} />
            <div className="in">
              <b>{d.name}</b>
              <small>
                {d.days} · {d.count} 条在售
              </small>
              <span className="lbl">例：{d.example}</span>
              {deal && (
                <button
                  className="b sm b-soft"
                  style={{ marginTop: 6 }}
                  onClick={async () => {
                    await api.post(`/deals/${deal}/direction`, { label: d.label, region: d.region, start: d.start, end: d.end });
                    toast(`已按“${d.name}”记为探索方向，可以找线了`);
                    onPicked?.();
                  }}
                >
                  客人选这个
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="card" style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
        方向来自目录里未来半年真实有团的线路。客人选了哪个，就按它找线；从选择里推断的条件只影响排序。
      </div>
    </>
  );
}

export function ChangeCard({
  deal,
  proposalId,
  from,
  items,
  impact,
  onDone,
}: {
  deal: string;
  proposalId: string;
  from: number;
  items: ChangeItem[];
  impact: Impact[];
  onDone: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const pending = items.filter((i) => i.status === "pending");
  async function adopt(fields: string[] | null, keep = false) {
    setBusy(true);
    try {
      const r = await api.post<{ version: number; effects: string[] }>(`/deals/${deal}/proposals/${proposalId}`, { fields, keep });
      toast(keep ? "已保留原需求" : `已采纳，需求 v${r.version}` + (r.effects.length ? "：" + r.effects.join("；") : ""));
      onDone();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!pending.length)
    return (
      <div className="turn">
        v{from} 的变更单已处理：{items.map((i) => `${LABELS[i.field]}${i.status === "adopted" ? "已采纳" : "保留原值"}`).join("、")}
      </div>
    );
  return (
    <>
      <Who>
        变更单 · v{from} → v{from + 1}
      </Who>
      <div className="card diff" id={"change-" + proposalId}>
        {items.map((i) => (
          <div className={"dr" + (i.old === null ? " add" : "")} key={i.field}>
            <span className="k">{LABELS[i.field]}</span>
            <div className="grow">
              {i.old !== null && <span className="old">{show(i.field, i.old)}</span>}
              <span className="new">{show(i.field, i.new)}</span>
              <small>
                {i.evidence ? `“${i.evidence}”` : ""}
                {i.source === "inferred" ? ` · ${i.hint || "推断"}` : ""}
              </small>
            </div>
            {i.status === "pending" ? (
              <span className="row" style={{ flexDirection: "column", gap: 4 }}>
                <button className="ok" disabled={busy} onClick={() => adopt([i.field])} style={{ background: "none", cursor: "pointer", minHeight: 30 }}>
                  采纳
                </button>
                <button className="linkish" disabled={busy} onClick={() => adopt([i.field], true)}>
                  不改
                </button>
              </span>
            ) : (
              <Tag tone={i.status === "adopted" ? "ok" : "gray"}>{i.status === "adopted" ? "已采纳" : "已保留"}</Tag>
            )}
          </div>
        ))}
        <div className="row" style={{ gap: 8, marginTop: 8 }}>
          <button className="b sm b-ghost grow" disabled={busy} onClick={() => adopt(null, true)}>
            都不改
          </button>
          <button className="b sm b-br grow" disabled={busy} onClick={() => adopt(pending.map((i) => i.field))}>
            全部采纳 → v{from + 1}
          </button>
        </div>
      </div>
      {impact.length > 0 && (
        <>
          <Who tone="sun">采纳后会发生什么（程序算出）</Who>
          <div className="card impact">
            {impact.map((m) => (
              <div className={"im " + m.level} key={m.title}>
                <b>{m.title}</b>
                <small>{m.text}</small>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}

export function RoutesCard({ deal, search, picked, onPick }: { deal: string; search: Search; picked: string[]; onPick: (id: string) => void }) {
  return (
    <>
      <Who>
        {search.query_context ? "按临时条件" : "按需求"}找到 {search.cards.length} 条
        {search.alternatives_only ? "（出发地都不同，只能作备选）" : ""}
      </Who>
      <div className="opts">
        {search.cards.map((c, i) => (
          <RouteOpt key={c.product_id} card={c} rank={i + 1} deal={deal} picked={picked.includes(c.product_id)} onPick={() => onPick(c.product_id)} />
        ))}
      </div>
      {search.explain && <div className="card explain">{search.explain}</div>}
      <Link className="linkish" href={`/deals/${deal}/search`}>
        看查询条件和漏斗 ›
      </Link>
    </>
  );
}

export function RouteReadCard({ deal, item }: { deal: string; item: RouteRead }) {
  const d = item.dates;
  return <section className="card route-read">
    <b>{item.title}</b>
    {d.requested && <small>查询日期：{d.requested}</small>}
    <p>{d.status === "error" ? d.message : d.status === "nearby" ? "所问日期范围内未查到团，邻近日期如下。出发需求未改变。" : d.status === "available" ? "所问日期范围内有团期：" : d.status === "partial" ? "查询未覆盖全部团期，请缩小日期范围。" : `所问日期及前后 ${d.around_days ?? 10} 天内未查到团期。`}</p>
    {!!d.items.length && <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>{Array.from(new Set(d.items.map(x => x.date))).map(day => <span className="tag t-br" key={day}>{day}</span>)}</div>}
    {d.partial && <small>这里只展示部分结果。</small>}
    <p>{item.itinerary.message || "已发布行程"}</p>
    {item.itinerary.summary.map(line => <small key={line}>{line}</small>)}
    {item.itinerary.notice && <p className="tip">{item.itinerary.notice}</p>}
    <div className="row" style={{ gap: 8 }}>
      <Link className="b sm b-soft" href={`/routes/${item.product_id}?deal=${deal}`}>查看线路</Link>
      {item.itinerary.status === "published" && <a className="b sm b-br" href={`/api/routes/${item.product_id}/page?deal=${deal}`} target="_blank" rel="noreferrer">看完整线路</a>}
    </div>
  </section>;
}

export function RouteOpt({ card, rank, deal, picked, onPick }: { card: RouteCard; rank: number; deal: string; picked: boolean; onPick: () => void }) {
  return (
    <div className="opt">
      <div className={"cv " + cover(card.title)} style={{ height: 86 }}>
        <span className="rk">{card.alternative ? "备选" : `No.${rank} · 匹配 ${card.score}%`}</span>
        <button className={"pick" + (picked ? " on" : "")} aria-label={picked ? "取消比较" : "加入比较"} onClick={onPick}>
          {picked ? "✓" : "+"}
        </button>
      </div>
      <div className="in">
        <Link href={`/routes/${card.product_id}?deal=${deal}`}>
          <h4>{card.title}</h4>
        </Link>
        <span className="m">
          {card.days ?? "—"} 天 · {card.depart_city || "出发地待核实"}出发 <Supplier s={card.supplier} />
        </span>
        {card.also && card.also.length > 0 && (
          <div className="also">
            别家也在卖：
            {card.also.map((a) => (
              <Link key={a.product_id} href={`/routes/${a.product_id}?deal=${deal}`}>
                <Supplier s={a.supplier} />
              </Link>
            ))}
          </div>
        )}
        <div className="three">
          <div>
            <b>{card.three.hotel}</b>
            <small>酒店</small>
          </div>
          <div>
            <b>{card.three.meals}</b>
            <small>含餐</small>
          </div>
          <div>
            <b>{card.three.shopping}</b>
            <small>购物</small>
          </div>
        </div>
        <div className="fit">
          {card.yes.map((t) => (
            <span className="y" key={"y" + t}>
              ✓ {t}
            </span>
          ))}
          {card.no.map((t) => (
            <span className="n" key={"n" + t}>
              ✕ {t}
            </span>
          ))}
          {card.unknown.map((t) => (
            <span className="q" key={"q" + t}>
              ? {t}
            </span>
          ))}
        </div>
        <div className="pr">
          {card.price ? (
            <>
              <b>{money(card.price.per_person)}</b>
              <small>/人 · 全家 {money(card.price.total)}</small>
            </>
          ) : (
            <small>选团期后核价</small>
          )}
        </div>
      </div>
    </div>
  );
}

export function AnswersCard({ deal, items }: { deal: string; items: Answer[] }) {
  const toast = useToast();
  const known = items.filter((a) => a.kind !== "unknown").length;
  return (
    <>
      <Who>
        {items.length} 个问题，{known} 个有依据
      </Who>
      <div className="card qa">
        {items.map((a) => (
          <div className={"it" + (a.kind === "unknown" ? " unk" : "")} key={a.question}>
            <span className="q">{a.question}</span>
            <span className="a">{a.answer}</span>
            {a.kind !== "unknown" ? (
              <span className="ev">
                {[...new Set(a.facts.map((f) => f.section))].map((s) => (
                  <span className="src" key={s}>
                    {s}
                  </span>
                ))}
                {a.route && <span className="lbl">{a.route}</span>}
                {a.kind === "advice" && <Tag tone="ai">建议是助手给的</Tag>}
                {a.facts.some((f) => f.reviewed === false) && <Tag tone="sun">资料未人工审核</Tag>}
              </span>
            ) : (
              <div className="row" style={{ marginTop: 4 }}>
                <Tag tone="sun">待核实</Tag>
                {a.product_id && (
                  <button
                    className="b sm b-soft"
                    onClick={async () => {
                      await api.post(`/deals/${deal}/notes`, { product_id: a.product_id, category: "question", text: a.question });
                      toast("已记进问答簿，问到答案后补上");
                    }}
                  >
                    问供应商
                  </button>
                )}
                <span className="lbl">记进问答簿</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
