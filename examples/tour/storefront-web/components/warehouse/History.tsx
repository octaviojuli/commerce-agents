"use client";

import { useEffect, useRef, useState } from "react";
import type { AgentTurn, AssistantSegment, ChatItem } from "web-shared";
import { message, type WarehouseEvent } from "web-shared/warehouse-client";
import { stamp, type Page, type WarehouseAgentApi } from "@/lib/warehouse";

type Summary = {
  id: string;
  title: string | null;
  resumable: boolean;
  created_at: string;
  offline_status?: string;
  shared?: boolean;
  stage?: string;
};
type SavedTurn = {
  id: string;
  message: string;
  status: "complete" | "running" | "interrupted";
  events: WarehouseEvent[];
};
type Transcript = {
  turns: SavedTurn[];
  next_cursor: string | null;
  busy: boolean;
};

function replay(turns: SavedTurn[], nextTurn: () => number): ChatItem[] {
  return turns.flatMap((turn): ChatItem[] => {
    const segments: AssistantSegment[] = [];
    // Only committed public text and fully enriched cards are replayed. No tool
    // arguments, internal model messages, partial cards or old action suggestions.
    for (const event of turn.events) {
      if (event.type === "text_delta") {
        const text = String(event.data.text ?? "");
        const last = segments.at(-1);
        if (last?.type === "text") last.text += text;
        else segments.push({ type: "text", text });
      } else if (
        event.type === "ui" &&
        event.data.component !== "suggestions"
      ) {
        segments.push({
          type: "ui",
          status: "final",
          slotKey: `${turn.id}-${segments.length}`,
          block: {
            component: String(event.data.component),
            payload: event.data.payload,
          },
        });
      }
    }
    if (turn.status !== "complete")
      segments.push({
        type: "error",
        text:
          turn.status === "running"
            ? "正在处理，恢复后将显示完整回答。"
            : "本轮已中断，没有保存完整回答。请重新发起查询。",
      });
    return [
      { kind: "user", text: turn.message },
      {
        kind: "assistant",
        turn: nextTurn(),
        segments,
        suggestions: [],
        pending: false,
        tools: [],
      },
    ];
  });
}

export function useWarehouseHistory(agent: WarehouseAgentApi, chat: AgentTurn) {
  const [page, setPage] = useState<Page<Summary>>({
    items: [],
    next_cursor: null,
  });
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [listing, setListing] = useState(false);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const negative = useRef(0);
  const [query, setQuery] = useState("");
  const [recovering, setRecovering] = useState(false);
  const [revision, setRevision] = useState(0);
  const recovery = useRef<AbortController | null>(null);
  const latest = useRef({ chat });
  latest.current = { chat };
  async function recover() {
    const id = agent.conversation;
    if (!id) return;
    if (latest.current.chat.busy) {
      agent.dispose();
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    recovery.current?.abort();
    const cancel = new AbortController();
    recovery.current = cancel;
    setRecovering(true);
    setError("");
    const deadline = Date.now() + 90000;
    try {
      do {
        const result = await agent.warehouse.get<Transcript>(
          `/conversations/${id}`,
          cancel.signal,
        );
        if (cancel.signal.aborted || agent.conversation !== id) return;
        setRemoteBusy(result.busy);
        setRevision((n) => n + 1);
        if (!latest.current.chat.busy) {
          chat.setItems(replay(result.turns, () => --negative.current));
          setCursor(result.next_cursor);
        }
        if (!result.busy) {
          setRefresh((n) => n + 1);
          return;
        }
        await new Promise<void>((resolve) => {
          const t = setTimeout(resolve, 2000);
          cancel.signal.addEventListener(
            "abort",
            () => {
              clearTimeout(t);
              resolve();
            },
            { once: true },
          );
        });
      } while (!cancel.signal.aborted && Date.now() < deadline);
    } catch (e) {
      if (!cancel.signal.aborted) setError(message(e));
    } finally {
      if (!cancel.signal.aborted) setRecovering(false);
    }
  }
  const recoverRef = useRef(recover);
  recoverRef.current = recover;
  useEffect(() => {
    const visible = () => {
      if (document.visibilityState === "visible") void recoverRef.current();
      else agent.dispose();
    };
    const online = () => void recoverRef.current();
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("online", online);
    return () => {
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("online", online);
      recovery.current?.abort();
    };
  }, [agent]);
  useEffect(() => {
    if (remoteBusy) void recoverRef.current();
  }, [remoteBusy]);
  const pending = useRef<AbortController | null>(null);
  const listPending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    const cancel = new AbortController();
    listPending.current?.abort();
    listPending.current = cancel;
    setListing(true);
    agent.warehouse
      .get<Page<Summary>>(
        `/conversations?role=advisor&query=${encodeURIComponent(query)}`,
        cancel.signal,
      )
      .then((result) => {
        if (!cancel.signal.aborted) setPage(result);
      })
      .catch((err) => {
        if (!cancel.signal.aborted) setError(message(err));
      })
      .finally(() => {
        if (!cancel.signal.aborted) setListing(false);
      });
    return () => listPending.current?.abort();
  }, [agent, chat.completed, refresh, query]);

  async function moreConversations() {
    if (!page.next_cursor || listing) return;
    const cancel = new AbortController();
    listPending.current?.abort();
    listPending.current = cancel;
    setListing(true);
    try {
      const result = await agent.warehouse.get<Page<Summary>>(
        `/conversations?role=advisor&query=${encodeURIComponent(query)}&before=${encodeURIComponent(page.next_cursor)}`,
        cancel.signal,
      );
      if (!cancel.signal.aborted)
        setPage((old) => ({
          items: [
            ...old.items,
            ...result.items.filter(
              (item) => !old.items.some((row) => row.id === item.id),
            ),
          ],
          next_cursor: result.next_cursor,
        }));
    } catch (err) {
      if (!cancel.signal.aborted) setError(message(err));
    } finally {
      if (!cancel.signal.aborted) setListing(false);
    }
  }

  function reset() {
    recovery.current?.abort();
    setRecovering(false);
    pending.current?.abort();
    agent.conversation = "";
    chat.setItems([]);
    setCursor(null);
    setRemoteBusy(false);
    setLoading(false);
    setError("");
  }

  async function load(id: string, older = false) {
    if (chat.busy || loading) return;
    const cancel = new AbortController();
    pending.current?.abort();
    pending.current = cancel;
    setLoading(true);
    setError("");
    if (!older) {
      chat.setItems([]);
      agent.conversation = "";
      setCursor(null);
    }
    try {
      const params =
        older && cursor ? `?before=${encodeURIComponent(cursor)}` : "";
      const result = await agent.warehouse.get<Transcript>(
        `/conversations/${id}${params}`,
        cancel.signal,
      );
      if (cancel.signal.aborted) return;
      const items = replay(result.turns, () => --negative.current);
      agent.conversation = id;
      chat.setItems((old) => (older ? [...items, ...old] : items));
      setCursor(result.next_cursor);
      setRemoteBusy(result.busy);
    } catch (err) {
      if (!cancel.signal.aborted) {
        reset();
        setError(message(err));
      }
    } finally {
      if (!cancel.signal.aborted) setLoading(false);
    }
  }

  const guarded: AgentTurn = {
    ...chat,
    busy: chat.busy || loading || remoteBusy,
    send: async (text) => {
      if (!loading && !remoteBusy) await chat.send(text);
    },
  };
  return {
    page,
    cursor,
    loading,
    recovering,
    revision,
    recover,
    search: setQuery,
    listing,
    remoteBusy,
    error,
    load,
    reset,
    moreConversations,
    refresh: () => setRefresh((n) => n + 1),
    chat: guarded,
  };
}

