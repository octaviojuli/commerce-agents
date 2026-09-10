// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * What /showcase renders from, with no API running. Each `const X: Product` literal is a record
 * one session read: the three routes are what `search_products` returned for destination 伊犁 in
 * the window 2026-10-11 到 2026-10-20, 8 到 10 天, 2 大 2 小 (儿童 5 岁与 9 岁), 不含购物店; the
 * three 团期 are variants `get_product_details("RT-1022")` returned in that same session. The
 * keys are the ERP's own (`docs/erp-contract.md`): a 线路 carries its 线路编号, 天数, 出发城市, the
 * tags and features its editors wrote and the attributes `api/tags.py` normalised the ERP's
 * itinerary tags into (`destination`, `shopping`, `hotel_grade`, `family`, `departure_cities`,
 * `inclusions`, `budget`), and a 团期 carries its 团号, 余位, 成团人数, the 同业价
 * this party was quoted at (`adult_price`, `party_quote_total`, under the ERP's own
 * `price_type`) and the 市场价 beside it (`market_adult_price`), which is the customer's own
 * price. A route's 起价 is the cheapest 同业价
 * in that window and a 团期's 报价 is made for that party, so both are a snapshot of that one read.
 * `api/tests/test_showcase.py` replays it and holds these literals to it, on a backend pinned to
 * `data/routes.json`'s `dates_anchored_to` (2026-09-06): `data/departures.json` shifts its dates
 * by whole weeks from that day, while `periodId`, and so the `DP-` id, stays put.
 */

import type {
  CartPayload,
  CheckoutPayload,
  ComparisonPayload,
  FocusPayload,
  GuidePayload,
  OrderStatusPayload,
  PlanPayload,
  Product,
  ProductsPayload,
  ShortlistPayload,
} from "./types";

// --- Routes (线路), as search returns them ---

const XINJIANG_8: Product = {
  product_id: "RT-1021",
  title: "伊犁北疆环线 8 日纯玩小团",
  brand: "ACME 旅行社 新疆部",
  price: 5780.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["四钻酒店", "纯玩无购物", "亲子", "乌鲁木齐出发"],
  attributes: {
    route_code: "YLBJ",
    days: "8",
    depart_city: "乌鲁木齐",
    company: "ACME 旅行社 新疆部",
    destination: "伊犁",
    shopping: "none",
    hotel_grade: "四钻",
    family: "yes",
    departure_cities: "乌鲁木齐",
    inclusions: "含景点首道门票|含导游服务费",
    budget: "预算约5800—6600元",
    region: "伊犁",
    tags: "纯玩|小团|亲子|轻徒步|零购物|四钻|8座商务车|伊犁|乌鲁木齐出发|赛里木湖|那拉提草原|喀拉峻",
    features: "赛里木湖|那拉提草原|喀拉峻|薰衣草田|果子沟大桥|8 座车不超 8 人，全程零购物，赛里木湖与那拉提各住一晚，适合带孩子的家庭。",
    match: "exact",
    catalog_matches: "3"
  },
  in_stock: true,
  short_description: "8 座车不超 8 人，全程零购物，赛里木湖与那拉提各住一晚，适合带孩子的家庭。",
  options: {
    depart_date: ["2026-10-13", "2026-10-17"]
  }
};

const KALAJUN_10: Product = {
  product_id: "RT-1022",
  title: "伊犁·喀拉峻草原深度 10 日",
  brand: "ACME 旅行社 新疆部",
  price: 7880.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["四钻酒店", "纯玩无购物", "乌鲁木齐出发", "含景点首道门票"],
  attributes: {
    route_code: "YLKL",
    days: "10",
    depart_city: "乌鲁木齐",
    company: "ACME 旅行社 新疆部",
    destination: "伊犁",
    shopping: "none",
    hotel_grade: "四钻",
    family: "no",
    departure_cities: "乌鲁木齐",
    inclusions: "含景点首道门票|含摄影向导",
    budget: "",
    region: "伊犁",
    tags: "深度游|摄影|纯玩|小团|零购物|四钻|6座商务车|伊犁|乌鲁木齐出发|喀拉峻|琼库什台|夏塔古道",
    features: "喀拉峻|琼库什台|夏塔古道|唐布拉百里画廊|独库公路|6 人小车走透伊犁，喀拉峻两晚、琼库什台一晚，早晚光线全留给拍照。",
    match: "exact",
    catalog_matches: "3"
  },
  in_stock: true,
  short_description: "6 人小车走透伊犁，喀拉峻两晚、琼库什台一晚，早晚光线全留给拍照。",
  options: {
    depart_date: ["2026-10-11", "2026-10-14"]
  }
};

