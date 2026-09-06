// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * What /showcase renders from, with no API running. Each `const X: Product` literal is a record
 * one session read: the three routes are what `search_products` returned for destination 伊犁 in
 * the window 2026-10-11 到 2026-10-20, 8 到 10 天, 2 大 2 小 (儿童 5 岁与 9 岁), 不含购物店; the
 * three 团期 are the variants `get_product_details("RT-1022")` returned in that same session. A
 * route's price is its 起价 in that window and a 团期's quote is made for that party, so both are
 * a snapshot of that one read, and every 报价 below repeats it. `api/tests/test_showcase.py`
 * replays the read and holds these literals to it, on a backend pinned to `data/routes.json`'s
 * `dates_anchored_to` (2026-09-06): `data/departures.json` shifts its dates by whole weeks from
 * that day, which moves the departure ids with it.
 */

import type { CartPayload, CheckoutPayload, ComparisonPayload, GuidePayload, OrderStatusPayload, PlanPayload, Product, ProductsPayload } from "./types";

// --- Routes (线路), as search returns them ---

const XINJIANG_8: Product = {
  product_id: "RT-1021",
  title: "伊犁北疆环线 8 日纯玩小团",
  brand: "ACME 新疆地接",
  price: 5780.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["纯玩", "小团", "亲子", "轻徒步"],
  attributes: {
    destination: "伊犁",
    region: "新疆",
    departure_city: "乌鲁木齐",
    days: "8",
    nights: "7",
    hotel_level: "四钻",
    vehicle: "8座商务车",
    group_size_max: "8",
    includes_transport: "no",
    shopping_stops: "0",
    optional_paid_items: "1",
    child_min_age: "3",
    child_policy: "2-12岁不占床儿童价，含车位、导服与正常餐；占床加收房差",
    intensity: "3",
    max_drive_hours_per_day: "4.5",
    highlights: "赛里木湖|那拉提草原|喀拉峻|薰衣草田|果子沟大桥",
    fit_tags: "纯玩|小团|亲子|轻徒步",
    match: "exact"
  },
  in_stock: true,
  short_description: "8 座车不超 8 人，全程零购物，赛里木湖与那拉提各住一晚，适合带孩子的家庭。",
  options: { depart_date: ["2026-10-13", "2026-10-17"] }
};

const KALAJUN_10: Product = {
  product_id: "RT-1022",
  title: "伊犁·喀拉峻草原深度 10 日",
  brand: "ACME 新疆地接",
  price: 7880.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["深度游", "摄影", "纯玩", "小团"],
  attributes: {
    destination: "伊犁",
    region: "新疆",
    departure_city: "乌鲁木齐",
    days: "10",
    nights: "9",
    hotel_level: "四钻",
    vehicle: "6座商务车",
    group_size_max: "6",
    includes_transport: "no",
    shopping_stops: "0",
    optional_paid_items: "2",
    child_min_age: "2",
    child_policy: "2-12岁不占床儿童价；儿童全程与家长同车同导",
    intensity: "4",
    max_drive_hours_per_day: "5",
    highlights: "喀拉峻|琼库什台|夏塔古道|唐布拉百里画廊|独库公路",
    fit_tags: "深度游|摄影|纯玩|小团",
    match: "exact"
  },
  in_stock: true,
  short_description: "6 人小车走透伊犁，喀拉峻两晚、琼库什台一晚，早晚光线全留给拍照。",
  options: { depart_date: ["2026-10-11", "2026-10-14"] }
};

const LUXURY_8: Product = {
  product_id: "RT-1024",
  title: "伊犁五钻轻奢 8 日私享小团",
  brand: "ACME 伊犁精品",
  price: 9680.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["高端", "纯玩", "小团", "老人友好"],
  attributes: {
    destination: "伊犁",
    region: "新疆",
    departure_city: "乌鲁木齐",
    days: "8",
    nights: "7",
    hotel_level: "五钻",
    vehicle: "6座商务车",
    group_size_max: "6",
    includes_transport: "no",
    shopping_stops: "0",
    optional_paid_items: "1",
    child_min_age: "3",
    child_policy: "3-12岁不占床儿童价；五钻酒店多为大床房，占床需补房差",
    intensity: "2",
    max_drive_hours_per_day: "4",
    highlights: "赛里木湖|那拉提草原|喀拉峻|伊宁六星街",
    fit_tags: "高端|纯玩|小团|老人友好",
    match: "exact"
  },
  in_stock: true,
  short_description: "全程五钻酒店与 6 座商务车，每日车程控制在 4 小时内，含双导服务。",
  options: { depart_date: ["2026-10-13", "2026-10-18"] }
};

