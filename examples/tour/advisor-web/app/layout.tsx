import type { Metadata, Viewport } from "next";
import Shell from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "顾问搭档 · ACME 旅行",
  description: "旅游顾问的幕后搭档：记住、对比、核对、起草。",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
