// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** Maps a `ui` event's component key to a card. */

import { type GenerativeBlockProps, UnknownBlock } from "web-shared";
import type {
  CheckoutPayload,
  ComparisonPayload,
  GuidePayload,
  OrderStatusPayload,
  PlanPayload,
  ProductsPayload,
} from "@/lib/types";
import BookingStatusCard from "./BookingStatusCard";
import ComparisonSpread from "./ComparisonSpread";
import PlanChecklist from "./PlanChecklist";
import PolicyCard from "./PolicyCard";
import QuoteSheet from "./QuoteSheet";
import RouteCarousel from "./RouteCarousel";

export default function GenerativeBlock({ block, status }: GenerativeBlockProps) {
  const partial = status !== "final";
  const payload = block.payload;
  switch (block.component) {
    case "products":
      return <RouteCarousel payload={payload as ProductsPayload} partial={partial} />;
    case "comparison":
      return <ComparisonSpread payload={payload as ComparisonPayload} partial={partial} />;
    case "plan":
      return <PlanChecklist payload={payload as PlanPayload} partial={partial} />;
    case "guide":
      return <PolicyCard payload={payload as GuidePayload} />;
    case "order_status":
      if (partial) return null;
      return <BookingStatusCard payload={payload as OrderStatusPayload} />;
    case "checkout":
      if (partial) return null;
      return <QuoteSheet payload={payload as CheckoutPayload} />;
    default:
      return partial ? null : <UnknownBlock component={block.component} />;
  }
}
