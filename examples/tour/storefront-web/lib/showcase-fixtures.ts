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
  AttachmentsPayload,
  CartPayload,
  CheckoutPayload,
  ComparisonPayload,
  DeparturesPayload,
  FocusPayload,
  GuidePayload,
  ItineraryPayload,
  OrderStatusPayload,
  PlanPayload,
  Product,
  ProductsPayload,
  RouteDaysPayload,
  ShortlistPayload,
} from "./types";

// --- Routes (线路), as search returns them ---

const XINJIANG_8: Product = {
  product_id: "RT-1021",
  title: "伊犁北疆环线 8 日纯玩小团",
  brand: "ACME 旅行社 新疆部",
  price: 5580.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: [
    "纯玩",
    "四钻",
    "已复核"
  ],
  attributes: {
    route_code: "YLBJ",
    days: "8",
    nights: "7",
    depart_city: "乌鲁木齐",
    countries: "新疆",
    region: "伊犁",
    airline: "",
    hotel_standard: "全程当地四钻酒店，赛里木湖与那拉提各连住一晚",
    meal_standard: "含 7 早 5 正，风味餐两顿",
    shopping_stops: "0",
    optional_count: "0",
    ticket_count: "3",
    gift_count: "0",
    places: "乌鲁木齐|赛里木湖|霍尔果斯|伊宁|喀拉峻草原|特克斯|那拉提草原|那拉提|独库公路北段|乌鲁木齐送站",
    highlights: "8 座车不超 8 人，全程零购物，赛里木湖与那拉提各住一晚，适合带孩子的家庭。|赛里木湖|那拉提草原",
    doc: "reviewed",
    match: "exact",
    departures: "2026-10-13:满员|2026-10-17:已成团",
    departures_window: "2026-10-11..2026-10-20",
    catalog_matches: "3"
  },
  in_stock: true,
  short_description: "8 座车不超 8 人，全程零购物，赛里木湖与那拉提各住一晚，适合带孩子的家庭。",
  options: {
    depart_date: [
      "2026-10-13",
      "2026-10-17"
    ]
  }
};

const KALAJUN_10: Product = {
  product_id: "RT-1022",
  title: "伊犁·喀拉峻草原深度 10 日",
  brand: "ACME 旅行社 新疆部",
  price: 7580.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: [
    "纯玩",
    "四钻",
    "已复核"
  ],
  attributes: {
    route_code: "YLKL",
    days: "10",
    nights: "9",
    depart_city: "乌鲁木齐",
    countries: "新疆",
    region: "伊犁",
    airline: "",
    hotel_standard: "全程当地四钻酒店，喀拉峻连住两晚",
    meal_standard: "含 9 早 6 正",
    shopping_stops: "0",
    optional_count: "1",
    ticket_count: "6",
    gift_count: "0",
    places: "乌鲁木齐|赛里木湖|伊宁|琼库什台|喀拉峻|西喀拉峻|特克斯|夏塔古道|昭苏|唐布拉",
    highlights: "6 人小车走透伊犁，喀拉峻两晚、琼库什台一晚，早晚光线全留给拍照。|喀拉峻|琼库什台",
    doc: "reviewed",
    match: "exact",
    departures: "2026-10-11:满员|2026-10-14:可报名",
    departures_window: "2026-10-11..2026-10-20",
    catalog_matches: "3"
  },
  in_stock: true,
  short_description: "6 人小车走透伊犁，喀拉峻两晚、琼库什台一晚，早晚光线全留给拍照。",
  options: {
    depart_date: [
      "2026-10-11",
      "2026-10-14"
    ]
  }
};

