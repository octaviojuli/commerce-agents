// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** How each kind of supplier record shows: its label, icon, and tone. */

import type { KindStyle, Tone } from "web-shared";
import type { InventoryAlert, ListingStatus, OrderIssue } from "./types";

export const ISSUE_KINDS: Record<OrderIssue["kind"], KindStyle> = {
  delayed: { label: "延误", icon: "clock", tone: "warn" },
  return_spike: { label: "取消激增", icon: "return", tone: "danger" },
  buyer_message: { label: "客人留言", icon: "message", tone: "info" },
  damaged: { label: "问题反馈", icon: "alert", tone: "danger" },
};

export const INVENTORY_KINDS: Record<InventoryAlert["kind"], KindStyle> = {
  low_stock: { label: "房量紧张", icon: "bed", tone: "warn" },
  slow_mover: { label: "进度偏慢", icon: "low", tone: "muted" },
};

export const LISTING_STATUS: Record<ListingStatus, { label: string; tone: Tone }> = {
  active: { label: "在售", tone: "ok" },
  paused: { label: "已暂停", tone: "muted" },
  draft: { label: "草稿", tone: "info" },
  out_of_stock: { label: "已售罄", tone: "danger" },
};

// The shared order pipeline's parcel statuses, translated into booking language.
export const BOOKING_STATUS: Record<string, { label: string; tone: Tone }> = {
  processing: { label: "处理中", tone: "muted" },
  shipped: { label: "已确认", tone: "info" },
  out_for_delivery: { label: "已确认", tone: "info" },
  delivered: { label: "已完成", tone: "ok" },
  delayed: { label: "已延误", tone: "warn" },
  cancelled: { label: "已取消", tone: "muted" },
  return_initiated: { label: "已申请退款", tone: "violet" },
  refunded: { label: "已退款", tone: "ok" },
};

/** The composer prompt for an availability or pacing alert; the same words on every surface. */
export function inventoryPrompt(kind: InventoryAlert["kind"], ref: string): string {
  return kind === "low_stock" ? `${ref} 的房量快售完了，我有哪些选择？` : `${ref} 的预订进度偏慢，帮我规划一下房价。`;
}

/** "sells out in ~4 days" for a tight property; the shared `runwayLabel` says it in English. */
export function runwayText(days: number): string {
  return days < 1 ? "一天内售罄" : `约 ${Math.round(days)} 天内售罄`;
}
