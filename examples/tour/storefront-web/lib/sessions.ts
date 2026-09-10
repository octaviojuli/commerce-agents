// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * 历史会话: the advisor's own conversations on this API, and which one the workbench is in.
 * `GET /api/sessions` lists them newest first and `GET /api/sessions/{id}/messages` returns one
 * transcript; the API authorizes both by advisor, so a session is read before the workbench
 * moves into it. Resuming is nothing more than sending that id in the session header, which the
 * server continues — there is no other call for it.
 *
 * A load still starts a session first, because `POST /api/session` is the one route that names
 * the advisor, and the remembered id is adopted only when that advisor's own list holds it: a
 * browser two advisors share never opens the other's conversation. The started session is left
 * unused on a resume, and nothing was ever said in it, which is what `spoken` keeps out of the
 * panel's list.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentApi, ChatItem } from "web-shared";
import type { SessionMessage, SessionSummary } from "./types";

/** The last session this browser was in, per advisor. */
const REMEMBERED = "acme-tour.session";

interface Remembered {
  session_id: string;
  user_id: string;
}

interface Started {
  sessionId: string;
  /** The advisor the API signed this session in as. */
  userId: string;
  shopper?: Shopper;
}

type Shopper = { name: string; tier?: string };

function readRemembered(): Remembered | null {
  try {
    const raw = window.localStorage.getItem(REMEMBERED);
    const value = raw ? (JSON.parse(raw) as Partial<Remembered>) : null;
    if (!value?.session_id) return null;
    return { session_id: value.session_id, user_id: String(value.user_id ?? "") };
  } catch {
    return null;
  }
}

function remember(value: Remembered): void {
  try {
    window.localStorage.setItem(REMEMBERED, JSON.stringify(value));
  } catch {
    // A browser that refuses storage simply does not resume; the workbench is unaffected.
  }
}

/**
 * The shared client keeps the session token but not the advisor it belongs to, so the start
 * route is read here for the `user_id` the resume check needs.
 */
async function startSession(api: AgentApi): Promise<Started | null> {
  const data = await api.post<{
    session_id: string;
    user_id?: string;
    name?: string | null;
    tier?: string | null;
  }>("/session");
  if (!data?.session_id) return null;
  return {
    sessionId: data.session_id,
    userId: String(data.user_id ?? ""),
    shopper: data.name ? { name: data.name, tier: data.tier ?? undefined } : undefined,
  };
}

async function fetchSessions(api: AgentApi): Promise<SessionSummary[] | null> {
  const data = await api.get<{ sessions: SessionSummary[] }>("/sessions");
  return data?.sessions ?? null;
}

async function fetchMessages(api: AgentApi, sessionId: string): Promise<SessionMessage[] | null> {
  const path = `/sessions/${encodeURIComponent(sessionId)}/messages`;
  const data = await api.get<{ messages: SessionMessage[] }>(path);
  return data?.messages ?? null;
}

/** Hides what a load abandoned: a session nobody spoke in that is not the open one. */
function spoken(sessions: SessionSummary[], current: string | null): SessionSummary[] {
  return sessions.filter((item) => item.message_count > 0 || item.session_id === current);
}

export interface Resumed {
  sessionId: string;
  /** The session's earlier messages; empty in one this browser just started. */
  messages: SessionMessage[];
}

export interface AdvisorSession {
  sessionId: string | null;
  /** The signed-in advisor; the same person in every one of these sessions. */
  shopper?: Shopper;
  /** The advisor's sessions, newest first; null until the first read. */
  sessions: SessionSummary[] | null;
  /** A new object per move, which is what the transcript replays. */
  resumed: Resumed | null;
  /** A move is in flight. */
  switching: boolean;
  resume: (sessionId: string) => void;
  startNew: () => void;
  refresh: () => void;
}

/**
 * The workbench's session, in place of `useSession`: the one it resumes or the one it starts,
 * the list behind the 历史会话 panel, and the two moves the panel offers. Only the newest move
 * installs its token, as with the shared hook.
 */