const LUXURY_8: Product = {
  product_id: "RT-1024",
  title: "伊犁五钻轻奢 8 日私享小团",
  brand: "ACME 旅行社 新疆部",
  price: 9680.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["五钻酒店", "纯玩无购物", "乌鲁木齐出发", "含景点首道门票"],
  attributes: {
    route_code: "YLQS",
    days: "8",
    depart_city: "乌鲁木齐",
    company: "ACME 旅行社 新疆部",
    destination: "伊犁",
    shopping: "none",
    hotel_grade: "五钻",
    family: "no",
    departure_cities: "乌鲁木齐",
    inclusions: "含景点首道门票|含导游服务费|含双导服务",
    budget: "预算约9800—1.1万元",
    region: "伊犁",
    tags: "高端|纯玩|小团|老人友好|零购物|五钻|6座商务车|伊犁|乌鲁木齐出发|赛里木湖|那拉提草原|喀拉峻",
    features: "赛里木湖|那拉提草原|喀拉峻|伊宁六星街|全程五钻酒店与 6 座商务车，每日车程控制在 4 小时内，含双导服务。",
    match: "exact",
    catalog_matches: "3"
  },
  in_stock: true,
  short_description: "全程五钻酒店与 6 座商务车，每日车程控制在 4 小时内，含双导服务。",
  options: {
    depart_date: ["2026-10-13", "2026-10-18"]
  }
};

// --- Departures (团期) of RT-1022, as get_product_details returns them ---

const DEPARTURE_1007: Product = {
  product_id: "DP-3015",
  title: "伊犁·喀拉峻草原深度 10 日 10/7 出发",
  brand: "ACME 旅行社 新疆部",
  price: 7880.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    period_code: "XJ-YLKL-20261007-001",
    depart_date: "2026-10-07",
    return_date: "2026-10-16",
    seats_left: "4",
    seats_total: "6",
    min_group_size: "2",
    confirm_count: "2",
    group_status: "confirmed",
    reserve_hours: "24",
    adult_price: "7880",
    child_price: "4980",
    elder_price: "7880",
    single_room_diff: "1900",
    market_adult_price: "7880",
    market_child_price: "4980",
    party_quote_total: "25720",
    quote_party: "2大2小",
    quote_source: "customer",
    price_type: "同行价"
  },
  in_stock: true,
  short_description: "余位 4/6，已成团，同业价 2大2小合计 25720 元（市场价成人 7880 元）",
  option_values: { depart_date: "2026-10-07" },
  variant_of: "RT-1022"
};

const DEPARTURE_1014: Product = {
  product_id: "DP-3017",
  title: "伊犁·喀拉峻草原深度 10 日 10/14 出发",
  brand: "ACME 旅行社 新疆部",
  price: 7880.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    period_code: "XJ-YLKL-20261014-001",
    depart_date: "2026-10-14",
    return_date: "2026-10-23",
    seats_left: "5",
    seats_total: "6",
    min_group_size: "2",
    confirm_count: "1",
    group_status: "pending",
    reserve_hours: "24",
    adult_price: "7880",
    child_price: "4980",
    elder_price: "7880",
    single_room_diff: "1900",
    market_adult_price: "7880",
    market_child_price: "4980",
    party_quote_total: "25720",
    quote_party: "2大2小",
    quote_source: "customer",
    price_type: "同行价"
  },
  in_stock: true,
  short_description: "余位 5/6，待成团，同业价 2大2小合计 25720 元（市场价成人 7880 元）",
  option_values: { depart_date: "2026-10-14" },
  variant_of: "RT-1022"
};

