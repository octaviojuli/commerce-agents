"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Tabs } from "@/components/ui";
import { api } from "@/lib/api";

export default function MePage() {
  const router = useRouter();
  const [me, setMe] = useState<{ name: string; org: string } | null>(null);
  useEffect(() => {
    api.get<{ name: string; org: string }>("/me").then(setMe);
  }, []);
  return (
    <div className="app">
      <div className="top">
        <div className="tt">
          <b style={{ fontSize: 21 }}>我的</b>
          <small>{me ? `${me.name} · ${me.org}` : ""}</small>
        </div>
      </div>
      <div className="sc">
        <div className="pad">
          <div className="card">
            <div className="kv">
              <span>账号</span>
              <span>{me?.name}</span>
            </div>
            <div className="kv">
              <span>所属</span>
              <span>{me?.org}</span>
            </div>
          </div>
          <button
            className="b b-line"
            onClick={async () => {
              await api.post("/logout");
              router.replace("/login");
            }}
          >
            退出登录
          </button>
        </div>
      </div>
      <Tabs />
    </div>
  );
}
