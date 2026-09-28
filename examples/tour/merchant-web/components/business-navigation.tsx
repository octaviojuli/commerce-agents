"use client";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";

const eventName = "warehouse-navigation";
function subscribe(callback: () => void) {
  window.addEventListener("popstate", callback);
  window.addEventListener(eventName, callback);
  return () => { window.removeEventListener("popstate", callback); window.removeEventListener(eventName, callback); };
}
export function useLocation() {
  return useSyncExternalStore(subscribe, () => window.location.pathname + window.location.search, () => "/");
}
export function navigate(href: string) {
  window.history.pushState(null, "", href);
  window.dispatchEvent(new Event(eventName));
}
export function scoped(href: string, organization: string) {
  const url = new URL(href, "https://warehouse.invalid");
  url.searchParams.set("org", organization);
  return url.pathname + url.search;
}
export function BusinessLink({href, children, className="link", id}: {href:string; children:ReactNode; className?:string; id?:string}) {
  return <a href={href} className={className} id={id} onClick={e => {
    if (e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const key = "warehouse-position:" + window.location.pathname + window.location.search;
    try { sessionStorage.setItem(key, JSON.stringify({top:document.querySelector(".portal-main")?.scrollTop ?? 0, focus:id})); } catch {}
    navigate(href);
  }}>{children}</a>;
}
export function useListPosition(location: string, ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const main=document.querySelector(".portal-main");
    if (!main) return;
    const key="warehouse-position:"+location;
    let saved: {top?:number; focus?:string}={};
    try { saved=JSON.parse(sessionStorage.getItem(key) || "{}"); } catch {}
    const frame=requestAnimationFrame(()=>{ main.scrollTop=saved.top ?? 0; if(saved.focus) document.getElementById(saved.focus)?.focus({preventScroll:true}); });
    const record=()=>{ try {sessionStorage.setItem(key,JSON.stringify({top:main.scrollTop,focus:saved.focus}));}catch{} };
    main.addEventListener("scroll",record);
    return ()=>{cancelAnimationFrame(frame);main.removeEventListener("scroll",record);};
  },[location,ready]);
}