// --- Departures (团期) of RT-1022, as get_product_details returns them ---

const DEPARTURE_1007: Product = {
  product_id: "DP-1022-20261007",
  title: "伊犁·喀拉峻草原深度 10 日 10/7 出发",
  brand: "ACME 新疆地接",
  price: 7880.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    depart_date: "2026-10-07",
    return_date: "2026-10-16",
    seats_left: "4",
    seats_total: "6",
    group_status: "confirmed",
    booking_deadline: "2026-10-02",
    adult_price: "7880",
    child_price: "4980",
    single_supplement: "1900",
    party_quote_total: "25720",
    quote_party: "2大2小",
    hold_ttl_minutes: "30"
  },
  in_stock: true,
  short_description: "余位 4/6，已成团，2大2小合计 25720 元",
  option_values: { depart_date: "2026-10-07" },
  variant_of: "RT-1022"
};

const DEPARTURE_1014: Product = {
  product_id: "DP-1022-20261014",
  title: "伊犁·喀拉峻草原深度 10 日 10/14 出发",
  brand: "ACME 新疆地接",
  price: 7880.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    depart_date: "2026-10-14",
    return_date: "2026-10-23",
    seats_left: "5",
    seats_total: "6",
    group_status: "confirmed",
    booking_deadline: "2026-10-09",
    adult_price: "7880",
    child_price: "4980",
    single_supplement: "1900",
    party_quote_total: "25720",
    quote_party: "2大2小",
    hold_ttl_minutes: "30"
  },
  in_stock: true,
  short_description: "余位 5/6，已成团，2大2小合计 25720 元",
  option_values: { depart_date: "2026-10-14" },
  variant_of: "RT-1022"
};

const DEPARTURE_1021: Product = {
  product_id: "DP-1022-20261021",
  title: "伊犁·喀拉峻草原深度 10 日 10/21 出发",
  brand: "ACME 新疆地接",
  price: 7580.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    depart_date: "2026-10-21",
    return_date: "2026-10-30",
    seats_left: "5",
    seats_total: "6",
    group_status: "confirmed",
    booking_deadline: "2026-10-16",
    adult_price: "7580",
    child_price: "4980",
    single_supplement: "1900",
    party_quote_total: "25120",
    quote_party: "2大2小",
    hold_ttl_minutes: "30"
  },
  in_stock: true,
  short_description: "余位 5/6，已成团，2大2小合计 25120 元",
  option_values: { depart_date: "2026-10-21" },
  variant_of: "RT-1022"
};

const ROUTES = [XINJIANG_8, KALAJUN_10, LUXURY_8];
const DEPARTURES = [DEPARTURE_1007, DEPARTURE_1014, DEPARTURE_1021];

export const SHOWCASE_PRODUCT_INDEX: Record<string, Product> = Object.fromEntries(
  [...ROUTES, ...DEPARTURES].map((product) => [product.product_id, product]),
);

const products: ProductsPayload = {
  title: "10/11–10/20 出发的伊犁纯玩线路",
  layout: "carousel",
  items: [
    { product: XINJIANG_8, reason: "8 座车最多 8 人，两个孩子有座有床，起价也最低。" },
    { product: KALAJUN_10, reason: "客人要的 10 天在这条线上，喀拉峻住两晚，拍照时间最宽裕。" },
    { product: LUXURY_8, reason: "同样 8 天但住五钻、每天车程 4 小时以内，长辈同行时报这条。" },
  ],
};

const departures: ProductsPayload = {
  title: "喀拉峻深度 10 日 · 10 月团期",
  layout: "list",
  items: [
    { product: DEPARTURE_1007, reason: "国庆后第一班，余位 4 个，2 大 2 小刚好占满。" },
    { product: DEPARTURE_1014, reason: "客人问的就是这一班，余位 5 个，四个人上完还剩一个。" },
    { product: DEPARTURE_1021, reason: "晚一周，成人价便宜 300，客人时间能挪就报这班。" },
  ],
};

