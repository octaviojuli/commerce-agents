// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi } from "web-shared";
import type {
  AlertsResponse,
  ListingDetailResponse,
  ListingsResponse,
  OccupancyOverviewResponse,
  OverviewResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export const api = new AgentApi(API_URL, "/api/merchant");

export const UNREACHABLE =
  "无法连接 8001 端口的 travel API。请先运行 " +
  "`uvicorn travel.api.main:app --app-dir examples --port 8001`，再重试。";

export function fetchOverview(): Promise<OverviewResponse | null> {
  return api.get<OverviewResponse>("/overview");
}

export function fetchOccupancy(): Promise<OccupancyOverviewResponse | null> {
  return api.get<OccupancyOverviewResponse>("/occupancy");
}

export function fetchListings(query?: string): Promise<ListingsResponse | null> {
  return api.get<ListingsResponse>("/listings", query ? { query } : undefined);
}

export function fetchListingDetail(listingId: string): Promise<ListingDetailResponse | null> {
  return api.get<ListingDetailResponse>(`/listings/${encodeURIComponent(listingId)}`);
}

export function fetchAlerts(): Promise<AlertsResponse | null> {
  return api.get<AlertsResponse>("/alerts");
}
