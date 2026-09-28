"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { ErrorBox, Loading, Top, WxIcon, useToast } from "@/components/ui";
import { api, copy, money } from "@/lib/api";
import type { Plan } from "@/lib/types";

const SHORT: Record<string, string> = { 行程节奏: "别太累", 预算: "预算", 购物自费: "购物", 儿童: "孩子", 老人: "老人", 住宿: "住宿", 餐食: "餐食" };

function PlanPage() {
  const { id } = useParams<{ id: string }>();
  const ids = (useSearchParams().get("ids") ?? "").split(",").filter(Boolean);
  const toast = useToast();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [link, setLink] = useState("");
  const started = useRef(false);
  useEffect(() => {
    // Building a plan creates a version: run it once even when effects run twice.
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        if (ids.length) setPlan(await api.post<Plan>(`/deals/${id}/plans`, { product_ids: ids }));
        else {
          const list = await api.get<{ items: Plan[] }>(`/deals/${id}/plans`);
          if (list.items[0]) setPlan(list.items[0]);
          else setError("还没有方案。先找线，选一到三条做方案。");
        }
      } catch (e) {
        setError((e as Error).message);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  async function share() {
    if (!plan) return;
    try {
      const r = await api.post<{ path: string }>(`/deals/${id}/plans/${plan.id}/share`);
      const url = window.location.origin + r.path;
      setLink(url);
      await copy(url);
      toast("方案链接已复制，发给客人");
    } catch (e) {
      toast((e as Error).message);
    }
  }
  return (
    <div className="app">
      <Top
        title={plan ? `推荐方案 · 第 ${plan.version} 版` : "推荐方案"}
        sub={plan ? (plan.status === "void" ? "需求已变，这版已作废" : `按需求 v${plan.need_version}${plan.version > 1 ? " · 上一版已作废" : ""}`) : ""}
        back={`/deals/${id}`}
      />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!plan && !error && <Loading />}
          {plan?.body.routes.map((r, i) => (
            <div className="card pl2" key={r.product_id}>
              <div className="row">
                <span className={"tag " + (i === 0 ? "t-sun" : "t-ok")}>{i === 0 ? "首推" : "备选"}</span>
                <b className="grow">{r.title}</b>
              </div>
              {r.reasons.map((x) => (
                <div className="rsn" key={x.text}>
                  <span className="for">{SHORT[x.concern] ?? x.concern.slice(0, 2)}</span>
                  <span className="grow">{x.text}</span>
                  <span className="src2">{x.source}</span>
                </div>
              ))}
              {r.tell.map((x) => (
                <div className="rsn con" key={x.text}>
                  <span className="for">说清</span>
                  <span className="grow">{x.text}</span>
                  <span className="src2">{x.source}</span>
                </div>
              ))}
              {r.price ? (
                <>
                  <div className="row" style={{ marginTop: 8 }}>
                    <b className="num">{money(r.price.per_person)}/人</b>
                    <span className="lbl">全家 {money(r.price.sales_total ?? r.price.market_total)}</span>
                  </div>
                  {r.price.profit && (
                    <div style={{ marginTop: 5 }}>
                      <span className="tag t-ok">
                        预计毛利 {money(r.price.profit)} · {r.price.margin}%
                      </span>
                    </div>
                  )}
                </>
              ) : (
                <div className="lbl" style={{ marginTop: 8 }}>
                  还没核价。<Link href={`/routes/${r.product_id}?deal=${id}`}>选这条看团期</Link>后可显示客人价和毛利。
                </div>
              )}
              {r.dates.length > 0 && <div className="lbl">可选出发：{r.dates.join("、")}</div>}
            </div>
          ))}
          {plan && (
            <div className="card" style={{ fontSize: 12.5, color: "var(--soft)" }}>
              理由按客人最在意的事写，每条都注明依据；每条线路至少写一条要说清的缺点。需求一变，这版方案自动作废，客人的方案页会显示“方案调整中”。
            </div>
          )}
          {link && (
            <div className="card lbl" style={{ wordBreak: "break-all" }}>
              客人链接：{link}
            </div>
          )}
        </div>
      </div>
      {plan && plan.status !== "void" && (
        <div className="dock">
          <Link className="b b-line" href={link ? link.replace(window.location.origin, "") : "#"} onClick={(e) => !link && (e.preventDefault(), share())}>
            预览
          </Link>
          <div className="grow">
            <button className="b b-wx" onClick={share}>
              <WxIcon />
              发第 {plan.version} 版方案
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <PlanPage />
    </Suspense>
  );
}
