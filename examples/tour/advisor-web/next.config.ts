import type { NextConfig } from "next";
import path from "node:path";

// The browser talks only to this origin; /api is forwarded to the advisor service.
const advisor = process.env.ADVISOR_API_URL ?? "http://127.0.0.1:8006";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: process.env.ADVISOR_STANDALONE === "1" ? "standalone" : undefined,
  outputFileTracingRoot: path.join(__dirname, "../.."),
  distDir: process.env.ADVISOR_NEXT_DIST_DIR ?? ".next",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${advisor}/api/:path*` }];
  },
};

export default nextConfig;
