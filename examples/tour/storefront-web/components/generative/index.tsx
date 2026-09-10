// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * Maps a `ui` event's component key to a card, and the one key the workbench mints itself: the
 * divider `lib/sessions.ts` puts under a resumed 历史会话.
 */

import { type GenerativeBlockProps, UnknownBlock } from "web-shared";
import { HISTORY_MARK } from "@/lib/sessions";
import type {
  CheckoutPayload,
  ComparisonPayload,
  FocusPayload,
  GuidePayload,
  ItineraryPayload,
  OrderStatusPayload,
  PlanPayload,
  ProductsPayload,
  ShortlistPayload,
} from "@/lib/types";
import BookingStatusCard from "./BookingStatusCard";
import ComparisonSpread from "./ComparisonSpread";
import FocusCard from "./FocusCard";
import HistoryMark from "./HistoryMark";
import ItineraryTimeline from "./ItineraryTimeline";
import PlanChecklist from "./PlanChecklist";
import PolicyCard from "./PolicyCard";
import QuoteSheet from "./QuoteSheet";
import RouteCarousel from "./RouteCarousel";
import ShortlistCard from "./ShortlistCard";

export default function GenerativeBlock({ block, status }: GenerativeBlockProps) {
  const partial = status !== "final";
  const payload = block.payload;
  switch (block.component) {
    case "products":
      return <RouteCarousel payload={payload as ProductsPayload} partial={partial} />;
    case "shortlist":
      return <ShortlistCard payload={payload as ShortlistPayload} partial={partial} />;
    case "focus":
      if (partial) return null;
      return <FocusCard payload={payload as FocusPayload} />;
    case "comparison":
      return <ComparisonSpread payload={payload as ComparisonPayload} partial={partial} />;
    case "itinerary":
      return <ItineraryTimeline payload={payload as ItineraryPayload} partial={partial} />;
    case "plan":
      return <PlanChecklist payload={payload as PlanPayload} partial={partial} />;
    case "guide":
      return <PolicyCard payload={payload as GuidePayload} />;
    case "order_status":
      if (partial) return null;
      return <BookingStatusCard payload={payload as OrderStatusPayload} />;
    case HISTORY_MARK:
      return <HistoryMark />;
    case "checkout":
      if (partial) return null;
      return <QuoteSheet payload={payload as CheckoutPayload} />;
    default:
      return partial ? null : <UnknownBlock component={block.component} />;
  }
}
