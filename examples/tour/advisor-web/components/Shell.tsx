"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import DealList from "./DealList";
import MemoryView from "./MemoryView";
import { Toasts } from "./ui";

// Mobile shows one screen at a time. From 1024 px the deal list and the deal's memory
// stay beside the current screen.
export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const bare = path.startsWith("/login") || path.startsWith("/p/");
  const deal = path.match(/^\/deals\/([0-9a-f-]{36})/)?.[1];
  if (bare)
    return (
      <>
        {children}
        <Toasts />
      </>
    );
  return (
    <div className={"shell" + (deal ? "" : " two")}>
      <aside className="side" aria-label="我的跟单">
        <DealList compact />
      </aside>
      <div style={{ minWidth: 0 }}>{children}</div>
      {deal && (
        <aside className="side right" aria-label="这一单记住的事">
          <MemoryView id={deal} compact />
        </aside>
      )}
      <Toasts />
    </div>
  );
}
