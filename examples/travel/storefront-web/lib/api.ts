// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi } from "web-shared";
import type { Product } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export const api = new AgentApi(API_URL, "/api");

export const UNREACHABLE =
  "无法连接 8001 端口的 travel API。请先运行 " +
  "`uvicorn travel.api.main:app --app-dir examples --port 8001`，再重试。";

export async function fetchProducts(): Promise<Product[] | null> {
  const data = await api.get<{ products: Product[] }>("/products", { limit: "100" });
  return data?.products ?? null;
}
