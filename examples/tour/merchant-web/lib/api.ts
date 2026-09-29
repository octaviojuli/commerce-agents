export {
  ApiError,
  WarehouseClient,
  message,
  type Organization,
} from "web-shared/warehouse-client";
export type Connection = {
  id: string;
  name: string;
  connector_type: string;
  active: boolean;
  capabilities: Record<string, unknown>;
};
export type Partner = {
  id: string;
  buyer_org_id: string;
  buyer_name: string;
  connection_id: string;
  active: boolean;
  valid: boolean;
};
export type Listing = {
  listing_id: string;
  title: string;
  status: string;
  price: number | null;
  currency: string;
  stock: number | null;
  attributes: Record<string, string>;
  variants?: Listing[];
  long_description?: string;
  option_values?: Record<string, string>;
};
export type Change = {
  id: string;
  kind: string;
  status: string;
  payload_hash: string;
  payload: Record<string, any>;
  created_at: string;
  result?: Record<string, unknown>;
  target_label?: string;
  buyer_name?: string;
  buyer_names?: Record<string, string>;
  target_names?: Record<string, string>;
  document_source?: {
    asset_id: string; file_name: string; file_hash: string;
    product_version: number; parse_id: string; parser_version: string;
  };
  reversed_movement?: {
    business_key: string;
    delta_total: number;
    delta_sold: number;
    delta_blocked: number;
  };
};
export type Pool = {
  id: string;
  departure_id: string;
  version: number;
  total: number;
  sold: number;
  blocked: number;
  held: number;
  available: number;
  code: string;
  depart_date: string;
  product_name: string;
};
export function money(value: number | null | undefined, currency = "CNY") {
  return value == null
    ? "待询价"
    : currency === "XXX"
      ? "币种待确认"
      : new Intl.NumberFormat("zh-CN", { style: "currency", currency }).format(
          value,
        );
}
export function time(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
export const statuses: Record<string, string> = {
  staged: "待审批",
  applied: "已应用",
  discarded: "已作废",
  published: "已发布",
  running: "同步中",
  failed: "同步失败",
  active: "已上架",
  paused: "已暂停",
  draft: "草稿",
  out_of_stock: "已售罄",
  validated: "校验通过",
  invalid: "校验未通过",
};
