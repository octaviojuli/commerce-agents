"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AnswersCard,
  ChangeCard,
  ClarityCard,
  DirectionsCard,
  GatesCard,
  ReadCard,
  RoutesCard,
  RouteReadCard,
  Who,
} from "@/components/Cards";
import { Chips, Draft, ErrorBox, Loading, Sheet, Top, Track, Working, useToast } from "@/components/ui";
import { ago, api, copy } from "@/lib/api";
import type { Card, Chip, Deal, Turn, TurnResult } from "@/lib/types";
import { STAGES } from "@/lib/types";
import QueryBar from "@/components/QueryBar";

const MENU = [
  ["memory", "需求与记忆"],
  ["search", "按需求找线"],
  ["compare", "线路比较"],
  ["plan", "推荐方案"],
  ["qa", "问答簿"],
  ["itinerary", "行程标注"],
  ["dates", "团期选择"],
  ["confirm", "确认单"],
  ["quote", "正式报价"],
  ["docs", "收证件"],
  ["after", "出发前清单"],
];

export default function DealPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const [deal, setDeal] = useState<Deal | null>(null);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"advisor" | "customer">("advisor");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [menu, setMenu] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [directions, setDirections] = useState<Extract<Card, { type: "directions" }>["items"] | null>(null);
  const feed = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const d = await api.get<Deal>(`/deals/${id}`);
    setDeal(d);
    return d;
  }, [id]);

  const send = useCallback(
    async (message: string, kind = "advisor") => {
      if (!message.trim()) return;
      setBusy(true);
      setError("");
      setText("");
      try {
        await api.post<TurnResult>(`/deals/${id}/turns`, { text: message, kind });
        await load();
      } catch (e) {
        setError((e as Error).message);
        setText(message);
      } finally {
        setBusy(false);
      }
    },
    [id, load],
  );

  useEffect(() => {
    load()
      .then(() => {
        const pending = sessionStorage.getItem("advisor-pending-" + id);
        if (pending) {
          sessionStorage.removeItem("advisor-pending-" + id);
          setMode("customer");
          send(pending, "customer");
        }
      })
      .catch((e) => setError(e.message));
  }, [id, load, send]);

  useEffect(() => {
    feed.current?.scrollTo({ top: feed.current.scrollHeight, behavior: "smooth" });
  }, [deal?.turns.length, busy]);

  async function act(chip: Chip) {
    if (!deal) return;
    if (chip.action.startsWith("relax:")) {
      const field = chip.action.slice(6);
      setBusy(true);
      setError("");
      try {
        await api.post(`/deals/${id}/query/relax`, { field, amount: field === "days" ? 2 : 10 });
        await load();
        router.push(`/deals/${id}/search`);
      } catch (e) {
        setError((e as Error).message);
        await load();
      } finally {
        setBusy(false);
      }
      return;
    }
    const cards = deal.search?.cards ?? [];
    const ids = picked.length >= 2 ? picked : cards.slice(0, 2).map((c) => c.product_id);
    switch (chip.action) {
      case "review_change":
        document.getElementById("change-" + deal.pending[0]?.id)?.scrollIntoView({ behavior: "smooth" });
        break;
      case "copy_draft": {
        const last = [...deal.turns].reverse().find((t) => t.result.draft);
        if (last && (await copy(last.result.draft!.text))) toast("已复制，去微信粘贴");
        break;
      }
      case "directions":
        setDirections((await api.get<{ items: typeof directions }>("/directions")).items);
        break;
      case "edit_need":
        router.push(`/deals/${id}/memory`);
        break;
      case "search":
        setBusy(true);
        try {
          await api.post(`/deals/${id}/search`);
          router.push(`/deals/${id}/search`);
        } finally {
          setBusy(false);
        }
        break;
      case "compare":
        router.push(`/deals/${id}/compare?ids=${ids.join(",")}`);
        break;
      case "plan":
        router.push(`/deals/${id}/plan?ids=${ids.join(",")}`);
        break;
      case "adopt_query":
        if (deal.query) {
          await api.post(`/deals/${id}/query/resolve`, { query_id: deal.query.id, adopt: true });
          toast("已记入客人需求");
          await load();
        }
        break;
      case "release_route":
        // Taking the route back voids its sheets and prices; the deal returns to choosing.
        await api.del(`/deals/${id}/route`);
        toast("已退回这条线，可以重新选线");
        await load();
        break;
      default:
        router.push(`/deals/${id}/${{ dates: "dates", confirm: "confirm", qa: "qa" }[chip.action] ?? chip.action}`);
    }
  }

  if (!deal)
    return (
      <div className="app">
        <Top title="读取中" back="/deals" />
        {error ? <ErrorBox error={error} /> : <Loading />}
      </div>
    );

  const pending = deal.pending.length > 0;
  const quoteMissing = deal.gates.quote.missing.filter((k) => !deal.gates.search.missing.includes(k)).length;
  const last = deal.turns[deal.turns.length - 1];
  return (
    <div className="app">
      <Top
        title={deal.title}
        sub={`${STAGES[deal.stage]} · ${last ? ago(last.at) : "新客人"}${deal.turns.length ? ` · 第 ${deal.turns.length} 轮` : ""}`}
        back="/deals"
        right={
          <button className="ic" aria-label="这一单的全部功能" onClick={() => setMenu(true)}>
            ≡
          </button>
        }
      >
        {null}
      </Top>
      <Track stage={deal.stage} />
      <Link className={"state" + (pending ? " warn" : "")} href={`/deals/${id}/memory`}>
        <span className="ver">{pending ? `v${deal.version}→v${deal.version + 1}` : `v${deal.version}`}</span>
        <span className="grow">{pending ? `客人改了 ${deal.pending[0].items.filter((i) => i.status === "pending").length} 项，等你确认` : deal.need.summary}</span>
        {pending ? (
          <span className="gate no">报价已暂停</span>
        ) : (
          <>
            <span className={"gate " + (deal.gates.search.ready ? "ok" : "no")}>{deal.gates.search.ready ? "可找线" : "还不能找线"}</span>
            {deal.gates.search.ready && <span className={"gate " + (quoteMissing ? "no" : "ok")}>{quoteMissing ? `报价缺 ${quoteMissing}` : "可报价"}</span>}
          </>
        )}
      </Link>
      <div className="sc" ref={feed}>
        <div className="feed">
          {!deal.turns.length && !busy && (
            <div className="empty">
              直接告诉助手要查什么，或切换到“客人说”录入原话。
              <br />
              助手会整理需求、查线路、写好回复。
            </div>
          )}
          {deal.turns.map((t, i) => (
            <TurnView
              key={t.seq}
              deal={deal}
              turn={t}
              latest={i === deal.turns.length - 1}
              picked={picked}
              onPick={(pid) => setPicked((p) => (p.includes(pid) ? p.filter((x) => x !== pid) : [...p, pid].slice(-3)))}
              onAct={act}
              onAsk={(q) => { setMode("customer"); setText(q); }}
              reload={load}
            />
          ))}
          {directions && <DirectionsCard items={directions} deal={id} onPicked={() => { setDirections(null); load(); }} />}
          {busy && <Working text={mode === "advisor" ? "助手正在执行指令、查询资料…" : "助手正在读客人原话、查资料、写回复…"} />}
          <ErrorBox error={error} />
        </div>
      </div>
      <QueryBar deal={id} query={deal.query} onDone={load} />
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(text, mode);
        }}
      >
        <div className="input-modes" role="group" aria-label="消息来源">
          <button type="button" aria-pressed={mode === "advisor"} disabled={busy} onClick={() => setMode("advisor")}>我对助手说</button>
          <button type="button" aria-pressed={mode === "customer"} disabled={busy} onClick={() => setMode("customer")}>客人说</button>
          <span>{mode === "advisor" ? "执行查询，不生成客人回复" : "整理客人需求，准备回复草稿"}</span>
        </div>
        <textarea
          aria-label={mode === "advisor" ? "给助手的指令" : "客人原话"}
          rows={1}
          value={text}
          placeholder={mode === "advisor" ? "例如：前后放宽10天，帮我查团期" : "在这里粘贴或输入客人原话"}
          onChange={(e) => setText(e.target.value)}
        />
        {text.trim() ? (
          <button className="p" disabled={busy}>
            发送
          </button>
        ) : (
          <button
            type="button"
            className="p"
            disabled={busy}
            onClick={async () => {
              setMode("customer");
              try {
                const clip = await navigator.clipboard.readText();
                if (clip.trim()) setText(clip.trim());
              } catch {
                toast("浏览器不让读剪贴板，请长按输入框粘贴");
              }
            }}
          >
            录入客人原话
          </button>
        )}
      </form>
      <Sheet open={menu} onClose={() => setMenu(false)} title="这一单">
        {MENU.map(([path, label]) => (
          <Link key={path} className="b b-line" href={`/deals/${id}/${path}`} onClick={() => setMenu(false)}>
            {label}
          </Link>
        ))}
      </Sheet>
    </div>
  );
}

