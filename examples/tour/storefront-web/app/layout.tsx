// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { Metadata, Viewport } from "next";
import { ASSISTANT, BRAND } from "@/lib/brand";
import "./globals.css";

// The workbench is Chinese throughout, so the type comes from the system CJK stack in
// globals.css rather than a Latin web font.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
};

export function generateMetadata(): Metadata {
  return process.env.TOUR_BACKEND_MODE === "warehouse"
    ? {
        title: `${BRAND} · 云仓顾问`,
        description: "查询已授权线路、团期库存与采购报价。",
      }
    : {
        title: `${BRAND} · ${ASSISTANT}`,
        description: "门店顾问的选团工作台：查线路、开团期、占位。",
      };
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
