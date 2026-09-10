// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * Who the workbench is signed in as, and which of that advisor's conversations it is in.
 *
 * The login is the advisor's own ERP account (`lib/auth.ts`), and the session it starts is the
 * only thing this browser remembers: no password is stored, and the id alone is worth nothing
 * once the ERP's eight-hour token is over. So a load starts nothing. With no remembered id, or
 * one `GET /api/advisor` no longer honours, the page is the login screen; with one it does, the
 * workbench comes back where it was.
 *
 * 历史会话 is unchanged by the login: `GET /api/sessions` lists that advisor's conversations
 * newest first and `GET /api/sessions/{id}/messages` returns one transcript, and resuming is
 * nothing more than sending that id in the session header, which the server continues. The
 * remembered id is adopted only when the advisor's own list carries it, so a session that was
 * moved on from is read before the workbench moves into it. 新会话 asks the API for another
 * conversation for the advisor already signed in; the credentials are not here to start one.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentApi, ChatItem } from "web-shared";
import {
  type Advisor,
  EXPIRED,
  logIn as erpLogIn,
  logOut as erpLogOut,
  readAdvisor,
  startFreshSession,
  watchExpiry,
} from "./auth";
import type { SessionMessage, SessionSummary } from "./types";

/** The last session this browser was in. */
const REMEMBERED = "acme-tour.session";

function readRemembered(): string | null {
  try {
    const raw = window.localStorage.getItem(REMEMBERED);
    const value = raw ? (JSON.parse(raw) as { session_id?: unknown }) : null;
    const id = typeof value?.session_id === "string" ? value.session_id : "";
    return id || null;
  } catch {
    return null;
  }
}

function remember(sessionId: string): void {
  try {
    window.localStorage.setItem(REMEMBERED, JSON.stringify({ session_id: sessionId }));
  } catch {
    // A browser that refuses storage simply does not resume; the workbench is unaffected.
  }
}