function TurnView({
  deal,
  turn,
  latest,
  picked,
  onPick,
  onAct,
  onAsk,
  reload,
}: {
  deal: Deal;
  turn: Turn;
  latest: boolean;
  picked: string[];
  onPick: (id: string) => void;
  onAct: (c: Chip) => void;
  onAsk: (q: string) => void;
  reload: () => void;
}) {
  const r = turn.result ?? ({ cards: [], chips: [], notes: [] } as unknown as TurnResult);
  const tags = r.tags ?? [];
  const intent = tags.includes("change")
    ? ["i-chg", "改需求"]
    : tags.includes("ask")
      ? ["i-ask", "提问"]
      : tags.includes("decide") || tags.includes("confirm")
        ? ["i-dec", "做决定"]
        : tags.includes("new_need")
          ? ["i-new", "新需求"]
          : null;
  const proposals = Object.fromEntries(deal.proposals.map((p) => [p.id, p]));
  const readItems = r.cards.filter((c) => c.type === "read");
  return (
    <>
      <div className="turn">
        第 {turn.seq} 轮 · {new Date(turn.at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}
      </div>
      <div className="cust">
        <span className="src">
          {intent && <span className={"intent " + intent[0]}>{intent[1]}</span>}
          {turn.kind === "advisor" ? "我对助手说" : "客人原话"}
        </span>
        <div className="bb">{turn.text}</div>
      </div>
      {turn.result && (
        <div className="aib">
          {r.degraded && <div className="err">{r.degraded}</div>}
          {r.cards.map((c, i) => {
            switch (c.type) {
              case "route_reads":
                return <div key={i}>{c.items.map(item => <RouteReadCard key={item.product_id} deal={deal.id} item={item} />)}</div>;
              case "tasks":
                return <div className="card" key={i}>{c.items.map(task => <p key={task.id}>{task.text}（尚未联系）</p>)}</div>;
              case "read":
                return <ReadCard key={i} items={c.items} />;
              case "clarity":
                return <ClarityCard key={i} card={c} />;
              case "directions":
                return <DirectionsCard key={i} items={c.items} deal={deal.id} onPicked={reload} />;
              case "change": {
                const live = proposals[c.proposal_id];
                return (
                  <ChangeCard
                    key={i}
                    deal={deal.id}
                    proposalId={c.proposal_id}
                    from={c.from}
                    items={live?.items ?? c.items}
                    impact={c.impact}
                    onDone={reload}
                  />
                );
              }
              case "routes":
                return <RoutesCard key={i} deal={deal.id} search={c} picked={picked} onPick={onPick} />;
              case "answers":
                return <AnswersCard key={i} deal={deal.id} items={c.items} />;
              case "chosen":
                return (
                  <div className="card row" key={i}>
                    <span className="tag t-ok">已选</span>
                    <b className="grow">{c.title}</b>
                    <Link className="b sm b-soft" href={`/deals/${deal.id}/dates`}>
                      看团期
                    </Link>
                  </div>
                );
              case "confirm_reply":
                return (
                  <div className="card row" key={i}>
                    <span className={"tag " + (c.status === "confirmed" ? "t-ok" : "t-sun")}>
                      {c.status === "confirmed" ? "客人已确认" : c.status === "stale" ? "确认单已失效" : c.status === "incomplete" ? "确认单还缺信息" : "还有异议"}
                    </span>
                    <span className="grow">
                      {c.status === "stale" ? "客人回的是旧确认单，需要按当前线路、团期、套餐重发" : c.status === "incomplete" ? "客人同意了，但确认单还缺信息，补齐后重发" : c.disputes.length ? c.disputes.join("、") : "确认单全部确认"}
                    </span>
                    <Link className="b sm b-br" href={`/deals/${deal.id}/${c.status === "confirmed" ? "quote" : "confirm"}`}>
                      {c.status === "confirmed" ? "出正式报价" : c.status === "stale" ? "重发确认单" : c.status === "incomplete" ? "补齐确认单" : "改确认单"}
                    </Link>
                  </div>
                );
              default:
                return null;
            }
          })}
          {latest && readItems.length > 0 && r.gates && <GatesCard gates={r.gates} />}
          {r.notes?.map((n) => (
            <div className="lbl" key={n}>
              ⓘ {n}
            </div>
          ))}
          {r.to_advisor && <div className="tip">✦ {r.to_advisor}</div>}
          {r.draft && (
            <Draft
              text={r.draft.text}
              removed={r.draft.removed}
              note={
                r.draft.claims.some((c) => c.reviewed === false)
                  ? "引用的资料未人工审核，发前核对"
                  : r.asked
                    ? "这一轮只问 1 件事"
                    : r.draft.simplified
                      ? "已避开没把握的话"
                      : ""
              }
              sent={r.sent}
              onSent={() => api.post(`/deals/${deal.id}/sent`, { seq: turn.seq }).catch(() => {})}
            />
          )}
          {latest && <Chips items={deal.chips.length ? deal.chips : r.chips} onPick={(i) => onAct((deal.chips.length ? deal.chips : r.chips)[i])} />}
          {latest && r.may_ask && r.may_ask.length > 0 && (
            <Chips label="她可能接着问" items={r.may_ask.map((q) => ({ label: q, will: true }))} onPick={(i) => onAsk(r.may_ask![i])} />
          )}
        </div>
      )}
      {!turn.result && <Who>这一轮没有完成，可以重发</Who>}
    </>
  );
}
