"use client";

import {useEffect, useRef, useState, type ReactNode} from "react";
import type {Card, Unit} from "../lib/route-review";

export type Choice={option:string;input?:string};

function Highlight({text,needle}:{text:string;needle:string}) {
  const key=needle.trim().slice(0,12);
  const at=key.length>=2?text.indexOf(key):-1;
  if(at<0)return <>{text}</>;
  return <>{text.slice(0,at)}<mark>{text.slice(at,at+key.length)}</mark>{text.slice(at+key.length)}</>;
}
function Lines({lines,needle}:{lines:Unit[];needle:string}) {
  return <>{lines.map(u=><p className="review-line" key={u.id}>{u.page?<span className="review-page">第 {u.page} 页</span>:null}<Highlight text={u.text} needle={needle}/></p>)}</>;
}

function Question({card,choice,active,follow,onChoose,onJump}:{card:Card;choice?:Choice;active:boolean;follow:boolean;onChoose:(option:string,input?:string)=>void;onJump:(day?:number,card?:Card)=>void}) {
  const [open,setOpen]=useState(false);
  const picked=card.options.find(o=>o.id===choice?.option);
  const ref=useRef<HTMLElement>(null);
  useEffect(()=>{if(active&&follow)ref.current?.scrollIntoView({block:"nearest",behavior:"smooth"});},[active,follow]);
  const collapsed=!!picked&&!open&&!picked.input;
  if(collapsed)return <section className="review-card done" ref={ref}><div className="row between"><span><span className="review-where">{card.where}</span> {card.title} <span className="muted">→ {picked!.label}</span></span><button className="link" onClick={()=>setOpen(true)}>改选</button></div></section>;
  const shown=card.evidence.lines;
  return <section className={`review-card${active?" active":""}`} ref={ref} aria-label={card.title}>
    <div className="row"><span className="review-where">{card.where}</span><h3>{card.title}</h3></div>
    <p className="review-question">{card.question}</p>
    {card.facts&&<dl className="review-facts">{card.facts.map((f,i)=><div key={i}><dt>{f.source}</dt><dd>{f.value}</dd></div>)}</dl>}
    {shown.length>0 && <div className="review-source" aria-label="原文"><span className="review-tag">原文</span><Lines lines={shown} needle={card.evidence.needle}/></div>}
    {card.evidence.wide.length>0 && <details className="review-wide"><summary>{shown.length?"查看当天完整原文":"原文里没有直接对应的句子，查看当天完整原文"}</summary><div className="review-source"><Lines lines={card.evidence.wide.slice(0,40)} needle={card.evidence.needle}/></div></details>}
    {card.waiting?<p className="muted">{card.waiting}</p>:card.editOnly
      ? <div className="row"><button className="btn primary" onClick={()=>onJump(card.day)}>去修改这一处</button><span className="muted">改完回到这里点“重新检查”</span></div>
      : <div className="review-options">{card.options.map(o=><div className="review-option" key={o.id}>
          <button className={`btn review-choice${choice?.option===o.id?" picked":""}`} aria-pressed={choice?.option===o.id} onClick={()=>{if(o.jump){onJump(card.day,card);return;}onChoose(o.id,choice?.option===o.id?choice.input:o.input?.value);setOpen(false);}}>{o.label}</button>
          {choice?.option===o.id&&o.input&&<input className="input" aria-label={o.input.label} placeholder={o.input.label} value={choice.input??o.input.value} onChange={e=>onChoose(o.id,e.target.value)}/>}
        </div>)}</div>}
  </section>;
}

export function RouteKitReview({cards,choices,onChoose,onKeepAll,onJump,source,footer}:{cards:Card[];choices:Record<string,Choice>;onChoose:(card:Card,option:string,input?:string)=>void;onKeepAll:()=>void;onJump:(day?:number,card?:Card)=>void;source?:ReactNode;footer:ReactNode}) {
  const decided=cards.filter(c=>choices[c.key]).length;
  const pending=cards.filter(c=>!choices[c.key]);
  const next=pending[0]?.key;
  const safe=pending.filter(c=>!c.editOnly&&!c.facts&&!c.waiting&&c.options.some(o=>o.keeps));
  return <section className="review stack">
    <div className="panel stack">
      <div className="row between"><div><h2>逐项确认</h2><p className="muted">系统对照原文后，有 {cards.length} 处拿不准。每一项点一下您认为正确的答案即可，选错了可以“改选”。</p></div>{source}</div>
      <div className="review-progress" role="progressbar" aria-valuemin={0} aria-valuemax={cards.length} aria-valuenow={decided}><span style={{width:`${cards.length?decided/cards.length*100:100}%`}}/></div>
      <div className="row between"><strong>{pending.length?`还剩 ${pending.length} 项`:"全部确认完毕"}</strong>{safe.length>1&&<button className="btn" onClick={onKeepAll}>其余 {safe.length} 项都按系统已处理的结果</button>}</div>
    </div>
    {cards.map(card=><Question key={card.key} card={card} choice={choices[card.key]} active={card.key===next} follow={decided>0} onChoose={(o,i)=>onChoose(card,o,i)} onJump={onJump}/>)}
    {footer}
  </section>;
}