const DEPARTURE_1021: Product = {
  product_id: "DP-3018",
  title: "伊犁·喀拉峻草原深度 10 日 10/21 出发",
  brand: "ACME 旅行社 新疆部",
  price: 7580.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    period_code: "XJ-YLKL-20261021-001",
    depart_date: "2026-10-21",
    return_date: "2026-10-30",
    seats_left: "5",
    seats_total: "6",
    min_group_size: "2",
    confirm_count: "1",
    group_status: "pending",
    reserve_hours: "24",
    adult_price: "7580",
    child_price: "4980",
    elder_price: "7580",
    single_room_diff: "1900",
    market_adult_price: "7580",
    market_child_price: "4980",
    party_quote_total: "25120",
    quote_party: "2大2小",
    quote_source: "customer",
    price_type: "同行价"
  },
  in_stock: true,
  short_description: "余位 5/6，待成团，同业价 2大2小合计 25120 元（市场价成人 7580 元）",
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
    { product: XINJIANG_8, reason: "8 座车不超 8 人，两个孩子都有座，起价也最低。" },
    { product: KALAJUN_10, reason: "客人要的 10 天在这条线上，喀拉峻住两晚，拍照时间最宽裕。" },
    { product: LUXURY_8, reason: "同样 8 天，标了五钻和老人友好，长辈同行时报这条。" },
  ],
};

const departures: ProductsPayload = {
  title: "喀拉峻深度 10 日 · 10 月团期",
  layout: "list",
  items: [
    { product: DEPARTURE_1007, reason: "国庆后第一班，已成团，余位 4 个，2 大 2 小刚好占满。" },
    { product: DEPARTURE_1014, reason: "客人问的就是这一班，差 1 个人成团，这单上去就够。" },
    { product: DEPARTURE_1021, reason: "晚一周，成人价便宜 300，客人时间能挪就报这班。" },
  ],
};

const shortlist: ShortlistPayload = {
  title: "10 月两个可选团期",
  note: "两个团都还能坐下 2 大 2 小，10/21 那班成人价便宜 300 元。",
  items: [
    { departure: DEPARTURE_1014, route: KALAJUN_10 },
    { departure: DEPARTURE_1021, route: KALAJUN_10 },
  ],
  share_url: "http://localhost:3004/s/AlE5jmzx86rcTTM2",
};

// The same call mid-stream: one 团期 has landed and the link is not minted until it finishes.
const shortlist_streaming: ShortlistPayload = {
  title: shortlist.title,
  note: shortlist.note,
  items: shortlist.items.slice(0, 1),
};

const comparison: ComparisonPayload = {
  title: "三条伊犁线的差别在哪儿",
  entries: [
    {
      product_id: "RT-1021",
      product: XINJIANG_8,
      pros: ["起价最低", "标了亲子与轻徒步"],
      cons: ["喀拉峻只留一天"],
      best_for: "预算优先的家庭客",
    },
    {
      product_id: "RT-1022",
      product: KALAJUN_10,
      pros: ["10 天走透伊犁", "喀拉峻两晚、琼库什台一晚"],
      cons: ["比 8 日线贵 2100"],
      best_for: "时间够、想拍照的客人",
    },
    {
      product_id: "RT-1024",
      product: LUXURY_8,
      pros: ["标了五钻与老人友好", "同样 8 天"],
      cons: ["价格高出近四千"],
      best_for: "带长辈、住宿要求高的客人",
    },
  ],
  dimensions: ["天数", "出发城市", "起价", "标签"],
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
    { label: "报三个价位", detail: "先把 8 日、10 日、五钻三条线的起价和标签讲清楚。", products: ROUTES },
    { label: "开客人选中那条线的团期", detail: "按 2 大 2 小报总价，说明余位和成团进度。", products: [DEPARTURE_1014] },
    { label: "口头确认后占位", detail: "占位 30 分钟到期自动释放；取消或改人数只能在 ERP 后台处理。", products: [] },
  ],
};

const guide: GuidePayload = {
  title: "退改与成团规则",
  sections: [
    {
      heading: "不成团怎么办",
      body: "团期显示待成团时，人数不够会在出发前 7 天通知改期、转线路或全额退款；因不成团产生的机票、酒店退改损失由 ACME 旅行社承担。",
    },
    {
      heading: "退团扣费",
      body: "出发前 30 天以上取消只扣已实际产生的损失；15 至 29 天扣团款 20%；7 至 14 天扣 50%；出发前 6 天内（含）扣 100%。",
    },
    {
      heading: "儿童价",
      body: "不占床儿童价含车位、导服与正常餐，不含床位与门票；占床儿童价仅部分团期提供，以团期报价为准。",
    },
  ],
  sources: ["退改政策", "成团规则", "儿童价规则"],
};

