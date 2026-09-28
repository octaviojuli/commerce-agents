// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: process.env.TOUR_STANDALONE === "1" ? "standalone" : undefined,
  outputFileTracingRoot: path.join(__dirname, "../.."),
  distDir: process.env.TOUR_NEXT_DIST_DIR ?? ".next",
  transpilePackages: ["web-shared"],
};

export default nextConfig;
