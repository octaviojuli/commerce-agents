// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** Mirrors shopping_agent/types.py and tools/presentation.py; the cart's holds are tour's api/. */

export interface Product {
  product_id: string;
  title: string;
  brand?: string | null;
  price: number;
  currency?: string;
  rating?: number | null;
  review_count?: number | null;
  image_url?: string | null;
  category?: string | null;
  labels?: string[];
  /** Every tour attribute is a string; a list is "|"-joined (tags, features). */
  attributes?: Record<string, string>;
  in_stock?: boolean;
  short_description?: string | null;
  /** A route's departure dates; the cart takes one of its 团期 variants. */
  options?: Record<string, string[]>;
  /** A 团期's own depart_date. */
  option_values?: Record<string, string>;
  /** The route a 团期 belongs to. */
  variant_of?: string | null;
}

export interface CartItem {
  product_id: string;
  title: string;
  price: number;
  quantity: number;
  image_url?: string | null;
  option_values?: Record<string, string>;
  variant_of?: string | null;
  line_total: number;
}

/** One live 占位, as api/main.py `holds_payload` stamps it onto every cart read. */
export interface Hold {
  /** The 订单号 of the 预留 order the ERP wrote. */
  hold_id: string;
  /** The departure id the seats are held on. */
  product_id: string;
  expires_at: string;
  seconds_remaining: number;
}

export interface CartPayload {
  items: CartItem[];
  /** Sum of quantities: the whole party across every held 团期. */
  item_count: number;
  subtotal: number;
  currency: string;
  holds?: Hold[];
}

// --- Presentation payloads, as streamed after server enrichment ---

export interface ProductsPayload {
  title?: string;
  layout?: "carousel" | "grid" | "list";
  items: { product: Product; reason?: string | null }[];
}

export interface ComparisonPayload {
  title?: string;
  entries: {
    product_id: string;
    product: Product;
    pros?: string[];
    cons?: string[];
    best_for?: string | null;
  }[];
  dimensions?: string[];
  recommended_product_id?: string | null;
  // Stamped by the server: the spread between the cheapest and dearest compared routes.
  price_delta?: {
    amount: number;
    low_product_id: string;
    low_price: number;
    high_product_id: string;
    high_price: number;
  };
}

export interface PlanPayload {
  title: string;
  intro?: string;
  steps: { label: string; detail?: string | null; products: Product[] }[];
}

export interface GuidePayload {
  title: string;
  sections: { heading: string; body: string }[];
  related_products?: Product[];
  sources?: string[];
}

export interface OrderStatusPayload {
  order_id: string;
  summary: string;
  next_step?: string;
  order?: {
    order_id: string;
    status: string;
    placed_at: string;
    items: { product_id: string; title: string; quantity: number; price: number }[];
    total: number;
    currency?: string;
    estimated_delivery?: string;
    tracking_url?: string;
  };
}

/** `checkout`: the cart staged as a 报价单. Nothing here charges; the deposit is taken in store. */
export interface CheckoutPayload {
  note?: string;
  cart: CartPayload;
}

/**
 * `present_shortlist`: the 团期 the advisor sends the customer, each with the 线路 it departs
 * from. `share_url` is minted when the call finishes, so a streaming partial has none yet.
 */
export interface ShortlistPayload {
  title: string;
  note?: string;
  items: { departure: Product; route: Product }[];
  share_url?: string;
}

/**
 * `present_focus`: one narrowing question over a search that matched more 线路 than a shortlist
 * shows. `dimension` names the group whose values are the chips; a value with an `ask` sends
 * those words when tapped, one without is a count alone. `anchors` are up to three results the
 * card carries as a foothold when the match is not huge.
 */
export interface FocusPayload {
  question: string;
  total: number;
  shown: number;
  dimension: string;
  groups: { label: string; filter: string; values: { value: string; count: number; ask?: string }[] }[];
  anchors: Product[];
}

/**
 * One leg of a 参考航班: the 去程 at the head of a 逐日行程, the 回程 at its tail, or a flight the
 * document wrote inside a day. Everything but the day is free text as the 线路文档 states it.
 */
export interface RouteFlight {
  day?: number;
  flight_no?: string;
  carrier?: string;
  from_place?: string;
  to_place?: string;
  times?: string;
}

/** One meal of a day: what the document says, and whether it is included; null is 未写明. */
export interface RouteMeal {
  text?: string;
  included?: boolean | null;
}

/** What the 线路文档 makes of a stop: a visit, a facade, a shop, a paid extra, a gift, free time. */
export type SightKind = "景点" | "外观" | "购物" | "自费" | "赠送" | "自由活动";

/** One stop of a day; `ticket_included` is null where the document does not say. */
export interface RouteSight {
  name: string;
  kind?: SightKind;
  duration?: string;
  ticket_included?: boolean | null;
}

/** Where a day sleeps: a hotel, a ship, the plane, or home on the last day. */
export type Overnight = "hotel" | "ship" | "flight" | "home" | "unknown";

/** One day of a 线路, as the reviewed document writes it. */
export interface RouteDay {
  day: number;
  title?: string;
  places?: string[];
  /** How the day moves, where the document states it: 飞机, 大巴, 内陆航班, 高铁. */
  transport?: string;
  overnight?: Overnight;
  hotel?: { name: string; grade?: string; or_similar?: boolean } | null;
  meals?: { breakfast?: RouteMeal; lunch?: RouteMeal; dinner?: RouteMeal };
  flights?: RouteFlight[];
  sights?: RouteSight[];
  text?: string;
}

