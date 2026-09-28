import type { Metadata } from "next";
import { notFound } from "next/navigation";
import SharedQuote from "./shared-quote";

export const metadata:Metadata={title:"旅行市场报价",robots:{index:false,follow:false},referrer:"no-referrer"};
export const dynamic="force-dynamic";
export default function Page(){
  if(process.env.TOUR_BACKEND_MODE!=="warehouse")notFound();
  return <SharedQuote/>;
}
