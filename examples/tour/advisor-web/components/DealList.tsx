"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DealCard } from "@/lib/types";
import { Dots, Loading, Tag } from "./ui";

const SEGMENTS: { key: string; label: string; test: (d: DealCard) => boolean }[] = [
  { key: "me", label: "要我动", test: (d) => d.needs_me },
  { key: "need", label: "需求", test: (d) => d.stage === 0 },
  { key: "pick", label: "选线", test: (d) => d.stage === 1 },
  { key: "date", label: "定团", test: (d) => d.stage === 2 },
  { key: "quote", label: "报价", test: (d) => d.stage === 3 },
  { key: "won", label: "成交", test: (d) => d.stage >= 4 },
  { key: "all", label: "全部", test: () => true },
];

export function initial(title: string) {
  return (title.replace(/^(客人|新客人)/, "") || "新").slice(0, 1);
}

export default function DealList({ compact = false }: { compact?: boolean }) {
  const path = usePathname();
  const [deals, setDeals] = useState<DealCard[] | null>(null);
  const [segment, setSegment] = useState("me");
  useEffect(() => {
    let live = true;
    const load = () => api.get<{ items: DealCard[] }>("/deals").then((d) => live && setDeals(d.items)).catch(() => {});
    load();
    const t = setInterval(load, 20000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [path]);
  const counts = Object.fromEntries(SEGMENTS.map((s) => [s.key, (deals ?? []).filter(s.test).length]));
  const active = segment === "me" && counts.me === 0 ? "all" : segment;
  const shown = (deals ?? []).filter(SEGMENTS.find((s) => s.key === active)!.test);
  return (
    <>
      {compact && (
        <div className="top white">
          <div className="tt">
            <b style={{ fontSize: 18 }}>跟单</b>
            <small>{deals ? `${deals.length} 位客人` : ""}</small>
          </div>
        </div>
      )}
      <div className="segs" style={compact ? { paddingTop: 10 } : undefined}>
        {SEGMENTS.map((s) => (
          <span key={s.key} className={active === s.key ? "on" : ""} onClick={() => setSegment(s.key)} role="button" tabIndex={0}>
            {s.label}
            <b>{counts[s.key]}</b>
          </span>
        ))}
      </div>
      <div className="sc">
        <div className="pad" style={{ paddingTop: 0 }}>
          {!deals && <Loading />}
          {deals && !shown.length && <div className="empty">这里还没有客人。点“粘贴客人消息”开始第一单。</div>}
          {shown.map((d) => (
            <Link key={d.id} className="card deal" href={`/deals/${d.id}`} style={path.includes(d.id) ? { outline: "2px solid var(--brand)" } : undefined}>
              <div className="h">
                <span className={"av" + (d.stage >= 4 ? "" : d.needs_me ? " o" : " g")}>{initial(d.title)}</span>
                <div className="grow">
                  <b>{d.title}</b>
                  <small>{d.line}</small>
                </div>
                {d.tag && <Tag tone={d.tag.tone}>{d.tag.text}</Tag>}
              </div>
              <div className="dots">
                <Dots stage={d.stage} />
              </div>
              {d.next && (
                <div className="nx">
                  <span className="grow">
                    <b>下一步：</b>
                    {d.next}
                  </span>
                </div>
              )}
            </Link>
          ))}
        </div>
      </div>
    </>
  );
}