/**
 * `present_route_days`: one 线路 as its 逐日行程 — every day of the reviewed 线路文档 expanded,
 * the 去程 flights at the head and the 回程 at the tail, and what the document states around
 * them: 费用包含 and 费用不含, the 购物店 and 自费项目 it lists, and its policies. A streaming
 * partial carries the head and the days written so far, so everything but the id is optional.
 *
 * `days` is the day list; the 天数 is `day_count`, and the list's own length stands in for it
 * until the payload states one.
 */
export interface RouteDaysPayload {
  route_id: string;
  title?: string;
  /** The agency's own poster for the line; the card draws its own wash without one. */
  image_url?: string | null;
  route_code?: string;
  department?: string;
  day_count?: number;
  nights?: number;
  depart_city?: string;
  countries?: string[];
  /** True once an editor has passed the parsed document; `reviewed_by` is who did. */
  reviewed?: boolean;
  reviewed_by?: string;
  highlights?: string[];
  airline?: string;
  hotel_standard?: string;
  meal_standard?: string;
  outbound?: RouteFlight[];
  inbound?: RouteFlight[];
  days: RouteDay[];
  inclusions?: string[];
  exclusions?: string[];
  shopping?: { name: string; day?: number; duration?: string }[];
  optional?: { name: string; price?: string; day?: number }[];
  policies?: {
    single_room?: string;
    child?: string;
    visa?: string;
    cancellation?: string;
    deposit?: string;
  };
  attachment_name?: string;
}

/** What a 团期 is open for, in the words the 团期 card and the route card's pills show. */
export type DepartureStatus = "可报名" | "已成团" | "满员" | "截止";

/**
 * `present_departures`: the sellable 团期 of one 线路 inside the window the advisor asked about.
 * Each row is the 团期 record itself with the date read off it, so a tap can open that 团期 by
 * its own id; `price_adult` is the 同业价 for one adult, and null where the ERP published none.
 */
export interface DeparturesPayload {
  route: Product;
  window?: { from: string; to: string };
  items: {
    departure: Product;
    date: string;
    weekday?: string;
    status: DepartureStatus;
    price_adult?: number | null;
  }[];
}

/**
 * `present_attachments`: the 行程附件 of the 线路 the advisor asked for, each the file as the
 * agency named it; the bytes come from `GET /api/attachments/{product_id}` on a tap.
 */
export interface AttachmentsPayload {
  note?: string;
  items: { product_id: string; title: string; name: string; extension: string }[];
}

/**
 * One 历史会话 as `GET /api/sessions` lists it, newest first: the conversation's id, the title
 * the API made for it, when it was last spoken in, and how many messages it holds. `current` is
 * the session the request itself was made in.
 */
export interface SessionSummary {
  session_id: string;
  title: string;
  updated_at: string;
  message_count: number;
  current: boolean;
}

/** One message of a session's transcript, as `GET /api/sessions/{id}/messages` returns it. */
export interface SessionMessage {
  role: "user" | "assistant";
  text: string;
}

/**
 * One day of a 定制方案. A finished day carries how it stands against the version it was made
 * from — `change` is null on a plan made straight off the baseline — and `request` marks a day
 * the customer asked for that only the 计调 can price. A streaming partial carries the label and
 * the note and nothing else, so everything but the label is optional.
 */
export interface ItineraryDay {
  label: string;
  note?: string;
  /** The day is what the customer asked for, not what the baseline itinerary runs. */
  request?: boolean;
  change?: "same" | "changed" | "added" | null;
  changed_fields?: ("label" | "note")[];
}

/**
 * `itinerary`: one version of the 定制方案 the advisor is building on a 线路. The final frame
 * carries the version's own facts — which plan it is, which version it was made from, the
 * baseline 线路 and the 团期 the advisor opened, what changed, and the customer's link; a
 * streaming partial carries only the title, the dates, the party and the days so far, so
 * everything else is optional and the card draws what has landed.
 */
export interface ItineraryPayload {
  plan_id?: string;
  version?: number;
  /** The version this one was made from; null on a plan made straight off the baseline. */
  parent_version?: number | null;
  title?: string;
  /** "2026-10-14 至 2026-10-23", or whatever free text the dates were written as. */
  travel_dates?: string;
  party?: string;
  /** The baseline 线路 this plan changes. */
  route?: Product;
  /** The 团期 the reference price is read off, when the advisor opened one. */
  departure?: Product;
  days: ItineraryDay[];
  /** Days of the parent version that are gone; `index` is where they sat in `days`. */
  removed_days?: { index: number; label: string; note: string }[];
  /** "+1 天（第 5 天）；改 1 天（第 6 天）" — what this version did to the one before it. */
  summary?: string;
  /**
   * The baseline 团期's own prices, which a 定制 plan only refers to: the 同业价 and the 市场价
   * for one adult, which of the two the ERP quoted at, and this party's total. The plan's own
   * price is the 计调's to make, so nothing here is the customer's price for it.
   */
  reference_price?: {
    tong_ye_adult: number | null;
    market_adult: number | null;
    quote_source: string;
    party_total: number | null;
  } | null;
  /** The 线路 the agency built for this plan in the ERP; null until it has built one. */
  erp_route_id?: string | null;
  /** The plan as plain text for the 计调, which the advisor copies out of the card. */
  handoff_text?: string;
  /** The customer's link, minted on the finished call; a partial has none. */
  share_url?: string;
}

/**
 * One version of a 定制方案 as `GET /api/share/plan/{token}` hands it to the customer. It is the
 * customer's own read: the advisor who made it, the baseline in words rather than as a record,
 * and the 市场价 alone — the 同业价 is the agency's and does not leave the workbench.
 */
export interface SharedPlan {
  plan_id: string;
  version: number;
  title: string;
  travel_dates?: string;
  party?: string;
  advisor_name: string;
  created_at: string;
  route: { title: string; days?: string; depart_city?: string };
  days: { label: string; note: string; request: boolean }[];
  market_adult: number | null;
}
