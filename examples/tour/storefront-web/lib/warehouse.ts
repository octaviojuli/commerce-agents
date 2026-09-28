import { AgentApi, type AgentEvent } from "web-shared";
import {
  WarehouseClient,
  message,
  warehouseEvents,
} from "web-shared/warehouse-client";

export type WarehouseProduct = {
  product_id: string;
  title: string;
  short_description?: string | null;
  attributes: Record<string, string>;
  option_values?: Record<string, string>;
};
export type Page<T> = { items: T[]; next_cursor: string | null };
export type QuoteLine = {
  code: string;
  label?: string;
  quantity: number;
  unit_amount: string | null;
  total: string | null;
};
export type Party = {
  adults: number;
  children: number;
  seniors: number;
  single_rooms: number;
  child_ages: number[];
  room_type: string | null;
  rooms?: Rooms | null;
};
export type Quote = {
  offer_name?: string;
  service_description?: string;
  product_name?: string;
  product_name_origin?: string;
  departure_code?: string;
  source_name?: string;
  quote_id: string;
  departure_date: string;
  business_timezone?: string;
  local_booking_deadline?: string | null;
  party: Party;
  currency: string | null;
  market_total: string | null;
  settlement_total: string | null;
  known_market_subtotal: string | null;
  known_settlement_subtotal: string | null;
  market_lines: QuoteLine[];
  settlement_lines: QuoteLine[];
  included_items?: string[];
  missing_items: string[];
  complete: boolean;
  snapshot_stale: boolean;
  availability: "available" | "unavailable" | "unknown";
  capacity_sufficient: boolean | null;
  observed_at: string;
  expires_at: string | null;
  fresh_until: string;
};

/** Original chat shell, with warehouse identities and no legacy session/cart calls. */
export class WarehouseAgentApi extends AgentApi {
  conversation = "";
  shareToken = "";
  private pending: AbortController | null = null;
  constructor(public warehouse: WarehouseClient) {
    super("", "/warehouse-api/v1");
  }
  override headers(json = false) {
    return this.warehouse.headers(
      json ? { "Content-Type": "application/json" } : {},
    );
  }
  override async fetchMemory() {
    return [];
  }
  override async fetchOrders() {
    return null;
  }
  override async fetchCart<T>(): Promise<T | null> {
    return null;
  }
  dispose() {
    this.pending?.abort();
  }
  override async *chatStream(text: string): AsyncGenerator<AgentEvent> {
    const cancel = new AbortController();
    this.pending = cancel;
    let complete = false,
      refused = false;
    try {
      if (!this.conversation) {
        const response = await this.warehouse.response("/conversations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: "advisor" }),
          signal: cancel.signal,
        });
        this.conversation = (await response.json()).id;
      }
      const response = await this.warehouse.response(
        `/conversations/${this.conversation}/chat`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: JSON.stringify({ message: text }),
          signal: cancel.signal,
        },
      );
      if (!response.body) throw new Error("助手没有返回内容。");
      for await (const event of warehouseEvents(response.body)) {
        if (cancel.signal.aborted) return;
        if (event.type === "ui" && event.data.component === "warehouse_share") {
          this.shareToken = String(event.data.payload?.token ?? "");
        }
        complete ||= event.type === "turn_complete";
        refused ||= event.type === "error";
        // Partial presentation arguments have not yet been enriched by the server.
        if (event.type !== "ui_partial") yield event as AgentEvent;
      }
      if (!complete && !refused)
        throw new Error("连接暂时中断，服务端可能仍在处理。恢复连接后将读取会话状态。");
    } catch (error) {
      if (!cancel.signal.aborted)
        yield {
          type: "error",
          data: { message: message(error) },
        } as AgentEvent;
    } finally {
      if (this.pending === cancel) this.pending = null;
    }
  }
}

export const inputStyle =
  "w-full rounded-xl border border-(--line-strong) bg-(--card) px-3 py-2 text-sm focus:outline-2 focus:outline-(--accent)";
export const cardStyle =
  "rounded-2xl border border-(--line) bg-(--card) p-4 shadow-(--shadow-sm)";
export function amount(value: string | null, currency: string | null) {
  if (value == null || !currency || currency === "XXX") return "待核实";
  return `${currency} ${value}`;
}
export function stamp(value: string | null) {
  if (!value) return "上游未提供";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export type BriefField = {
  value: any;
  source: "said" | "inferred" | "advisor" | null;
  evidence: string;
  hint: string;
};
export type Rooms = {
  doubles: number;
  twins: number;
  singles: number;
  child_bed: boolean | null;
  raw: string;
};
export type BriefEnvelope = {
  version: number;
  title: string;
  stage: "need" | "select" | "departure" | "quote" | "shared";
  allowed_actions: string[];
  quote_stale: boolean;
  readiness: Record<
    "search" | "quote",
    { ready: boolean; missing: string[]; inferred: string[] }
  >;
  body: Record<string, any> & {
    route_id: string | null;
    departure_id: string | null;
    quote_id: string | null;
    quote: Quote | null;
    quote_fields_version: number;
    quote_brief_version: number | null;
    share_token: string | null;
    offline_status: string;
    offline_hold_note: string;
  };
};
export type WorkbenchAction = {
  action: string;
  product_id?: string;
  offer_id?: string;
  after?: string;
  query?: string;
  filters?: Record<string, string>;
  note?: string;
};
