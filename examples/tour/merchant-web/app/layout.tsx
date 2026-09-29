import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Tour 云仓 · 业务工作台",
  description: "统一管理旅游线路、团期、库存与供采合作",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