export function useAdvisorSession(api: AgentApi): AdvisorSession {
  const [session, setSession] = useState<{ sessionId: string | null; shopper?: Shopper }>({
    sessionId: null,
  });
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [resumed, setResumed] = useState<Resumed | null>(null);
  const [switching, setSwitching] = useState(false);
  const advisor = useRef("");
  const move = useRef(0);

  const refresh = useCallback(() => {
    void fetchSessions(api).then((listed) => {
      if (listed) setSessions(listed);
    });
  }, [api]);

  /** Moves in: the token on the client, the id in the browser, the transcript on the page. */
  const install = useCallback(
    (sessionId: string, messages: SessionMessage[], shopper?: Shopper) => {
      api.session = sessionId;
      remember({ session_id: sessionId, user_id: advisor.current });
      setSession((previous) => ({ sessionId, shopper: shopper ?? previous.shopper }));
      setResumed({ sessionId, messages });
    },
    [api],
  );

  const startNew = useCallback(() => {
    const mine = ++move.current;
    setSwitching(true);
    void (async () => {
      const started = await startSession(api);
      if (move.current !== mine) return;
      if (started) {
        advisor.current = started.userId;
        install(started.sessionId, [], started.shopper);
      }
      setSwitching(false);
      refresh();
    })();
  }, [api, install, refresh]);

  // The messages are read before the token moves, so a message sent meanwhile still belongs to
  // the conversation on the page.
  const resume = useCallback(
    (sessionId: string) => {
      const mine = ++move.current;
      setSwitching(true);
      void (async () => {
        const messages = await fetchMessages(api, sessionId);
        if (move.current !== mine) return;
        // A session the API no longer has stays closed; the list says so on the next read.
        if (messages) install(sessionId, messages);
        setSwitching(false);
        refresh();
      })();
    },
    [api, install, refresh],
  );

  useEffect(() => {
    const mine = ++move.current;
    void (async () => {
      const started = await startSession(api);
      if (move.current !== mine || !started) return;
      advisor.current = started.userId;
      api.session = started.sessionId;
      const remembered = readRemembered();
      const candidate =
        remembered && remembered.user_id === started.userId ? remembered.session_id : null;
      // The list is read through the session just started, so what it names is this advisor's.
      const listed = candidate ? await fetchSessions(api) : null;
      if (move.current !== mine) return;
      if (listed) setSessions(listed);
      const confirmed = listed?.some((item) => item.session_id === candidate) ? candidate : null;
      const messages = confirmed ? await fetchMessages(api, confirmed) : null;
      if (move.current !== mine) return;
      if (confirmed && messages) install(confirmed, messages, started.shopper);
      else install(started.sessionId, [], started.shopper);
    })();
    return () => {
      move.current += 1;
    };
  }, [api, install]);

  return {
    ...session,
    sessions: sessions && spoken(sessions, session.sessionId),
    resumed,
    switching,
    resume,
    startNew,
    refresh,
  };
}

/** The 历史记录 divider, a component key the workbench mints rather than the server. */
export const HISTORY_MARK = "history_mark";

const MARK: ChatItem = {
  kind: "assistant",
  turn: 0,
  segments: [
    {
      type: "ui",
      block: { component: HISTORY_MARK, payload: {} },
      slotKey: HISTORY_MARK,
      status: "final",
    },
  ],
  suggestions: [],
  pending: false,
  tools: [],
};

/**
 * A resumed session's messages as transcript items, above the live conversation: the advisor's
 * lines as bubbles, the assistant's as prose, and the divider under the last of them. The cards
 * a reply streamed are not in the transcript the server keeps, so none is replayed and the block
 * reads as the record of what was said. The turn numbers are outside the range a live turn
 * counts from, so nothing streaming can write into a replayed reply.
 */
export function replayItems(messages: SessionMessage[]): ChatItem[] {
  const said = messages.filter((message) => message.text.trim());
  if (!said.length) return [];
  const items: ChatItem[] = said.map((message, index) =>
    message.role === "user"
      ? { kind: "user", text: message.text }
      : {
          kind: "assistant",
          turn: -1 - index,
          segments: [{ type: "text", text: message.text }],
          suggestions: [],
          pending: false,
          tools: [],
        },
  );
  return [...items, MARK];
}
