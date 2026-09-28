"use client";

import {useEffect,useState} from "react";
import CustomerQuote,{type CustomerQuoteData} from "@/components/warehouse/CustomerQuote";

export default function SharedQuote(){
  const [quote,setQuote]=useState<CustomerQuoteData|null>(null),[notice,setNotice]=useState("正在读取报价…");
  useEffect(()=>{
    let active=true;
    let generation=0;
    const abort=new AbortController();
    async function read(){
      const current=++generation;
      const token=window.location.hash.slice(1);
      if(!/^[A-Za-z0-9_-]{43}$/.test(token)){setQuote(null);setNotice("报价已更新，请联系顾问。");return;}
      try{
        const response=await fetch("/warehouse-api/v1/public/quote",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token}),cache:"no-store",credentials:"omit",referrerPolicy:"no-referrer",signal:abort.signal});
        if(!response.ok)throw new Error(response.status===404?"报价已更新，请联系顾问。":"暂时无法核对报价，请稍后刷新。");
        const result=await response.json() as CustomerQuoteData;
        if(active&&current===generation){setQuote(result);setNotice("");}
      }catch(err){if(active&&current===generation){setQuote(null);setNotice(err instanceof Error?err.message:"暂时无法读取报价。");}}
    }
    void read();const timer=setInterval(()=>void read(),30000);
    window.addEventListener("hashchange",read);window.addEventListener("focus",read);
    return ()=>{active=false;abort.abort();clearInterval(timer);window.removeEventListener("hashchange",read);window.removeEventListener("focus",read);};
  },[]);
  return <main className="mx-auto min-h-dvh max-w-xl space-y-4 px-4 py-8"><p className="text-sm font-semibold">旅行报价</p>{quote?<CustomerQuote quote={quote}/>:<p role="status" className="rounded-xl bg-(--well) p-5">{notice}</p>}<p className="text-center text-xs text-(--ink-soft)">请通过原有联系方式联系顾问。</p></main>;
}
