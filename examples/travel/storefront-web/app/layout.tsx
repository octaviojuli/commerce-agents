// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import { Archivo, Fraunces } from "next/font/google";
import "./globals.css";

// Display face: headlines, day numerals, prices, postcard city names. Chinese text falls through
// to the CJK faces globals.css lists after it.
const fraunces = Fraunces({
  subsets: ["latin"],
  style: ["normal", "italic"],
  axes: ["opsz"],
  variable: "--font-display",
  display: "swap",
});

// Body face: copy, buttons, metadata lines.
const archivo = Archivo({
  subsets: ["latin"],
  variable: "--font-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ACME Travel",
  description: "与 ACME 助手一起规划并预订旅行。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${fraunces.variable} ${archivo.variable}`}>
      <body>
        {/* Grain at z-1; content above it at z-2. */}
        <div className="al-grain" aria-hidden />
        <div className="relative z-[2] h-full">
          {children}
        </div>
      </body>
    </html>
  );
}
