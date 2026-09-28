// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** Products mirror data/catalog.json rows; api/tests/test_showcase_fixtures.py checks them. */

import type { Product } from "./types";

const GUESTHOUSE_ALFAMA: Product = {
  product_id: "AL-STAY-101",
  title: "ACME Guesthouses Alfama",
  brand: "ACME Guesthouses",
  price: 214.0,
  currency: "USD",
  rating: 4.8,
  review_count: 412,
  category: "stays",
  labels: ["Top rated", "Near old town"],
  attributes: {
    city: "Lisbon",
    neighborhood: "Alfama",
    room_type: "queen room",
    breakfast_included: "yes",
    refundable: "no",
    typical_rate_band: "225-285",
    price_unit: "per_night",
    date_flex:
      "2026-10-12:225|2026-10-13:215|2026-10-14:217|2026-10-15*:214|2026-10-16:251|2026-10-17:266|2026-10-18:240",
    units_left_for_dates: "2",
  },
  in_stock: true,
  short_description:
    "An eight-room townhouse folded into Alfama's stairstep lanes, with azulejo-lined hallways and breakfast under a lemon tree.",
};

const ROOFTOP_SUITES: Product = {
  product_id: "AL-STAY-104",
  title: "ACME Suites Graca Rooftop",
  brand: "ACME Suites",
  price: 268.0,
  currency: "USD",
  rating: 4.7,
  review_count: 356,
  category: "stays",
  labels: ["Free cancellation"],
  attributes: {
    city: "Lisbon",
    neighborhood: "Graca",
    room_type: "rooftop suite",
    breakfast_included: "yes",
    refundable: "yes",
    free_cancellation_until: "2026-10-13",
    typical_rate_band: "225-310",
    price_unit: "per_night",
    date_flex:
      "2026-10-12:287|2026-10-13:267|2026-10-14:279|2026-10-15*:268|2026-10-16:318|2026-10-17:341|2026-10-18:290",
    units_left_for_dates: "2",
  },
  in_stock: true,
  short_description:
    "Six rooftop suites in hilltop Graca, each with a private terrace looking over the whole amphitheater of Lisbon.",
};

const FADO_WALK: Product = {
  product_id: "AL-EXP-301",
  title: "Alfama at Dusk: Fado & Petiscos Evening Walk",
  brand: "ACME Tours",
  price: 86.0,
  currency: "USD",
  rating: 4.8,
  review_count: 927,
  category: "experiences",
  labels: ["Free cancellation", "Top rated"],
  attributes: {
    city: "Lisbon",
    neighborhood: "Alfama",
    duration_hours: "3",
    price_unit: "per_person",
    refundable: "yes",
    free_cancellation_until: "2026-10-15",
  },
  in_stock: true,
  short_description:
    "Wind through Alfama as the lamps come on, grazing on petiscos before ending at a live fado set in a family-run tavern.",
};

const TILE_WORKSHOP: Product = {
  product_id: "AL-EXP-302",
  title: "Hands-On Azulejo Tile-Painting Workshop",
  brand: "ACME Tours",
  price: 54.0,
  currency: "USD",
  rating: 4.7,
  review_count: 463,
  category: "experiences",
  labels: ["Small group"],
  attributes: {
    city: "Lisbon",
    neighborhood: "Baixa",
    duration_hours: "2",
    price_unit: "per_person",
    refundable: "no",
  },
  in_stock: true,
  short_description:
    "Paint your own azulejo tile in a working Baixa atelier, guided by a ceramicist — fired, glazed, and shipped to you.",
};

const SINTRA_TRIP: Product = {
  product_id: "AL-EXP-303",
  title: "Sintra Palaces & Wild Coast Day Trip",
  brand: "ACME Tours",
  price: 118.0,
  currency: "USD",
  rating: 4.6,
  review_count: 1388,
  category: "experiences",
  labels: ["Free cancellation"],
  attributes: {
    city: "Lisbon",
    neighborhood: "Sintra",
    duration_hours: "8",
    price_unit: "per_person",
    refundable: "yes",
    free_cancellation_until: "2026-10-15",
  },
  in_stock: true,
  short_description:
    "A full day among Sintra's storybook palaces and gardens, ending with cliffs and salt spray on the wild Atlantic coast.",
};

