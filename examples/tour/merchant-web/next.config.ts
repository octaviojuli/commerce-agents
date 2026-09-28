import type { NextConfig } from "next";
import path from "node:path";
const config: NextConfig = {
  reactStrictMode: true,
  output: process.env.TOUR_STANDALONE === "1" ? "standalone" : undefined,
  outputFileTracingRoot: path.join(__dirname, "../.."),
  distDir: process.env.TOUR_NEXT_DIST_DIR ?? ".next",
  transpilePackages: ["web-shared"],
};
export default config;
