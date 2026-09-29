"use client";

import {useState} from "react";

type Issue = {subject:string;kind:string;expected:string[];observed:string[];source_text:string;proposed_text:string;source_ids:number[]};
type Report = {human_review_required?:boolean;models?:{same_model:boolean};day?:number;status:string;code?:string;review?:Record<string,string[]>;fact_checks?:{status?:string;source_claims?:unknown[];issues?:Issue[]}};
const reasons:Record<string,string> = {
  DAYS_INCOMPLETE:"天号不完整或重复", EDITOR_NUMERIC_FACTS:"数字发生变化",
  EDITOR_CONDITION_LOSS:"限制条件未保留", EDITOR_CONDITION_ADDED:"新增了原文没有的条件", EDITOR_MODEL_UNAVAILABLE:"整理服务未完成处理",
  SEMANTIC_REVIEW:"事实或条件有疑点", EDITOR_FACT_ASSOCIATION:"对象与事实对应关系有变化",
  EDITOR_FACT_BOUND:"事实过多，需要人工核对", EDITOR_SOURCE_COVERAGE:"来源段落未完整保留",
};

export function RouteFactReview({reports,onSourceDay}:{reports:Report[];onSourceDay:(day:number)=>void}) {
  const [onlyIssues,setOnlyIssues]=useState(true);
  const retained=reports.filter(r=>r.status!=="edited"||r.human_review_required);
  const shown=onlyIssues?retained:reports;
  return <details className="panel stack">
    <summary>查看整理核对记录 · {retained.length} 项待复核</summary>
    <p className="muted">此处核对规则可识别的对象、数字和条件；通过不代表全部事实已经核实，发布前仍需人工对照原稿。</p>
    <label className="check"><input type="checkbox" checked={onlyIssues} onChange={e=>setOnlyIssues(e.target.checked)}/>仅显示需复核项目</label>
    {shown.length===0 && <p>本轮没有保留原文的项目，可取消筛选查看全部核对记录。</p>}
    {shown.map((r,i)=><section className="panel stack" key={`${r.day}-${i}`}>
      <div className="row between"><strong>{r.day?`第 ${r.day} 天`:"整条线路"} · {r.status==="edited"?"自动核对通过，待人工复核":`${reasons[r.code||""]||"未通过自动核对"}，已保留原文`}</strong>{r.day && <button className="btn" onClick={()=>onSourceDay(r.day!)}>定位当天原稿</button>}</div>
      {r.human_review_required && <p className="notice warning">本日被抽中人工复核，请逐项核对原文与整理稿。</p>}{r.models?.same_model && <p className="muted">改写与复核使用同一模型，自动复核不作为独立验证。</p>}
      {r.fact_checks?.source_claims && <p className="muted">本轮规则识别 {r.fact_checks.source_claims.length} 条原文声明。</p>}
      {r.fact_checks?.issues?.map((issue,j)=><div className="notice warning stack" key={j}>
        <strong>{issue.subject} · {issue.kind}</strong>
        <p>原稿：{issue.expected.join("、")||"未识别到此项"}</p><p>改写提议：{issue.observed.join("、")||"未保留此项"}</p>
        <details><summary>对照来源第 {issue.source_ids.join("、")} 段与未采用的改写</summary><p className="document-prose">{issue.source_text||"请结合当天完整原稿核对。"}</p><p className="muted">以下为未采用的改写，尚未发布：</p><p className="document-prose">{issue.proposed_text}</p></details>
      </div>)}
      {r.review && <ul>{Object.values(r.review).flat().map((v,j)=><li key={j}>{v}</li>)}</ul>}
    </section>)}
  </details>;
}