export default function History({
  history,
  agent,
  busy,
  open,
  openArchives,
  archivesOpen,
}: {
  history: ReturnType<typeof useWarehouseHistory>;
  agent: WarehouseAgentApi;
  busy: boolean;
  open: () => void;
  openArchives: () => void;
  archivesOpen: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-(--line) bg-(--card) px-4 py-2 text-xs">
      <label className="flex min-w-0 items-center gap-2">
        我的会话
        <select
          aria-label="我的会话"
          className="min-w-0 max-w-[210px] rounded-lg border border-(--line) p-1"
          value={agent.conversation}
          disabled={busy || history.loading}
          onChange={(e) => {
            if (e.target.value) {
              open();
              void history.load(e.target.value);
            } else history.reset();
          }}
        >
          <option value="">新会话</option>
          {agent.conversation &&
            !history.page.items.some(
              (item) => item.id === agent.conversation,
            ) && <option value={agent.conversation}>当前会话</option>}
          {history.page.items.map((item) => (
            <option key={item.id} value={item.id} disabled={!item.resumable}>
              {item.resumable ? item.title || "空会话" : "授权已变化，无法恢复"}{" "}
              · {stamp(item.created_at)}
            </option>
          ))}
        </select>
      </label>
      <button
        className="chip"
        disabled={busy || history.loading}
        onClick={() => {
          history.reset();
          open();
        }}
      >
        新建会话
      </button>
      <button
        className="chip"
        aria-pressed={archivesOpen}
        onClick={openArchives}
      >
        历史档案
      </button>
      <button
        className="chip"
        disabled={history.listing}
        onClick={history.refresh}
      >
        刷新列表
      </button>
      {history.page.next_cursor && (
        <button
          className="chip"
          disabled={history.listing}
          onClick={history.moreConversations}
        >
          更多会话
        </button>
      )}
      {history.cursor && (
        <button
          className="chip"
          disabled={busy || history.loading}
          onClick={() => {
            open();
            void history.load(agent.conversation, true);
          }}
        >
          读取更早对话
        </button>
      )}
      {history.remoteBusy && (
        <button
          className="chip"
          disabled={busy || history.loading}
          onClick={() => history.load(agent.conversation)}
        >
          重新读取本会话
        </button>
      )}
      {history.loading && <span role="status">正在读取历史…</span>}
      {history.error && (
        <p role="alert" className="w-full text-(--danger)">
          {history.error}
        </p>
      )}
      <p className="w-full text-(--ink-soft)">
        历史价格、库存与文字仅供回顾，请重新询价后向客户确认。
      </p>
    </div>
  );
}
