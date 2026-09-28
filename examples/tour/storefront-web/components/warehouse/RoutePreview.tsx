"use client";
import {useEffect,useState} from "react";
import {useRouteCover} from "web-shared/route-cover";
import {RouteDetail,isRouteKit,LegacyRouteDetail} from "web-shared/route-detail";
import {WarehouseClient,message} from "web-shared/warehouse-client";
import {Sheet} from "./mobile";
type Row=Record<string,any>;
export default function RoutePreview({api,productId,departureId}:{api:WarehouseClient;productId:string;departureId?:string}){
 const [open,setOpen]=useState(false),[content,setContent]=useState<Row|null>(null),[error,setError]=useState("");
 useEffect(()=>{if(!open)return;const abort=new AbortController();let running=false;async function load(){if(running)return;running=true;try{const result=await api.get<Row>(`/advisor/products/${productId}/document${departureId?`?departure_id=${encodeURIComponent(departureId)}`:""}`,abort.signal);if(!abort.signal.aborted){setContent(result.body);setError("");}}catch(e){if(!abort.signal.aborted){setContent(null);setError(message(e));}}finally{running=false;}}load();const timer=setInterval(load,30000);window.addEventListener("focus",load);return()=>{abort.abort();clearInterval(timer);window.removeEventListener("focus",load);};},[api,productId,departureId,open]);
 const coverUrl=useRouteCover(api,content?.cover_asset_id);
 return <><button onClick={()=>{setContent(null);setError("");setOpen(true);}}>查看完整行程</button>{open&&<Sheet title="线路行程" wide close={()=>{setOpen(false);setContent(null);setError("");}}>{error?<p role="alert">{error}</p>:!content?<p role="status">正在读取已发布行程…</p>:isRouteKit(content)?<RouteDetail content={content} coverUrl={coverUrl}/>:<LegacyRouteDetail content={content}/>}</Sheet>}</>;
}
