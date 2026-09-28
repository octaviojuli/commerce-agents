// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import { Archivo, Fraunces } from "next/font/google";
import "./globals.css";

// Display face: wordmark and view titles. Data and tables stay in the body face; Chinese text
// falls through to the CJK faces globals.css lists after each.
const fraunces = Fraunces({
  subsets: ["latin"],
  style: ["normal", "italic"],
  axes: ["opsz"],
  variable: "--font-display",
  display: "swap",
});

const archivo = Archivo({
  subsets: ["latin"],
  variable: "--font-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ACME Travel 供应商工作台",
  description: "ACME Travel 的后台工作台，商户智能体的示例。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${fraunces.variable} ${archivo.variable}`}>
      <body>
        {children}
      </body>
    </html>
  );
}
