import type { BriefEnvelope } from "@/lib/warehouse";
import type { WarehouseEvent } from "web-shared/warehouse-client";

export type Customer = {id:string;version:number;body:{name:string;contact:string;note:string;travelers:Traveler[]}};
export type Traveler = {name:string;birthday:string|null;document_type:string;document_number:string;document_expiry:string|null;confirmed:boolean};
export type RecordItem = {id:string;kind:string;brief_version:number;body:Record<string,any>;created_at:string;stale?:boolean};
export type Deal = {id:string;title:string;customer_name?:string;brief?:any;brief_version?:number;updated_at?:string};
export type DealDetail = Deal & {conversation_available?:boolean;ledger?:{sale:RecordItem|null;receipts:RecordItem[];received:string};customer:Customer|null;brief:BriefEnvelope;records:RecordItem[];assets:{id:string;filename:string;media_type:string;created_at:string}[]};
export type Turn = {id:string;message:string;status:string;events:WarehouseEvent[]};
export type Transcript = {turns:Turn[];busy:boolean;next_cursor:string|null};
export type Inquiry = {id:string;question:string;brief_version:number;context:{route_name:string;party:any;rooms:any;window:any};replies:{id:string;answer:string;created_at:string}[];status:string;created_at:string};
export const confirmationItems=["线路与团期","出行人数与年龄","房型与儿童占床","费用包含与另付","退改与材料要求"];
export const recordLabels:Record<string,string>={state:"需求版本",proposal:"待采纳变化",adoption:"变更处理",memory:"客人记忆",qa:"问答簿",note:"行程备注",plan:"方案草稿",confirmation:"确认单",retail_quote:"销售报价",sale:"线下成交",receipt:"收款记录",task:"行前待办",task_done:"完成待办",inquiry_adoption:"已采纳商户回复"};