const FLIGHT_ECONOMY: Product = {
  product_id: "AL-FLT-201",
  title: "New York to Lisbon Nonstop — Economy",
  brand: "ACME Air Atlantic",
  price: 498.0,
  currency: "USD",
  rating: 4.3,
  review_count: 1856,
  category: "flights",
  labels: ["Nonstop"],
  attributes: {
    origin_city: "New York",
    destination_city: "Lisbon",
    cabin: "economy",
    departure_time_local: "18:40",
    duration: "6h 55m",
    refundable: "no",
    price_unit: "per_person",
  },
  in_stock: true,
  short_description:
    "ACME Air Atlantic's evening nonstop to Lisbon — leave after work, land with the whole morning ahead.",
};

const FLIGHT_PREMIUM: Product = {
  product_id: "AL-FLT-202",
  title: "New York to Lisbon Nonstop — Premium Economy",
  brand: "ACME Air Atlantic",
  price: 1120.0,
  currency: "USD",
  rating: 4.6,
  review_count: 642,
  category: "flights",
  labels: ["Nonstop", "Free cancellation"],
  attributes: {
    origin_city: "New York",
    destination_city: "Lisbon",
    cabin: "premium economy",
    departure_time_local: "18:40",
    duration: "6h 55m",
    refundable: "yes",
    free_cancellation_until: "2026-10-14",
    price_unit: "per_person",
  },
  in_stock: true,
  short_description:
    "The same overnight Lisbon nonstop with a wider seat, deeper recline, and a fully refundable fare.",
};

