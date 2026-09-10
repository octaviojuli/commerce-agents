// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The ERP account behind the workbench: the four routes the login uses, and the watch that
 * notices a session the API has stopped honouring.
 *
 * `POST /api/login` takes the advisor's own 手机号 and 密码, signs them in against the ERP and
 * answers with the session it started; `POST /api/logout` ends it; `GET /api/advisor` says
 * whether a session is still signed in, which is what a reload asks before it resumes anything.
 * The ERP's token lasts eight hours and a restarted API keeps none, so a remembered session is
 * a claim to be checked rather than a fact.
 *
 * The calls are written against `fetch` rather than the shared client because the ERP's own
 * refusal is the message the advisor needs to read: the client answers null on every failure
 * alike, and `detail` on a 401 is the sentence the ERP wrote. The base is the client's, so a
 * build that points elsewhere moves these with it.
 */

import type { AgentApi } from "web-shared";

/** The signed-in advisor as the API names them; `departments` is how many their account sells for. */
export interface Advisor {
  name: string;
  department: string;
  departments: number;
}

export type LoginResult =
  | { ok: true; sessionId: string; advisor: Advisor }
  /** The ERP's own sentence on a refusal, or this app's on an unreachable API. */
  | { ok: false; detail: string };

export type AdvisorState =
  | { state: "signed-in"; advisor: Advisor }
  /** The API no longer honours this session: the token expired, or the host restarted. */
  | { state: "expired" }
  /** Nothing answered, so the session is neither confirmed nor denied. */
  | { state: "unreachable" };

export type FreshSession = { ok: true; sessionId: string } | { ok: false; detail: string };

/** Shown wherever a login screen is standing because the API said the session is over. */
export const EXPIRED = "ERP 登录已过期，请重新登录";

const UNREACHABLE_DETAIL = "连不上工作台的接口，请稍后再试。";

function advisorOf(value: Partial<Advisor> | null | undefined): Advisor {
  return {
    name: String(value?.name ?? "").trim() || "顾问",
    department: String(value?.department ?? "").trim(),
    departments: Number(value?.departments ?? 0) || 0,
  };
}

/** The ERP's sentence when the body carries one, this app's fallback otherwise. */
async function detailOf(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail.trim() : "";
    if (detail) return detail;
  } catch {
    // A body that is not the API's own shape says nothing; the status does.
  }
  return `登录失败（${response.status}）。`;
}

/** The advisor's ERP credentials, sent once and kept nowhere. */
export async function logIn(
  api: AgentApi,
  mobile: string,
  password: string,
): Promise<LoginResult> {
  let response: Response;
  try {
    response = await fetch(`${api.base}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mobile, password }),
    });
  } catch {
    return { ok: false, detail: UNREACHABLE_DETAIL };
  }
  if (!response.ok) return { ok: false, detail: await detailOf(response) };
  const data = (await response.json().catch(() => null)) as {
    session_id?: string;
    advisor?: Partial<Advisor>;
  } | null;
  if (!data?.session_id) return { ok: false, detail: UNREACHABLE_DETAIL };
  return { ok: true, sessionId: data.session_id, advisor: advisorOf(data.advisor) };
}

/** Ends the ERP session behind the token the client is holding. */
export async function logOut(api: AgentApi): Promise<void> {
  try {
    await fetch(`${api.base}/logout`, { method: "POST", headers: api.headers() });
  } catch {
    // The workbench signs out either way: the token is dropped here and the ERP's own expires.
  }
}

/** Whether the session the client is holding is still an advisor's. */
export async function readAdvisor(api: AgentApi): Promise<AdvisorState> {
  let response: Response;
  try {
    response = await fetch(`${api.base}/advisor`, { headers: api.headers() });
  } catch {
    return { state: "unreachable" };
  }
  if (response.status === 401 || response.status === 403) return { state: "expired" };
  if (!response.ok) return { state: "unreachable" };
  const data = (await response.json().catch(() => null)) as
    | ({ logged_in?: boolean } & Partial<Advisor>)
    | null;
  if (!data) return { state: "unreachable" };
  if (!data.logged_in) return { state: "expired" };
  return { state: "signed-in", advisor: advisorOf(data) };
}

/**
 * Another conversation for the advisor already signed in. `POST /api/sessions/new` is the route
 * for it, because the ERP credentials are not in this browser and `POST /api/session` names an
 * advisor of its own — which is why a live API refuses it. A deployment on the fixtures still
 * has that route, and answers this one's absence with a 404, so the mock path falls back to it.
 */
export async function startFreshSession(api: AgentApi): Promise<FreshSession> {
  const fresh = await postSession(api, "/sessions/new");
  if (fresh.ok || (fresh.status !== 404 && fresh.status !== 405)) return fresh.result;
  const mock = await postSession(api, "/session");
  if (!mock.ok && mock.status === 403) {
    return { ok: false, detail: "这台服务器不允许再开新会话，请重新登录后再试。" };
  }
  return mock.result;
}

async function postSession(
  api: AgentApi,
  path: string,
): Promise<{ ok: true; result: FreshSession } | { ok: false; status: number; result: FreshSession }> {
  let response: Response;
  try {
    response = await fetch(`${api.base}${path}`, { method: "POST", headers: api.headers() });
  } catch {
    return { ok: false, status: 0, result: { ok: false, detail: UNREACHABLE_DETAIL } };
  }
  if (!response.ok) {
    return { ok: false, status: response.status, result: { ok: false, detail: await detailOf(response) } };
  }
  const data = (await response.json().catch(() => null)) as { session_id?: string } | null;
  if (!data?.session_id) {
    return { ok: false, status: response.status, result: { ok: false, detail: UNREACHABLE_DETAIL } };
  }
  return { ok: true, result: { ok: true, sessionId: data.session_id } };
}

/**
 * The workbench's own watch on the expiry it cannot see otherwise: the shared client answers
 * null on a 401 the same as on a dropped connection, and a cart read or a reply is where an
 * eight-hour token usually runs out. So every response this page gets from the API is looked at,
 * and the first 401 from anything but the login route signs the advisor out. The wrapper is
 * removed on cleanup when nothing else has wrapped it since.
 */
export function watchExpiry(api: AgentApi, onExpired: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const original = window.fetch;
  const login = `${api.base}/login`;
  const watched: typeof window.fetch = async (input, init) => {
    const response = await original(input, init);
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
    if (response.status === 401 && url.startsWith(api.base) && !url.startsWith(login)) onExpired();
    return response;
  };
  window.fetch = watched;
  return () => {
    if (window.fetch === watched) window.fetch = original;
  };
}