const comparison: ComparisonPayload = {
  title: "三条伊犁线的差别在哪儿",
  entries: [
    {
      product_id: "RT-1021",
      product: XINJIANG_8,
      pros: ["起价最低", "8 座车，两个孩子都有座"],
      cons: ["喀拉峻只留一天"],
      best_for: "预算优先的家庭客",
    },
    {
      product_id: "RT-1022",
      product: KALAJUN_10,
      pros: ["喀拉峻两晚", "6 人小车"],
      cons: ["强度 4，每天最长车程 5 小时"],
      best_for: "时间够、想拍照的客人",
    },
    {
      product_id: "RT-1024",
      product: LUXURY_8,
      pros: ["全程五钻", "每天车程 4 小时以内"],
      cons: ["价格高出近四千"],
      best_for: "带长辈、住宿要求高的客人",
    },
  ],
  dimensions: ["天数", "住宿标准", "车型", "每日车程"],
  recommended_product_id: "RT-1022",
  price_delta: {
    amount: 3900,
    low_product_id: "RT-1021",
    low_price: 5780,
    high_product_id: "RT-1024",
    high_price: 9680,
  },
};

const plan: PlanPayload = {
  title: "这单接下来怎么走",
  intro: "客人是 2 大 2 小，孩子 5 岁和 9 岁，10/11–10/20 出发，8 到 10 天，不要购物店。",
  steps: [
    { label: "报三个价位", detail: "先把 8 日、10 日、五钻三条线的起价和住宿标准讲清楚。", products: ROUTES },
    { label: "开客人选中那条线的团期", detail: "按 2 大 2 小报总价，说明余位和报名截止。", products: [DEPARTURE_1014] },
    { label: "口头确认后占位", detail: "占位 30 分钟，到期自动释放，之后再收定金。", products: [] },
  ],
};

const guide: GuidePayload = {
  title: "退改与成团规则",
  sections: [
    {
      heading: "不成团怎么办",
      body: "团期显示待成团时，人数不够会在报名截止后并团或改期；客人不接受调整的，全额退还已收款项。",
    },
    {
      heading: "退团扣费",
      body: "出发前 15 日以上退团不扣费；14 至 8 日扣团款 20%；7 至 4 日扣 50%；3 日以内扣 80%，机票与已开票的景区门票按实际损失另计。",
    },
    {
      heading: "儿童价",
      body: "2 至 12 岁不占床按儿童价，含车位、导服与正常餐；占床按各线路的房差补收，五钻线路多为大床房，占床需另补。",
    },
  ],
  sources: ["退改政策", "成团规则", "儿童价规则"],
};

const cart: CartPayload = {
  items: [
    {
      product_id: "DP-1022-20261014",
      title: "伊犁·喀拉峻草原深度 10 日 10/14 出发",
      // The ERP quotes 2 大 2 小 as 25,720 元; the line splits it evenly across the four heads.
      price: 6430.0,
      quantity: 4,
      option_values: { depart_date: "2026-10-14" },
      variant_of: "RT-1022",
      line_total: 25720.0,
    },
    {
      product_id: "DP-1022-20261021",
      title: "伊犁·喀拉峻草原深度 10 日 10/21 出发",
      price: 6280.0,
      quantity: 4,
      option_values: { depart_date: "2026-10-21" },
      variant_of: "RT-1022",
      line_total: 25120.0,
    },
  ],
  item_count: 8,
  subtotal: 50840.0,
  currency: "CNY",
  holds: [
    {
      hold_id: "HD-4F21A0",
      product_id: "DP-1022-20261014",
      expires_at: "2026-10-06T09:42:00+00:00",
      seconds_remaining: 1524,
    },
    {
      hold_id: "HD-9C07B3",
      product_id: "DP-1022-20261021",
      expires_at: "2026-10-06T09:20:00+00:00",
      seconds_remaining: 47,
    },
  ],
};

const checkout: CheckoutPayload = {
  note: "两个团期都占着位，10/21 那班还有 47 秒到期；客人定下来哪一班，另一班就释放掉。",
  cart,
};

const order_status: OrderStatusPayload = {
  order_id: "TR-20261006-0031",
  summary: "10/14 出发的喀拉峻深度 10 日已确认成团，2 大 2 小共 4 个位置。",
  next_step: "出发前 7 天把行前通知和集合地点发给客人。",
  order: {
    order_id: "TR-20261006-0031",
    status: "shipped",
    placed_at: "2026-10-06",
    items: [
      {
        product_id: "DP-1022-20261014",
        title: "伊犁·喀拉峻草原深度 10 日 10/14 出发",
        quantity: 4,
        price: 6430.0,
      },
    ],
    total: 25720.0,
    currency: "CNY",
    estimated_delivery: "2026-10-14",
  },
};

export const SHOWCASE = {
  products,
  departures,
  comparison,
  plan,
  guide,
  checkout,
  order_status,
  cart,
};
