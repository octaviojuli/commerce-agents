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
  /** Every tour attribute is a string; a list is "|"-joined (highlights, fit_tags). */
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
