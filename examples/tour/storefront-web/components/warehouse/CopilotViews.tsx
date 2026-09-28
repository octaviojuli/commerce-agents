"use client";
import { useState } from "react";
import type { Deal } from "./copilot-types";
import { amount, stamp } from "@/lib/warehouse";
import { copyText } from "./mobile";

export const stages = ["需求", "选线", "定团", "确认", "报价", "成交"];
export function stageOf(brief: any, sold = false, hasRoutes = false) {
  return sold
    ? 5
    : brief?.quote_id
      ? 4
      : brief?.departure_id
        ? 3
        : brief?.route_id
          ? 2
          : hasRoutes
            ? 1
            : 0;
}
export function StageStrip({ stage }: { stage: number }) {
  return (
    <ol className="cp-stage-strip" aria-label={`当前阶段：${stages[stage]}`}>
      {stages.map((name, i) => (
        <li key={name} data-current={i === stage} data-done={i < stage}>
          <span />
          {name}
        </li>
      ))}
    </ol>
  );
}
export function nextStep(deal: Deal) {
  return deal.waiting_reply
    ? "核对商户回复"
    : deal.pending_tasks
      ? `处理 ${deal.pending_tasks} 项跟进`
      : deal.sold
        ? "核对行前材料"
        : deal.brief?.quote_id
          ? "核对报价并跟进客人"
          : deal.brief?.departure_id
            ? "补齐确认单"
            : deal.brief?.route_id
              ? "选择合适团期"
              : "继续了解出行需求";
}
export function DealCard({
  deal,
  open,
  active = false,
}: {
  deal: Deal;
  open: (id: string) => void;
  active?: boolean;
}) {
  const b = deal.brief || {},
    total =
      b.party_total?.value ??
      (b.adults?.value || 0) +
        (b.children?.value || 0) +
        (b.seniors?.value || 0);
  return (
    <button
      className="cp-deal-card"
      aria-current={active ? "page" : undefined}
      onClick={() => open(deal.id)}
    >
      <div className="cp-section-head">
        <b>{deal.customer_name || deal.title || "新客人"}</b>
        <span className="cp-tag">
          {deal.waiting_reply
            ? "商户已回复"
            : deal.deadline
              ? `${stamp(deal.deadline)} 到期`
              : deal.pending_tasks
                ? "待跟进"
                : "沟通中"}
        </span>
      </div>
      <p>
        {total ? `${total}人 · ` : ""}
        {(b.destinations?.value || b.destination_regions?.value || []).join(
          " · ",
        ) || "方向待定"}{" "}
        · {b.window?.value ? `${b.window.value.start.slice(5)} 起` : "时间待定"}
      </p>
      <StageStrip stage={stageOf(b, deal.sold)} />
      <footer>
        <span>下一步 · {nextStep(deal)}</span>
        <span aria-hidden>›</span>
      </footer>
    </button>
  );
}
export function displayRoute(value: string) {
  return value
    .replace(/^\s*(?:W[PDO]-[a-f\d-]{8,}|[A-Z]{1,5}\d{1,5})\s*[-－_：:]*/i, "")
    .trim();
}

export async function exportComparison(data: any) {
  const canvas = document.createElement("canvas");
  canvas.width = 1080;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("当前浏览器无法生成图片");
  const lines:{text:string;size:number;color:string;y:number}[]=[];
  let y=180;
  const line=(text:string,size=24,color="#26343d")=>{
    ctx.font=size+"px sans-serif";let row="";
    for(const char of text){if(ctx.measureText(row+char).width>970){lines.push({text:row,size,color,y});y+=size*1.6;row="";}row+=char;}
    if(row){lines.push({text:row,size,color,y});y+=size*1.6;}
  };
  for(const route of data.routes||[]){
    line(displayRoute(route.title),30,"#0f6e6e");
    line((route.days||"待定")+"天 · "+(route.depart_city||"出发地待确认")+" · "+(route.sales_total?route.currency+" "+route.sales_total+" / 全家":"费用待核实"));
    for(const d of (route.dimensions||[]).slice(0,2))line(d.concern+"："+d.text,22);
    for(const drawback of route.drawbacks||[])line("要说清："+drawback,22,"#85442c");
    y+=28;
  }
  line("选择方案仅表示沟通意向。具体费用、房间及退改条件须再次核对。",21,"#56616d");
  canvas.height=Math.ceil(y+48);
  ctx.fillStyle="#f3f4f6";ctx.fillRect(0,0,1080,canvas.height);
  ctx.fillStyle="#0f6e6e";ctx.fillRect(0,0,1080,130);
  ctx.fillStyle="white";ctx.font="bold 40px sans-serif";ctx.fillText("旅行方案 · 一起看看如何取舍",48,80);
  for(const row of lines){ctx.font=row.size+"px sans-serif";ctx.fillStyle=row.color;ctx.fillText(row.text,48,row.y);}
  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/png"),
  );
  if (!blob) throw new Error("图片生成失败");
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = "旅行方案比较.png";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ComparisonCard({
  data,
  save,
  busy = false,
}: {
  data: any;
  save?: () => void;
  busy?: boolean;
}) {
  const [notice, setNotice] = useState("");
  return (
    <section className="cp-panel cp-comparison">
      <div className="cp-section-head">
        <h3>按客人顾虑比较</h3>
        <button
          onClick={() =>
            exportComparison(data).catch((e) => setNotice(String(e)))
          }
        >
          生成对客图片
        </button>
      </div>
      <p className="cp-muted">{data.price_answer}</p>
      <div className="cp-compare-scroll">
        {(data.routes || []).map((r: any) => (
          <article key={r.product_id || r.title}>
            <h3>{displayRoute(r.title)}</h3>
            {(r.dimensions || []).map((d: any) => (
              <div className="cp-record" key={d.concern}>
                <b>{d.concern}</b>
                <p>{d.text}</p>
                <small>
                  {d.known
                    ? `已复核依据 ${d.fact_ids?.length || 0} 项`
                    : "待核实"}
                </small>
              </div>
            ))}
            <div className="cp-drawback">
              <b>要说清的限制</b>
              {(r.drawbacks || []).map((d: string, i: number) => (
                <p key={i}>{d}</p>
              ))}
            </div>
            <p>
              客人价{" "}
              {r.per_person
                ? amount(r.per_person, r.currency) + " / 人"
                : "待核价"}
            </p>
            <p>全家合计 {amount(r.sales_total, r.currency)}</p>
            {"profit" in r && (
              <small>
                预计毛利 {amount(r.profit, r.currency)} · 毛利率{" "}
                {r.margin != null ? `${r.margin}%` : "待核价"}
              </small>
            )}
          </article>
        ))}
      </div>
      <p>{data.recommendation}</p>
      <div className="cp-row">
        {save && (
          <button disabled={busy} className="cp-primary" onClick={save}>
            保存为方案
          </button>
        )}
        <button
          onClick={async () => {
            await copyText(
              (data.routes || [])
                .map(
                  (r: any) =>
                    `${displayRoute(r.title)}\n${(r.dimensions || []).map((d: any) => `${d.concern}：${d.text}`).join("\n")}\n要说清：${r.drawbacks?.join("；")}`,
                )
                .join("\n\n"),
            );
            setNotice("对客比较文字已复制");
          }}
        >
          复制对客比较
        </button>
      </div>
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
