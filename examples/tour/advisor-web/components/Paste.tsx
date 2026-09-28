"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DealCard } from "@/lib/types";
import { Sheet } from "./ui";

/** Paste a customer's message: start a new deal or add it to one already open. */
export default function Paste() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [target, setTarget] = useState("new");
  const [deals, setDeals] = useState<DealCard[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) api.get<{ items: DealCard[] }>("/deals").then((d) => setDeals(d.items.filter((x) => x.status !== "won")));
  }, [open]);
  return (
    <>
      <button className="paste" onClick={() => setOpen(true)} style={{ border: 0, textAlign: "left", font: "inherit" }}>
        <div className="grow">
          <b>粘贴客人消息</b>
          <small>微信里的文字复制过来，助手来整理</small>
        </div>
        <span className="go">粘贴</span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title="粘贴客人消息">
        <label className="field">
          客人原话
          <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="把客人在微信里说的话粘贴到这里" />
        </label>
        <label className="field">
          放进哪一单
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="new">新客人</option>
            {deals.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title} · {d.line}
              </option>
            ))}
          </select>
        </label>
        {target === "new" && (
          <label className="field">
            怎么称呼这位客人
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：王女士一家" />
          </label>
        )}
        {error && <div className="err">{error}</div>}
        <button
          className="b b-br"
          disabled={!text.trim() || busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              let id = target;
              if (id === "new") id = (await api.post<{ id: string }>("/deals", { title: title.trim() || "新客人" })).id;
              sessionStorage.setItem("advisor-pending-" + id, text.trim());
              router.push("/deals/" + id);
            } catch (e) {
              setError((e as Error).message);
              setBusy(false);
            }
          }}
        >
          交给助手整理
        </button>
      </Sheet>
    </>
  );
}