// The cart is the 预留 orders this conversation wrote: two hold seats and count down, and the
// 10/11 团期 had only two left, so a party of four went onto its 候补 and holds nothing.
const cart: CartPayload = {
  items: [
    {
      product_id: "DP-3017",
      title: "伊犁·喀拉峻草原深度 10 日 10/14 出发",
      // The ERP totals the order at 25,720 元 for 2 大 2 小; the line splits it across four heads.
      price: 6430.0,
      quantity: 4,
      option_values: { depart_date: "2026-10-14" },
      variant_of: "RT-1022",
      line_total: 25720.0,
    },
    {
      product_id: "DP-3018",
      title: "伊犁·喀拉峻草原深度 10 日 10/21 出发",
      price: 6280.0,
      quantity: 4,
      option_values: { depart_date: "2026-10-21" },
      variant_of: "RT-1022",
      line_total: 25120.0,
    },
    {
      product_id: "DP-3016",
      title: "伊犁·喀拉峻草原深度 10 日 10/11 出发（候补）",
      price: 6430.0,
      quantity: 4,
      option_values: { depart_date: "2026-10-11" },
      variant_of: "RT-1022",
      line_total: 25720.0,
    },
  ],
  item_count: 12,
  subtotal: 76560.0,
  currency: "CNY",
  holds: [
    {
      hold_id: "ORD070001",
      product_id: "DP-3017",
      expires_at: "2026-10-06T09:42:00+00:00",
      seconds_remaining: 1524,
    },
    {
      hold_id: "ORD070002",
      product_id: "DP-3018",
      expires_at: "2026-10-06T09:20:00+00:00",
      seconds_remaining: 47,
    },
  ],
};

const checkout: CheckoutPayload = {
  note: "10/14 和 10/21 两班占着位，10/21 那班还有 47 秒到期；10/11 只剩 2 个位置，四个人排的是候补。",
  cart,
};

const order_status: OrderStatusPayload = {
  order_id: "70001",
  summary: "10/14 出发的喀拉峻深度 10 日已写成预留单，2 大 2 小共 4 个位置。",
  next_step: "客人确认后在 ERP 后台把预留单转成确认单，再收定金。",
  order: {
    order_id: "70001",
    status: "processing",
    placed_at: "2026-10-06T09:12:00+00:00",
    items: [
      {
        product_id: "DP-3017",
        title: "伊犁·喀拉峻草原深度 10 日（XJ-YLKL-20261014-001）",
        quantity: 4,
        price: 6430.0,
      },
    ],
    total: 25720.0,
    currency: "CNY",
    estimated_delivery: "预留，出发 2026-10-14",
  },
};

// A request too broad to shortlist: the question, the catalog's own groups as chips, and the
// footholds the advisor may open straight away.
const focus: FocusPayload = {
  question: "新疆这批线路先按方向缩一下，客人想走哪一片？",
  total: 7,
  shown: 3,
  dimension: "线路系",
  groups: [
    {
      label: "线路系",
      filter: "region",
      values: [
        { value: "伊犁", count: 4, ask: "只看线路系：伊犁" },
        { value: "喀纳斯", count: 2, ask: "只看线路系：喀纳斯" },
        { value: "南疆", count: 1, ask: "只看线路系：南疆" },
      ],
    },
    {
      label: "出发城市",
      filter: "departure_city",
      values: [
        { value: "乌鲁木齐", count: 6, ask: "只看出发城市：乌鲁木齐" },
        { value: "喀什", count: 1, ask: "只看出发城市：喀什" },
      ],
    },
    {
      label: "天数",
      filter: "days_min/days_max",
      values: [
        { value: "7 天以内", count: 2, ask: "只看天数：7 天以内" },
        { value: "8–10 天", count: 4, ask: "只看天数：8–10 天" },
        { value: "11–13 天", count: 1, ask: "只看天数：11–13 天" },
      ],
    },
    { label: "成团", filter: "", values: [{ value: "已成团", count: 3 }, { value: "待成团", count: 4 }] },
  ],
  anchors: [XINJIANG_8, KALAJUN_10, LUXURY_8],
};

export const SHOWCASE = {
  products,
  focus,
  departures,
  shortlist,
  shortlist_streaming,
  comparison,
  plan,
  guide,
  checkout,
  order_status,
  cart,
};