function forget(): void {
  try {
    window.localStorage.removeItem(REMEMBERED);
  } catch {
    // Nothing to drop; the id it holds stops being honoured on its own.
  }
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

/** Hides what a move abandoned: a session nobody spoke in that is not the open one. */
function spoken(sessions: SessionSummary[], current: string | null): SessionSummary[] {
  return sessions.filter((item) => item.message_count > 0 || item.session_id === current);
}

export interface Resumed {
  sessionId: string;
  /** The session's earlier messages; empty in one just started. */
  messages: SessionMessage[];
}

/** Whether the page is the login screen, the workbench, or neither one yet. */
export type SessionStatus = "checking" | "signed-out" | "signed-in";

export interface AdvisorSession {
  status: SessionStatus;
  /** Why the login screen is standing, when the workbench closed itself. */
  notice: string | null;
  /** The signed-in advisor, the same person in every one of these sessions. */
  advisor: Advisor | null;
  sessionId: string | null;
  /** The advisor's sessions, newest first; null until the first read. */
  sessions: SessionSummary[] | null;
  /** A new object per move, which is what the transcript replays. */
  resumed: Resumed | null;
  /** A move is in flight. */
  switching: boolean;
  /** Null once the workbench is open, the ERP's own refusal otherwise. */
  logIn: (mobile: string, password: string) => Promise<string | null>;
  logOut: () => void;
  resume: (sessionId: string) => void;
  startNew: () => void;
  /** What the API said about the last 新会话, when it refused one. */
  startError: string | null;
  refresh: () => void;
}

/**
 * The workbench's session, in place of `useSession`: the advisor it is signed in as, the
 * conversation it is in, the list behind the 历史会话 panel, and the moves the chrome offers.
 * Only the newest move installs its token.
 */
export function useAdvisorSession(api: AgentApi): AdvisorSession {
  const [status, setStatus] = useState<SessionStatus>("checking");
  const [notice, setNotice] = useState<string | null>(null);
  const [advisor, setAdvisor] = useState<Advisor | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [resumed, setResumed] = useState<Resumed | null>(null);
  const [switching, setSwitching] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const move = useRef(0);

  const refresh = useCallback(() => {
    void fetchSessions(api).then((listed) => {
      if (listed) setSessions(listed);
    });
  }, [api]);

  /** Moves in: the token on the client, the id in the browser, the transcript on the page. */
  const install = useCallback(
    (next: string, messages: SessionMessage[]) => {
      api.session = next;
      remember(next);
      setSessionId(next);
      setResumed({ sessionId: next, messages });
    },
    [api],
  );

  /** Drops the session and shows the login screen, with a note when the API ended it. */
  const close = useCallback(
    (note: string | null) => {
      move.current += 1;
      forget();
      api.session = null;
      setStatus("signed-out");
      setNotice(note);
      setAdvisor(null);
      setSessionId(null);
      setSessions(null);
      setResumed(null);
      setSwitching(false);
      setStartError(null);
    },
    [api],
  );

  const logIn = useCallback(
    async (mobile: string, password: string): Promise<string | null> => {
      const result = await erpLogIn(api, mobile, password);
      if (!result.ok) return result.detail;
      const mine = ++move.current;
      setNotice(null);
      setAdvisor(result.advisor);
      setStatus("signed-in");
      install(result.sessionId, []);
      if (move.current === mine) refresh();
      return null;
    },
    [api, install, refresh],
  );

  const logOut = useCallback(() => {
    void erpLogOut(api);
    close(null);
  }, [api, close]);

  const startNew = useCallback(() => {
    const mine = ++move.current;
    setSwitching(true);
    setStartError(null);
    void (async () => {
      const started = await startFreshSession(api);
      if (move.current !== mine) return;
      if (started.ok) install(started.sessionId, []);
      else setStartError(started.detail);
      setSwitching(false);
      refresh();
    })();
  }, [api, install, refresh]);

  // The messages are read before the token moves, so a message sent meanwhile still belongs to
  // the conversation on the page.
  const resume = useCallback(
    (next: string) => {
      const mine = ++move.current;
      setSwitching(true);
      void (async () => {
        const messages = await fetchMessages(api, next);
        if (move.current !== mine) return;
        // A session the API no longer has stays closed; the list says so on the next read.
        if (messages) install(next, messages);
        setSwitching(false);
        refresh();
      })();
    },
    [api, install, refresh],
  );

  // What a load does: ask about the remembered session, and either resume it or ask for the
  // ERP account again.
  useEffect(() => {
    const mine = ++move.current;
    void (async () => {
      const remembered = readRemembered();
      if (!remembered) {
        if (move.current === mine) setStatus("signed-out");
        return;
      }
      api.session = remembered;
      const state = await readAdvisor(api);
      if (move.current !== mine) return;
      if (state.state !== "signed-in") {
        close(state.state === "expired" ? EXPIRED : null);
        return;
      }
      setAdvisor(state.advisor);
      setStatus("signed-in");
      // The list is read through the session itself, so what it names is this advisor's.
      const listed = await fetchSessions(api);
      if (move.current !== mine) return;
      if (listed) setSessions(listed);
      const carried = listed?.some((item) => item.session_id === remembered) ?? false;
      const messages = carried ? await fetchMessages(api, remembered) : null;
      if (move.current !== mine) return;
      install(remembered, messages ?? []);
    })();
    return () => {
      move.current += 1;
    };
  }, [api, close, install]);

  // A token can run out while the workbench is open, and the request that finds out is any of
  // them; the watch is only worth having while there is a session to lose.
  useEffect(() => {
    if (status !== "signed-in") return;
    return watchExpiry(api, () => close(EXPIRED));
  }, [api, close, status]);

  return {
    status,
    notice,
    advisor,
    sessionId,
    sessions: sessions && spoken(sessions, sessionId),
    resumed,
    switching,
    logIn,
    logOut,
    resume,
    startNew,
    startError,
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
