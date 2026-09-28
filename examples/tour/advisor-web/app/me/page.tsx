"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Supplier, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import type { SupplierView } from "@/lib/types";

export default function MePage() {
  const router = useRouter();
  const [me, setMe] = useState<{ name: string; org: string } | null>(null);
  const [marks, setMarks] = useState<SupplierView[]>([]);
  useEffect(() => {
    api.get<{ items: SupplierView[] }>("/suppliers").then((r) => setMarks(r.items.filter((s) => s.stance || s.note)));
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
          <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8, margin: "12px 0" }}>
            <b>我的供应商标记</b>
            {!marks.length && <span className="lbl">在线路详情里给供应商标“常用 / 慎用”，写一句自己的备注。只有你自己看得到。</span>}
            {marks.map((s) => (
              <Supplier key={s.id} s={s} note />
            ))}
            <Link className="linkish" href="/routes">
              去线路里看 ›
            </Link>
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
