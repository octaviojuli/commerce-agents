// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { formatMoney } from "web-shared";
import type { Product } from "./types";

const DESTINATION_GRADIENTS: Record<string, [string, string]> = {
  lisbon: ["#F4D9CC", "#C9D8D3"],
  kyoto: ["#E8D5D0", "#D8E0D6"],
  "mexico city": ["#F4DFC0", "#E5C8B8"],
  reykjavik: ["#CBD8DF", "#E6EAE6"],
  marrakesh: ["#EFD2B8", "#E0B9A0"],
  queenstown: ["#CFE0D8", "#DCE8E2"],
};

const GRADIENT_FALLBACKS = Object.values(DESTINATION_GRADIENTS);

function hash(text: string): number {
  let value = 0;
  for (let i = 0; i < text.length; i++) {
    value = (value * 31 + text.charCodeAt(i)) >>> 0;
  }
  return value;
}

/** The seed keeps two items in one unmapped place from sharing a gradient. */
export function destinationGradientCss(city?: string | null, seed?: string): string {
  const key = city?.trim().toLowerCase();
  const [from, to] =
    (key && DESTINATION_GRADIENTS[key]) ||
    GRADIENT_FALLBACKS[hash(`${key ?? ""}·${seed ?? "acme-travel"}`) % GRADIENT_FALLBACKS.length];
  return `linear-gradient(135deg, ${from}, ${to})`;
}

export function productCity(product: Product): string | undefined {
  return product.attributes?.city ?? product.attributes?.destination_city ?? undefined;
}

/** Neighborhood before city, so cards in a one-city flow stay distinct. */
export function productPlace(product: Product): string | undefined {
  return product.attributes?.neighborhood ?? productCity(product);
}

// per_traveler folds into "/ 人" so mixed data renders one unit.
const PRICE_UNIT_LABELS: Record<string, string> = {
  per_night: "/ 晚",
  per_person: "/ 人",
  per_traveler: "/ 人",
};

export function priceUnitLabel(unit?: string | null): string | null {
  if (!unit) return null;
  return PRICE_UNIT_LABELS[unit] ?? `/ ${unit.replace(/^per[_\s]+/, "").replace(/_/g, " ")}`;
}

export function productPriceUnit(product: Product): string | null {
  return priceUnitLabel(product.attributes?.price_unit);
}

export function formatPrice(value: number): string {
  return formatMoney(value, "USD", { whole: Number.isInteger(value) });
}

/** "2026-10-13" → "10月13日", from the string parts so no timezone shifts the day. */
export function shortDate(iso?: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!match) return null;
  const month = Number(match[2]);
  return month >= 1 && month <= 12 ? `${month}月${Number(match[3])}日` : null;
}

/** Stays count nights, experiences and flights count people. */
function quantityNoun(productId: string): "晚" | "人" | null {
  if (productId.startsWith("AL-STAY-")) return "晚";
  if (productId.startsWith("AL-EXP-") || productId.startsWith("AL-FLT-")) return "人";
  return null;
}

export function quantityLabel(productId: string, quantity: number): string {
  const noun = quantityNoun(productId);
  if (!noun) return `× ${quantity}`;
  return `× ${quantity} ${noun}`;
}