const LUXURY_8: Product = {
  product_id: "RT-1024",
  title: "伊犁五钻轻奢 8 日私享小团",
  brand: "ACME 旅行社 新疆部",
  price: 9480.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: [
    "纯玩",
    "五钻",
    "已复核"
  ],
  attributes: {
    route_code: "YLQS",
    days: "8",
    nights: "7",
    depart_city: "乌鲁木齐",
    countries: "新疆",
    region: "伊犁",
    airline: "",
    hotel_standard: "全程当地五钻酒店，那拉提连住两晚",
    meal_standard: "含 7 早 6 正，一顿哈萨克家访宴",
    shopping_stops: "0",
    optional_count: "0",
    ticket_count: "3",
    gift_count: "0",
    places: "乌鲁木齐|赛里木湖|伊宁|喀拉峻草原|特克斯|昭苏草原|那拉提草原|那拉提",
    highlights: "全程五钻酒店与 6 座商务车，每日车程控制在 4 小时内，含双导服务。|赛里木湖|那拉提草原",
    doc: "reviewed",
    match: "exact",
    departures: "2026-10-13:已成团|2026-10-18:可报名",
    departures_window: "2026-10-11..2026-10-20",
    catalog_matches: "3"
  },
  in_stock: true,
  short_description: "全程五钻酒店与 6 座商务车，每日车程控制在 4 小时内，含双导服务。",
  options: {
    depart_date: [
      "2026-10-13",
      "2026-10-18"
    ]
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

/**
 * The second version of a 定制方案 built on RT-1022 for a party of 2 大 1 小: the customer asked
 * for a day on the grassland, which is the added day, and to skip the 伊宁 rest day, which is the
 * removed one; the 第 7 天 note was rewritten to cut its driving. The prices are the baseline
 * 团期's own — a 定制 line is priced by the 计调, not here.
 */
const itinerary: ItineraryPayload = {
  plan_id: "PL-7f3c2a91",
  version: 2,
  parent_version: 1,
  title: "伊犁定制 11 日 · 喀拉峻多住一天",
  travel_dates: "2026-10-14 至 2026-10-24",
  party: "2大1小（8岁）",
  route: KALAJUN_10,
  departure: DEPARTURE_1014,
  days: [
    {
      label: "第 1 天 乌鲁木齐集合",
      note: "全天接机，住机场附近酒店，晚上把路况和海拔讲一遍。",
      request: false,
      change: "same",
      changed_fields: [],
    },
    {
      label: "第 2 天 乌鲁木齐—赛里木湖",
      note: "走果子沟大桥进伊犁，傍晚到赛里木湖看日落。",
      request: false,
      change: "same",
      changed_fields: [],
    },
    {
      label: "第 3 天 赛里木湖—特克斯",
      note: "上午环湖，下午翻山到特克斯，住喀拉峻山下。",
      request: false,
      change: "same",
      changed_fields: [],
    },
    {
      label: "第 4 天 喀拉峻空中草原",
      note: "东西喀拉峻各半天，五花草甸和鳄鱼湾都留足时间。",
      request: false,
      change: "same",
      changed_fields: [],
    },
    {
      label: "第 5 天 喀拉峻牧场一日",
      note: "客人要的一天：上午跟牧民转场，下午草原自由活动，全天不安排车程。",
      request: true,
      change: "added",
      changed_fields: [],
    },
    {
      label: "第 6 天 喀拉峻—琼库什台",
      note: "沿库尔代河谷进琼库什台，住木屋民宿。",
      request: false,
      change: "same",
      changed_fields: [],
    },
    {
      label: "第 7 天 琼库什台—夏塔",
      note: "改成早出发走夏塔古道口，中午河谷野餐，比原方案少两个小时车程。",
      request: false,
      change: "changed",
      changed_fields: ["note"],
    },
    {
      label: "第 8 天 夏塔—昭苏",
      note: "昭苏油菜花田与格登碑，傍晚在马场拍天马。",
      request: false,
      change: "same",
      changed_fields: [],
    },
    {
      label: "第 9 天 昭苏—伊宁",
      note: "经琼博拉森林回伊宁，晚上逛六星街。",
      request: false,
      change: "same",
      changed_fields: [],
    },
    {
      label: "第 10 天 伊宁—乌鲁木齐",
      note: "上午薰衣草园，下午高铁回乌鲁木齐。",
      request: false,
      change: "same",
      changed_fields: [],
    },
    {
      label: "第 11 天 乌鲁木齐送机",
      note: "按航班送机，时间富余可加大巴扎半日。",
      request: false,
      change: "same",
      changed_fields: [],
    },
  ],
  removed_days: [
    {
      index: 5,
      label: "第 6 天 伊宁市区休整",
      note: "原方案在伊宁休整半天，客人说不想回城。",
    },
  ],
  summary: "+1 天（第 5 天）；改 1 天（第 7 天）；删 1 天（原第 6 天）",
  reference_price: {
    tong_ye_adult: 7880,
    market_adult: 7880,
    quote_source: "customer",
    party_total: 20740,
  },
  erp_route_id: "RT-1188",
  handoff_text:
    "定制方案 PL-7f3c2a91 v2（基线 RT-1022 伊犁·喀拉峻草原深度 10 日，团期 XJ-YLKL-20261014-001）\n" +
    "人数：2大1小（8岁）　日期：2026-10-14 至 2026-10-24（11 天）\n" +
    "改动：第 5 天新增喀拉峻牧场一日（客人要求，不安排车程）；第 7 天改走夏塔古道口，减两小时车程；删原第 6 天伊宁市区休整。\n" +
    "请按 11 天重新核算车导、住宿与门票差价，回报同业价。",
  share_url: "http://localhost:3004/p/Qm7pT2vXbN9kLr4s",
};

// The same call mid-stream: the head of the plan is written, and no version, marks or link yet.
const itinerary_streaming: ItineraryPayload = {
  title: itinerary.title,
  travel_dates: itinerary.travel_dates,
  party: itinerary.party,
  days: itinerary.days.slice(0, 6).map(({ label, note }) => ({ label, note })),
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
// The 行程附件 of the two 线路 the advisor asked for, each under the agency's own file name.
const attachments: AttachmentsPayload = {
  note: "两条线的行程单，点下载即存到本机。",
  items: [
    {
      product_id: "RT-1021",
      title: "伊犁北疆环线 8 日纯玩小团",
      name: "伊犁北疆环线8日纯玩小团-行程单-0901.docx",
      extension: "docx",
    },
    {
      product_id: "RT-1022",
      title: "伊犁·喀拉峻草原深度 10 日",
      name: "喀拉峻草原深度10日-行程单.pdf",
      extension: "pdf",
    },
  ],
};

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

// --- The 线路文档 cards: routes as their reviewed documents state them ---

/**
 * These are not a snapshot of a read, so none of them is a `const X: Product` literal: they are
 * written here to draw the cards the 线路文档 feeds — the route card, its 逐日行程 and its 团期 —
 * with every line, hotel, carrier and person invented for the showcase.
 */

const CEYLON_7 = {
  product_id: "RT-2041",
  title: "锡兰环岛 7 日 · 茶山与南岸",
  brand: "ACME 旅行社 南亚部",
  price: 6980.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["直飞", "当地四星", "纯玩", "上海出发"],
  attributes: {
    route_code: "XLHD7",
    days: "7",
    nights: "5",
    depart_city: "上海",
    countries: "斯里兰卡",
    region: "南亚",
    airline: "海途航空 上海直飞，往返均为夜航",
    hotel_standard: "当地四星，加勒古城与本托塔各一晚海景房",
    meal_standard: "含 6 早 4 正，其中一顿海鲜餐",
    shopping_stops: "0",
    optional_count: "2",
    ticket_count: "6",
    gift_count: "1",
    places: "科伦坡|尼甘布|丹布勒|狮子岩|康提|努沃勒埃利耶|埃拉|加勒|本托塔",
    highlights: "茶山小火车坐足两段|加勒古城住一晚，日落不用赶路|全程不进购物店",
    doc: "reviewed",
    match: "exact",
    catalog_matches: "5",
    attachment: "锡兰环岛7日-行程单-0903.pdf",
  },
  in_stock: true,
  options: { depart_date: ["2026-10-12", "2026-10-19", "2026-10-26"] },
} satisfies Product;

const EUROPE_12 = {
  product_id: "RT-2088",
  title: "法意瑞 12 日 · 三国经典环线",
  brand: "ACME 旅行社 欧洲部",
  price: 13800.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["四星酒店", "含门票", "上海出发"],
  attributes: {
    route_code: "FYR12",
    days: "12",
    nights: "10",
    depart_city: "上海",
    countries: "法国|意大利|瑞士",
    region: "西欧",
    airline: "云桥航空 上海—法兰克福 往返，转机一次",
    hotel_standard: "四星，米兰与卢塞恩各两晚",
    meal_standard: "含 10 早 8 正，正餐六菜一汤",
    shopping_stops: "2",
    optional_count: "4",
    ticket_count: "5",
    gift_count: "2",
    places: "上海|法兰克福|卢森堡|巴黎|第戎|卢塞恩|因特拉肯|米兰|威尼斯|佛罗伦萨|罗马",
    highlights: "卢塞恩住两晚，少女峰当日往返|威尼斯本岛用午餐|两家购物店已写进行程",
    doc: "reviewed",
    match: "exact",
    catalog_matches: "5",
    attachment: "法意瑞12日-行程单.docx",
  },
  in_stock: true,
  options: { depart_date: ["2026-10-08", "2026-10-15"] },
} satisfies Product;

const SIAM_5 = {
  product_id: "RT-2112",
  title: "暹罗湾 5 日 · 曼谷与沙美岛",
  brand: "ACME 旅行社 东南亚部",
  price: 0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  labels: ["海岛", "广州出发"],
  attributes: {
    route_code: "XLW5",
    days: "5",
    nights: "3",
    depart_city: "广州",
    countries: "泰国",
    region: "东南亚",
    airline: "湄洲航空 广州—曼谷 直飞",
    hotel_standard: "曼谷当地四星，沙美岛海边度假村一晚",
    meal_standard: "含 3 早 3 正",
    shopping_stops: "3",
    optional_count: "5",
    ticket_count: "2",
    gift_count: "0",
    places: "广州|曼谷|芭提雅|沙美岛",
    highlights: "沙美岛住一晚|行程含三家购物店，客人怕进店的先别报这条",
    doc: "draft",
    match: "relaxed",
    mismatch: "客人要的纯玩这条线没有：行程里写了三家购物店。",
    catalog_matches: "5",
  },
  in_stock: true,
} satisfies Product;

/** The same 线路 as a dated search stamps it: the 团期 it sells inside the window, each state. */
function withDepartures(route: Product, window: string, dates: string): Product {
  return {
    ...route,
    attributes: { ...route.attributes, departures_window: window, departures: dates },
  };
}

const route_products: ProductsPayload = {
  title: "客人要的三条线，先看线路本身",
  layout: "carousel",
  items: [
    { product: CEYLON_7, reason: "直飞加全程无购物店，客人问的两件事这条都占了。" },
    { product: EUROPE_12, reason: "12 天走三国，卢塞恩住两晚，车程压得住。" },
    { product: SIAM_5, reason: "价格还没发布，行程也只是解析稿，报之前先跟计调核一遍。" },
  ],
};

const route_products_dated: ProductsPayload = {
  title: "10/08–10/22 出发的三条线",
  layout: "grid",
  items: [
    {
      product: withDepartures(
        CEYLON_7,
        "2026-10-08..2026-10-22",
        "2026-10-12:可报名|2026-10-19:已成团|2026-10-22:满员",
      ),
      reason: "10/12 那班还空着大半，2 大 1 小随时能占。",
    },
    {
      product: withDepartures(
        EUROPE_12,
        "2026-10-08..2026-10-22",
        "2026-10-08:已成团|2026-10-15:可报名|2026-10-19:截止",
      ),
      reason: "10/15 是窗口里唯一还收人的班。",
    },
  ],
};

/**
 * The 逐日行程 of RT-2041: a 7 天 5 晚 line whose document writes six day blocks — the 回程 is a
 * night flight, so the seventh day is the tail strip rather than a block of its own.
 */
const route_days: RouteDaysPayload = {
  route_id: "RT-2041",
  title: "锡兰环岛 7 日 · 茶山与南岸",
  route_code: "XLHD7",
  department: "ACME 旅行社 南亚部",
  day_count: 7,
  nights: 5,
  depart_city: "上海",
  countries: ["斯里兰卡"],
  reviewed: true,
  reviewed_by: "计调 周敏",
  highlights: [
    "茶山小火车坐足两段，努沃勒埃利耶到埃拉不换车",
    "加勒古城住一晚，日落不用赶路",
    "全程不进购物店，自费两项写在行程里",
  ],
  airline: "海途航空 上海直飞，往返均为夜航",
  hotel_standard: "当地四星，加勒古城与本托塔各一晚海景房",
  meal_standard: "含 6 早 4 正，其中一顿海鲜餐",
  outbound: [
    {
      day: 1,
      flight_no: "HT621",
      carrier: "海途航空",
      from_place: "上海浦东",
      to_place: "科伦坡",
      times: "23:40–04:35+1",
    },
  ],
  inbound: [
    {
      day: 7,
      flight_no: "HT622",
      carrier: "海途航空",
      from_place: "科伦坡",
      to_place: "上海浦东",
      times: "01:20–13:05",
    },
  ],
  days: [
    {
      day: 1,
      title: "上海浦东 — 科伦坡",
      places: ["上海", "科伦坡"],
      overnight: "flight",
      hotel: null,
      meals: {
        breakfast: { text: "", included: false },
        lunch: { text: "", included: false },
        dinner: { text: "", included: false },
      },
      flights: [
        {
          flight_no: "HT621",
          carrier: "海途航空",
          from_place: "上海浦东",
          to_place: "科伦坡",
          times: "23:40–04:35+1",
        },
      ],
      sights: [],
      text: "21:00 浦东机场 T2 集合，领队发放登机牌与落地签资料。\n夜航六个半小时，机上过夜，落地即次日清晨。",
    },
    {
      day: 2,
      title: "科伦坡 — 尼甘布 — 丹布勒",
      places: ["科伦坡", "尼甘布", "丹布勒"],
      overnight: "hotel",
      hotel: { name: "丹布勒石林度假酒店", grade: "当地四星", or_similar: true },
      meals: {
        breakfast: { text: "酒店早餐", included: true },
        lunch: { text: "当地餐厅咖喱套餐", included: true },
        dinner: { text: "酒店自助", included: true },
      },
      flights: [],
      sights: [
        { name: "尼甘布渔市", kind: "景点", duration: "40 分钟", ticket_included: null },
        { name: "丹布勒石窟寺", kind: "景点", duration: "1.5 小时", ticket_included: true },
        { name: "独立纪念堂", kind: "外观", duration: "15 分钟", ticket_included: null },
      ],
      text: "清晨落地后先到酒店用早餐并短暂休整，再沿西海岸北上。\n下午进丹布勒石窟寺，五个洞窟按开凿年代走一遍，日落前抵酒店。",
    },
    {
      day: 3,
      title: "丹布勒 — 狮子岩 — 康提",
      places: ["丹布勒", "狮子岩", "康提"],
      overnight: "hotel",
      hotel: { name: "康提湖畔庭院酒店", grade: "当地四星", or_similar: true },
      meals: {
        breakfast: { text: "酒店早餐", included: true },
        lunch: { text: "山景餐厅自助", included: true },
        dinner: { text: "自理，酒店周边步行可达", included: false },
      },
      flights: [],
      sights: [
        { name: "狮子岩", kind: "景点", duration: "3 小时", ticket_included: true },
        { name: "康提佛牙寺", kind: "景点", duration: "1 小时", ticket_included: true },
        { name: "皇家植物园", kind: "自费", duration: "1.5 小时", ticket_included: false },
      ],
      text: "六点半出发避开正午日照，登狮子岩全程约一千二百级台阶，体力一般的客人可在山腰壁画层折返。\n傍晚赶上佛牙寺的供奉仪式。",
    },
    {
      day: 4,
      title: "康提 — 努沃勒埃利耶 — 埃拉",
      places: ["康提", "努沃勒埃利耶", "埃拉"],
      overnight: "hotel",
      hotel: { name: "埃拉云雾山庄", grade: "当地四星", or_similar: true },
      meals: {
        breakfast: { text: "酒店早餐", included: true },
        lunch: { text: "茶园餐厅", included: true },
        dinner: { text: "酒店晚餐", included: true },
      },
      flights: [],
      sights: [
        { name: "高山茶园与制茶厂", kind: "景点", duration: "1.5 小时", ticket_included: true },
        { name: "茶园下午茶", kind: "赠送", duration: "30 分钟", ticket_included: null },
        { name: "高山小火车 努沃勒埃利耶—埃拉", kind: "景点", duration: "2.5 小时", ticket_included: true },
      ],
      text: "上午在茶厂看一遍萎凋、揉捻与烘干，赠送的下午茶就在茶园露台。\n下午段小火车走的是全线风景最好的一截，车厢无空调，窗边位置先到先得。",
    },
    {
      day: 5,
      title: "埃拉 — 乌达瓦拉维 — 加勒",
      places: ["埃拉", "乌达瓦拉维", "加勒"],
      overnight: "hotel",
      hotel: { name: "加勒城墙海景旅舍", grade: "当地四星", or_similar: false },
      meals: {
        breakfast: { text: "酒店早餐", included: true },
        lunch: { text: "途中餐厅", included: true },
        dinner: { text: "自理，古城内餐厅多", included: false },
      },
      flights: [],
      sights: [
        { name: "乌达瓦拉维野生动物园吉普", kind: "自费", duration: "3 小时", ticket_included: false },
        { name: "加勒古堡与灯塔", kind: "景点", duration: "2 小时", ticket_included: true },
        { name: "古城城墙日落自由活动", kind: "自由活动", duration: "1 小时", ticket_included: null },
      ],
      text: "南下路况尚可，全天车程约五小时，分两段走。\n住在城墙里，日落与清晨两次逛古城都不用再跑一趟。",
    },
    {
      day: 6,
      title: "加勒 — 本托塔 — 科伦坡",
      places: ["加勒", "本托塔", "科伦坡"],
      overnight: "flight",
      hotel: null,
      meals: {
        breakfast: { text: "酒店早餐", included: true },
        lunch: { text: "海边海鲜餐", included: true },
        dinner: { text: "自理", included: false },
      },
      flights: [
        {
          flight_no: "HT622",
          carrier: "海途航空",
          from_place: "科伦坡",
          to_place: "上海浦东",
          times: "01:20–13:05+1",
        },
      ],
      sights: [
        { name: "本托塔海滩自由活动", kind: "自由活动", duration: "2 小时", ticket_included: null },
        { name: "红树林游船", kind: "赠送", duration: "1 小时", ticket_included: true },
        { name: "海龟保育站", kind: "景点", duration: "40 分钟", ticket_included: true },
      ],
      text: "中午的海鲜餐是全程唯一一顿，安排在本托塔海边。\n晚上返回科伦坡机场，办完登机手续即搭次日凌晨的航班回程。",
    },
  ],
  inclusions: [
    "上海—科伦坡往返国际机票及税费",
    "全程当地四星酒店 5 晚，双人标准间",
    "行程内 6 早 4 正，含一顿海鲜餐",
    "行程所列景点首道门票 6 处",
    "中文领队与当地司机导游服务",
    "旅行社责任险",
  ],
  exclusions: [
    "斯里兰卡电子签证费用",
    "单房差 1,600 元",
    "自费项目与个人消费",
    "司导小费，建议每人每天 30 元",
  ],
  shopping: [],
  optional: [
    { name: "乌达瓦拉维野生动物园吉普", price: "480元/人", day: 5 },
    { name: "皇家植物园", price: "180元/人", day: 3 },
  ],
  policies: {
    single_room: "单房差 1,600 元，全程有效；愿意拼房的由计调协调，拼不上仍按单房差收。",
    child: "12 岁以下不占床减 800 元，占床与成人同价；婴儿另议。",
    visa: "电子签，出发前 7 个工作日交护照首页扫描件，签证费现付。",
    cancellation: "出发前 30 天以上退团只扣实际损失，15 至 29 天扣 20%，7 至 14 天扣 50%，6 天内扣 100%。",
    deposit: "口头确认后 24 小时内交定金 2,000 元/人，余款出发前 10 天付清。",
  },
  attachment_name: "锡兰环岛7日-行程单-0903.pdf",
};

/** A short line whose document is still the parser's draft: three days, no flights. */
const route_days_short: RouteDaysPayload = {
  route_id: "RT-2107",
  title: "姑苏太湖 3 日 · 高铁往返",
  route_code: "GSTH3",
  department: "ACME 旅行社 华东部",
  day_count: 3,
  nights: 2,
  depart_city: "上海",
  countries: ["中国"],
  reviewed: false,
  highlights: ["高铁往返，不赶早班车", "太湖边住一晚"],
  hotel_standard: "本地四星，太湖一侧为湖景房",
  meal_standard: "含 2 早 2 正",
  outbound: [],
  inbound: [],
  days: [
    {
      day: 1,
      title: "上海 — 苏州",
      places: ["上海", "苏州"],
      overnight: "hotel",
      hotel: { name: "平江路河畔酒店", grade: "本地四星", or_similar: true },
      meals: {
        breakfast: { text: "", included: false },
        lunch: { text: "苏帮菜", included: true },
        dinner: { text: "自理", included: false },
      },
      flights: [],
      sights: [
        { name: "拙政园", kind: "景点", duration: "2 小时", ticket_included: true },
        { name: "平江路自由活动", kind: "自由活动", duration: "2 小时", ticket_included: null },
      ],
      text: "上午高铁 30 分钟到苏州，行李先送酒店。\n下午拙政园，傍晚平江路自己逛。",
    },
    {
      day: 2,
      title: "苏州 — 太湖",
      places: ["苏州", "太湖"],
      overnight: "hotel",
      hotel: { name: "太湖西山湖景酒店", grade: "本地四星", or_similar: true },
      meals: {
        breakfast: { text: "酒店早餐", included: true },
        lunch: { text: "农家太湖三白", included: true },
        dinner: { text: "自理", included: false },
      },
      flights: [],
      sights: [
        { name: "西山岛环岛", kind: "景点", duration: "3 小时", ticket_included: true },
        { name: "碧螺春茶庄", kind: "购物", duration: "60 分钟", ticket_included: null },
      ],
      text: "解析稿这里写了一个 60 分钟的茶庄停留，复核时要确认算不算购物店。",
    },
    {
      day: 3,
      title: "太湖 — 上海",
      places: ["太湖", "上海"],
      overnight: "home",
      hotel: null,
      meals: {
        breakfast: { text: "酒店早餐", included: true },
        lunch: { text: "自理", included: false },
        dinner: { text: "自理", included: false },
      },
      flights: [],
      sights: [{ name: "东山雕花楼", kind: "外观", duration: "30 分钟", ticket_included: null }],
      text: "上午自由活动，午后高铁回上海，约 17:00 到虹桥。",
    },
  ],
  inclusions: ["上海—苏州往返高铁二等座", "本地四星 2 晚", "含 2 早 2 正", "导游服务"],
  exclusions: ["个人消费", "未列明的门票"],
  shopping: [{ name: "碧螺春茶庄", day: 2, duration: "60 分钟" }],
  optional: [],
  policies: {
    single_room: "单房差 480 元。",
    child: "1.2 米以下不占床减 300 元。",
    cancellation: "出发前 3 天内取消扣 50%。",
  },
};

/** The same 行程 mid-stream: the head is written and the days are still landing. */
const route_days_streaming: RouteDaysPayload = {
  route_id: route_days.route_id,
  title: route_days.title,
  route_code: route_days.route_code,
  department: route_days.department,
  day_count: route_days.day_count,
  nights: route_days.nights,
  depart_city: route_days.depart_city,
  countries: route_days.countries,
  reviewed: route_days.reviewed,
  reviewed_by: route_days.reviewed_by,
  highlights: route_days.highlights,
  airline: route_days.airline,
  hotel_standard: route_days.hotel_standard,
  meal_standard: route_days.meal_standard,
  outbound: route_days.outbound,
  days: route_days.days.slice(0, 3),
};

// The 团期 of RT-2041 the ERP sells in that window, as `present_departures` hands them over.
const CEYLON_DP_1012 = {
  product_id: "DP-5102",
  title: "锡兰环岛 7 日 · 10/12 出发",
  brand: "ACME 旅行社 南亚部",
  price: 6980.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    period_code: "NY-XLHD7-20261012-001",
    depart_date: "2026-10-12",
    return_date: "2026-10-18",
    seats_left: "9",
    seats_total: "16",
    group_status: "pending",
  },
  in_stock: true,
  option_values: { depart_date: "2026-10-12" },
  variant_of: "RT-2041",
} satisfies Product;

const CEYLON_DP_1019 = {
  product_id: "DP-5108",
  title: "锡兰环岛 7 日 · 10/19 出发",
  brand: "ACME 旅行社 南亚部",
  price: 7180.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    period_code: "NY-XLHD7-20261019-001",
    depart_date: "2026-10-19",
    return_date: "2026-10-25",
    seats_left: "2",
    seats_total: "16",
    group_status: "confirmed",
  },
  in_stock: true,
  option_values: { depart_date: "2026-10-19" },
  variant_of: "RT-2041",
} satisfies Product;

const CEYLON_DP_1022 = {
  product_id: "DP-5111",
  title: "锡兰环岛 7 日 · 10/22 出发",
  brand: "ACME 旅行社 南亚部",
  price: 7180.0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    period_code: "NY-XLHD7-20261022-001",
    depart_date: "2026-10-22",
    return_date: "2026-10-28",
    seats_left: "0",
    seats_total: "16",
    group_status: "confirmed",
  },
  in_stock: false,
  option_values: { depart_date: "2026-10-22" },
  variant_of: "RT-2041",
} satisfies Product;

