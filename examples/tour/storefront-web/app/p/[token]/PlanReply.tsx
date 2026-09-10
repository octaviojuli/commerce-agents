// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The customer's answer to one version of a 定制方案: they take it, or they write the one thing
 * they want changed. Either way it is a note to the advisor and nothing else — no seat is held,
 * no price is agreed, and the customer is not a user of the API, so the token stands for them.
 */

import { useState } from "react";
import { API_URL } from "@/lib/api";

type Choice = "ok" | "question";

export default function PlanReply({ token }: { token: string }) {
  const [asking, setAsking] = useState(false);
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function send(choice: Choice) {
    setBusy(true);
    setFailed(false);
    try {
      const response = await fetch(`${API_URL}/api/share/plan/${token}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(choice === "question" ? { choice, text } : { choice }),
      });
      if (!response.ok) throw new Error(String(response.status));
      setSent(true);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <p className="rounded-(--radius) bg-(--ok-soft) px-4 py-3 text-center text-[14.5px] font-semibold text-(--ok)">
        已发送给顾问
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      {asking ? (
        <label className="flex flex-col gap-1.5">
          <span className="tg-label">想改哪里？一句话就行</span>
          <textarea
            autoFocus
            rows={1}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="例如：第 5 天想留在草原，不要赶路。"
            className="w-full resize-none rounded-(--radius) border border-(--line-strong) bg-(--card) px-3 py-2.5 text-[15px] leading-relaxed text-(--ink)"
          />
        </label>
      ) : null}
      <div className="flex flex-col gap-2 sm:flex-row">
        {asking ? (
          <>
            <button
              type="button"
              disabled={busy || !text.trim()}
              onClick={() => void send("question")}
              className="btn-primary flex-1"
            >
              {busy ? "发送中…" : "发送给顾问"}
            </button>
            <button type="button" onClick={() => setAsking(false)} className="chip py-2.5">
              取消
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => void send("ok")}
              className="btn-primary flex-1"
            >
              {busy ? "发送中…" : "确认这个方案"}
            </button>
            <button type="button" onClick={() => setAsking(true)} className="chip py-2.5">
              我有问题
            </button>
          </>
        )}
      </div>
      {failed ? (
        <p className="text-center text-[13px] text-(--danger)">没发出去，请稍后再试一次。</p>
      ) : null}
    </div>
  );
}
