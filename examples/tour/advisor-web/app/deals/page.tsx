"use client";

import DealList from "@/components/DealList";
import Paste from "@/components/Paste";
import { Tabs } from "@/components/ui";

export default function DealsPage() {
  return (
    <div className="app">
      <div className="top">
        <div className="tt">
          <b style={{ fontSize: 21 }}>跟单</b>
          <small>一位客人就是一单</small>
        </div>
      </div>
      <DealList />
      <Paste />
      <Tabs />
    </div>
  );
}
