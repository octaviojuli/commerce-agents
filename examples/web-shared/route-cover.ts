"use client";
import {useEffect,useState} from "react";
import {WarehouseClient} from "./warehouse-client";
export function useRouteCover(api:WarehouseClient,id?:string|null){
 const [url,setUrl]=useState<string|undefined>();
 useEffect(()=>{setUrl(undefined);if(!id)return;let current:string|undefined,stopped=false;const abort=new AbortController();async function load(){try{const blob=await (await api.response(`/route-media/${id}`,{signal:abort.signal})).blob();if(stopped)return;const next=URL.createObjectURL(blob);if(current)URL.revokeObjectURL(current);current=next;setUrl(next);}catch{if(!stopped){if(current)URL.revokeObjectURL(current);current=undefined;setUrl(undefined);}}}load();const timer=setInterval(load,30000);window.addEventListener("focus",load);return()=>{stopped=true;abort.abort();clearInterval(timer);window.removeEventListener("focus",load);if(current)URL.revokeObjectURL(current);};},[api,id]);
 return url;
}