export const SHOWCASE = {
  itinerary: {
    title: "你的里斯本长周末",
    // Three nights, so the footer total matches the checkout fixture for the same trip.
    travel_dates: "10月15日（周四）至 10月18日（周日）",
    days: [
      {
        label: "第1天 — 抵达，安顿在阿尔法玛",
        note: "办理入住，缓一缓长途飞行。黄昏时在阶梯小巷里随意走走，在观景台喝一杯绿酒，再找家本地小馆吃晚饭。",
        products: [GUESTHOUSE_ALFAMA],
      },
      {
        label: "第2天 — 徒步老城 + 瓷砖彩绘工坊",
        note: "上午在阿尔法玛和莫拉里亚迷路；下午去拜沙区的瓷砖彩绘工作室，小团授课，成品烧制后寄回家。",
        products: [TILE_WORKSHOP],
      },
      {
        label: "第3天 — 辛特拉一日游",
        note: "童话般的宫殿、葱郁的花园和大西洋的野性悬崖。傍晚前返回，正好留一晚吃顿悠长的晚餐。",
        products: [SINTRA_TRIP],
      },
      {
        label: "第3天 傍晚 — 家庭小馆里的法多",
        note: "日落时分跟着向导穿过阿尔法玛，边走边尝小食，最后在一场现场法多演出中收尾。",
        products: [FADO_WALK],
      },
      {
        label: "第4天 — 慢悠悠的早晨，启程回家",
        note: "在柠檬树下吃早餐，在观景台喝最后一杯浓缩咖啡，然后退房。里斯本十月的光线是金色的，值得在出租车来之前多待一分钟。",
        products: [],
      },
    ],
  },

  // Two stays on the same arrival day are alternatives, so the footer prices the pick.
  itinerary_alternatives: {
    title: "里斯本，两种住法",
    travel_dates: "10月15日（周四）至 10月18日（周日）",
    days: [
      {
        label: "第1天 — 抵达，选好落脚点",
        note: "同样三晚，两处住所可选：柠檬树联排小楼是提前预订的优惠价，不可退款；格拉萨屋顶套房在入住前两天都可免费取消。每晚 $54 的差价，就是灵活性的价格。",
        products: [GUESTHOUSE_ALFAMA, ROOFTOP_SUITES],
      },
      {
        label: "第2天 — 徒步老城 + 瓷砖彩绘工坊",
        note: "上午在阿尔法玛和莫拉里亚迷路，下午去拜沙区的瓷砖彩绘工作室。",
        products: [TILE_WORKSHOP],
      },
      {
        label: "第3天 — 辛特拉一日游",
        note: "童话般的宫殿和大西洋的野性悬崖，傍晚前返回。",
        products: [SINTRA_TRIP],
      },
      {
        label: "第4天 — 慢悠悠的早晨，启程回家",
        note: "在观景台喝最后一杯浓缩咖啡，然后退房。",
        products: [],
      },
    ],
  },

  comparison: {
    title: "灵活性值多少钱",
    entries: [
      {
        product_id: FLIGHT_ECONOMY.product_id,
        product: FLIGHT_ECONOMY,
        pros: ["直飞过夜航班，06:35 落地", "每人省 $622", "同一出发时间"],
        cons: ["不可退款，改签需付手续费", "7 小时坐得更挤"],
        best_for: "日期已定、轻装上阵的人",
      },
      {
        product_id: FLIGHT_PREMIUM.product_id,
        product: FLIGHT_PREMIUM,
        pros: ["全额可退票价", "座椅更宽、可躺更深", "含两件托运行李"],
        cons: ["每人多付 $622"],
        best_for: "计划可能变动的人，或者想在飞机上睡个好觉的人",
      },
    ],
    dimensions: ["价格", "可退款", "舒适度", "行李"],
    recommended_product_id: FLIGHT_PREMIUM.product_id,
    // The price spread the server attaches to every comparison.
    price_delta: {
      amount: 622.0,
      low_product_id: FLIGHT_ECONOMY.product_id,
      low_price: 498.0,
      high_product_id: FLIGHT_PREMIUM.product_id,
      high_price: 1120.0,
    },
  },

  products: {
    title: "阿尔法玛与格拉萨的精品住宿",
    layout: "carousel" as const,
    items: [
      { product: GUESTHOUSE_ALFAMA, reason: "老城区最受喜爱的联排小楼" },
      { product: ROOFTOP_SUITES, reason: "私享屋顶，从城堡一路望到河" },
      { product: FADO_WALK, reason: "和任一住宿都很搭" },
    ],
  },

  checkout: {
    note: "ACME Guesthouses 三晚、辛特拉一日游和两人的法多之夜，预订后一分钟内确认邮件送达。",
    fulfillment_method: "delivery" as const,
    cart: {
      items: [
        {
          product_id: GUESTHOUSE_ALFAMA.product_id,
          title: GUESTHOUSE_ALFAMA.title,
          price: 214.0,
          quantity: 3,
          line_total: 642.0,
        },
        {
          product_id: FADO_WALK.product_id,
          title: FADO_WALK.title,
          price: 86.0,
          quantity: 2,
          line_total: 172.0,
        },
        {
          product_id: SINTRA_TRIP.product_id,
          title: SINTRA_TRIP.title,
          price: 118.0,
          quantity: 2,
          line_total: 236.0,
        },
      ],
      item_count: 7,
      subtotal: 1050.0,
      currency: "USD",
    },
  },

  order_status: {
    order_id: "AL-30418",
    summary:
      "你的京都之旅正在确认中：机票已出票，ACME Ryokan 正在确认你的三晚住宿。一切就绪后会合并在一封邮件里发给你。",
    next_step: "无需操作，确认通常在几小时内完成。",
    order: {
      order_id: "AL-30418",
      status: "processing",
      placed_at: "2026-05-30T22:41:00Z",
      items: [
        { product_id: "AL-FLT-204", title: "San Francisco to Kyoto — Economy, One Stop", quantity: 1, price: 812.0 },
        { product_id: "AL-STAY-108", title: "ACME Ryokan Higashiyama Annex", quantity: 3, price: 358.0 },
      ],
      total: 1886.0,
      currency: "USD",
      estimated_delivery: "2026-11-06",
      tracking_url: "https://trips.example.com/AL-30418",
    },
  },
} as const;

export const SHOWCASE_PRODUCT_INDEX: Record<string, Product> = Object.fromEntries(
  [
    GUESTHOUSE_ALFAMA,
    ROOFTOP_SUITES,
    FADO_WALK,
    TILE_WORKSHOP,
    SINTRA_TRIP,
    FLIGHT_ECONOMY,
    FLIGHT_PREMIUM,
  ].map((product) => [product.product_id, product]),
);
