// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `present_attachments`: the 行程附件 the advisor asked for, one row per 线路, each a download
 * under the name the agency gave the file. The bytes are fetched on the tap with the session
 * header and saved through a blob link; the row says what happened until the next tap.
 */

import { useState } from "react";
import { api } from "@/lib/api";
import { downloadAttachment } from "@/lib/attachments";
import type { AttachmentsPayload } from "@/lib/types";

type Item = AttachmentsPayload["items"][number];

function Row({ item }: { item: Item }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "failed">("idle");
  const [detail, setDetail] = useState("");
  const label =
    state === "busy" ? "下载中…" : state === "done" ? "已下载" : state === "failed" ? "重试" : "下载";
  return (
    <li className="ac-reveal flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 border-t border-dashed border-(--line) py-3 first:border-t-0 first:pt-0">
      <div className="min-w-0 flex-1">
        <div className="truncate text-[14px] font-semibold text-(--ink-2)">{item.title}</div>
        <div className="mt-0.5 truncate text-[12.5px] leading-snug text-(--ink-soft)" title={item.name}>
          {item.name}
        </div>
        {detail ? <div className="mt-0.5 text-[12px] text-(--danger,#b3261e)">{detail}</div> : null}
      </div>
      <button
        type="button"
        className="chip shrink-0"
        disabled={state === "busy"}
        onClick={async () => {
          setState("busy");
          const result = await downloadAttachment(api, item.product_id, item.name);
          if (result.ok) {
            setState("done");
            setDetail("");
          } else {
            setState("failed");
            setDetail(result.detail);
          }
        }}
      >
        <span aria-hidden>↓</span> {label}
        {item.extension ? <span className="tg-label ml-1 uppercase">{item.extension}</span> : null}
      </button>
    </li>
  );
}

export default function AttachmentsCard({ payload }: { payload: AttachmentsPayload }) {
  const items = payload.items ?? [];
  return (
    <section className="tg-card ac-reveal p-5">
      <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">行程附件</h3>
      {payload.note ? (
        <p className="mt-1 text-[14px] leading-relaxed text-(--ink-2)">{payload.note}</p>
      ) : null}
      <ul className="mt-3 flex flex-col">
        {items.map((item) => (
          <Row key={item.product_id} item={item} />
        ))}
      </ul>
    </section>
  );
}