const CEYLON_DP_1026 = {
  product_id: "DP-5117",
  title: "锡兰环岛 7 日 · 10/26 出发",
  brand: "ACME 旅行社 南亚部",
  price: 0,
  currency: "CNY",
  image_url: null,
  category: "tour",
  attributes: {
    period_code: "NY-XLHD7-20261026-001",
    depart_date: "2026-10-26",
    return_date: "2026-11-01",
    seats_left: "16",
    seats_total: "16",
    group_status: "pending",
  },
  in_stock: true,
  option_values: { depart_date: "2026-10-26" },
  variant_of: "RT-2041",
} satisfies Product;

const route_departures: DeparturesPayload = {
  route: CEYLON_7,
  window: { from: "2026-10-10", to: "2026-10-28" },
  items: [
    { departure: CEYLON_DP_1012, date: "2026-10-12", weekday: "周一", status: "可报名", price_adult: 6980 },
    { departure: CEYLON_DP_1019, date: "2026-10-19", weekday: "周一", status: "已成团", price_adult: 7180 },
    { departure: CEYLON_DP_1022, date: "2026-10-22", weekday: "周四", status: "满员", price_adult: 7180 },
    { departure: CEYLON_DP_1026, date: "2026-10-26", weekday: "周一", status: "截止", price_adult: null },
  ],
};

export const SHOWCASE = {
  products,
  route_products,
  route_products_dated,
  route_days,
  route_days_short,
  route_days_streaming,
  route_departures,
  focus,
  attachments,
  departures,
  shortlist,
  shortlist_streaming,
  itinerary,
  itinerary_streaming,
  comparison,
  plan,
  guide,
  checkout,
  order_status,
  cart,
};
