// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * 历史会话: the app bar's button and the drawer it opens — the advisor's conversations, newest
 * first, with the open one marked and 新会话 above them. `StoreShell` has one panel slot and the
 * 占位 holds it, so this drawer is the workbench's own: it comes in from the left, under its
 * button, and closes on Escape, on the ground beside it, or on a move. A move waits for a reply
 * to finish, because the reply lands in the session it was sent in.
 */

import { useEffect, useRef } from "react";
import { Icon, IconButton } from "web-shared";
import { sessionTimeLabel } from "@/lib/format";
import type { AdvisorSession } from "@/lib/sessions";
import type { SessionSummary } from "@/lib/types";

const TITLE = "历史会话";

export function SessionButton({ onOpen }: { onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={TITLE}
      title={TITLE}
      className="flex items-center gap-1.5 rounded-[9px] px-2 py-1.5 text-[13.5px] font-medium text-(--ink-2) transition-colors hover:bg-(--well)"
    >
      <Icon name="clock" size={16} className="text-(--ink-soft)" />
      <span className="hidden sm:inline">历史</span>
    </button>
  );
}

function SessionRow({
  item,
  current,
  disabled,
  onPick,
}: {
  item: SessionSummary;
  current: boolean;
  disabled: boolean;
  onPick: () => void;
}) {
  const when = sessionTimeLabel(item.updated_at);
  return (
    <button
      type="button"
      onClick={onPick}
      disabled={disabled || current}
      aria-current={current ? "true" : undefined}
      className={`w-full rounded-[10px] px-3 py-2.5 text-left transition-colors ${
        current ? "bg-(--accent-soft)" : "hover:bg-(--well) disabled:opacity-50"
      }`}
    >
      <span className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate text-[14px] font-semibold leading-snug text-(--ink)">
          {item.title || "未命名会话"}
        </span>
        {current ? (
          <span className="shrink-0 rounded-full bg-(--card) px-1.5 py-0.5 text-[11px] font-semibold text-(--accent-ink)">
            当前
          </span>
        ) : null}
      </span>
      <span className="mt-0.5 flex items-center gap-1.5 text-[12px] text-(--ink-soft)">
        {when ? <span className="tg-num">{when}</span> : null}
        {when ? <span aria-hidden>·</span> : null}
        <span className="tg-num">{item.message_count} 条</span>
      </span>
    </button>
  );
}

export function SessionDrawer({
  session,
  open,
  onClose,
  busy,
}: {
  session: AdvisorSession;
  open: boolean;
  onClose: () => void;
  /** A reply is streaming, so no move is offered. */
  busy: boolean;
}) {
  const drawerRef = useRef<HTMLElement>(null);

  // The drawer takes focus when it opens, gives it back when it closes, and closes on Escape.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    drawerRef.current?.querySelector<HTMLElement>("button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  const held = busy || session.switching;
  const move = (run: () => void) => () => {
    run();
    onClose();
  };
  return (
    <>
      <div onClick={onClose} aria-hidden className="fixed inset-0 z-40 bg-black/35" />
      <aside
        ref={drawerRef}
        aria-label={TITLE}
        className="fixed inset-y-0 left-0 z-50 flex w-[min(92vw,340px)] flex-col border-r border-(--line) bg-(--card) shadow-2xl"
      >
        <div className="flex items-center gap-2 border-b border-(--line) px-[18px] py-3.5">
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-(--ink)">{TITLE}</h2>
          <IconButton icon="x" label={`关闭${TITLE}`} onClick={onClose} className="ml-auto" />
        </div>
        <div className="border-b border-(--line) px-[18px] py-3">
          <button
            type="button"
            disabled={held}
            onClick={move(session.startNew)}
            className="btn-primary flex w-full items-center justify-center gap-1.5"
          >
            <Icon name="edit" size={15} />
            新会话
          </button>
          {busy ? (
            <p className="mt-2 text-[12px] leading-snug text-(--ink-soft)">
              这一条回复结束后可以换会话。
            </p>
          ) : null}
        </div>
        <div className="panel-scroll min-h-0 flex-1 overflow-y-auto px-2.5 py-2.5">
          {session.sessions === null ? (
            <p className="px-3 py-6 text-center text-[13px] text-(--ink-soft)">读取中…</p>
          ) : session.sessions.length === 0 ? (
            <p className="px-3 py-6 text-center text-[13px] leading-relaxed text-(--ink-soft)">
              还没有历史会话。这次问过的团期，下次进来就在这里。
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {session.sessions.map((item) => (
                <li key={item.session_id}>
                  <SessionRow
                    item={item}
                    current={item.session_id === session.sessionId}
                    disabled={held}
                    onPick={move(() => session.resume(item.session_id))}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}
