// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/** A trip is an order here: these are travel's words for the shared order pieces. */

import { isOpen, type Order, ORDER_NOUNS, type OrderNouns, useCatalogIndex } from "web-shared";
import { PostcardWindow } from "@/components/PostcardWindow";
import { fetchProducts } from "./api";
import { productCity } from "./format";

export const NOUNS: OrderNouns = {
  ...ORDER_NOUNS,
  one: "次行程",
  many: "次行程",
  title: "我的行程",
  cardTitle: "即将出发",
  noneOpen: "暂无即将出发的行程",
  openVerb: "出发",
  closedWhen: (_order, date) => `${date} 的行程`,
  statusLabels: { processing: "确认中", shipped: "已确认", out_for_delivery: "已确认", delivered: "已完成", return_initiated: "已申请退款" },
  filters: [
    { id: "open", label: "即将出发", match: isOpen },
    { id: "closed", label: "过往", match: (order) => !isOpen(order) },
  ],
  handoff(order) {
    const ref = `行程 ${order.order_id}`;
    if (isOpen(order)) return { label: "问一问", prompt: `我的${ref}现在是什么状态？` };
    if (order.status === "delivered") return { label: "再来一次", prompt: `按${ref}的样子再规划一次旅行。` };
    return { label: "问一问", prompt: `${ref}后来怎么样了？` };
  },
};

/** The trip's destination: the city of its first stay, experience, or flight. */
export function TripThumb({ order }: { order: Order }) {
  const catalog = useCatalogIndex(fetchProducts);
  const product = order.items.map((item) => catalog[item.product_id]).find(Boolean);
  const city = product ? productCity(product) : undefined;
  return <PostcardWindow city={city} title={order.items[0]?.title ?? order.order_id} className="h-[42px] w-[60px] shrink-0" />;
}
