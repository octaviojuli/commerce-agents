// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The page before the workbench: the advisor's own ERP account, the 手机号 and the 密码 alone.
 * There is nothing else on it — no other way in, and no store to pick, because the ERP names the
 * advisor's 门店 itself.
 *
 * The password is typed and sent, and that is all: the field is `current-password` so a password
 * manager can fill it and nothing else can, the value never leaves component state, and the
 * browser remembers a session id rather than a credential. The note under the button says so,
 * along with how long the ERP's own session lasts, because an eight-hour token is what puts this
 * screen back up in the middle of a working day. A refusal is the ERP's own sentence: 手机号 and
 * 密码 wrong, an account locked, too many tries.
 */

import { useState } from "react";
import { BRAND } from "@/lib/brand";

export default function LoginView({
  /** Why this screen is up, when the workbench closed itself. */
  notice,
  /** Answers null once the workbench is open, the refusal to show otherwise. */
  onLogIn,
}: {
  notice: string | null;
  onLogIn: (mobile: string, password: string) => Promise<string | null>;
}) {
  const [mobile, setMobile] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = mobile.trim().length > 0 && password.length > 0 && !pending;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setPending(true);
    setError(null);
    void onLogIn(mobile.trim(), password).then((refusal) => {
      // A login that opened the workbench unmounts this screen; only a refusal comes back here.
      if (refusal === null) return;
      setPending(false);
      setPassword("");
      setError(refusal);
    });
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-(--ground) px-4 py-10">
      <div className="w-full max-w-[380px]">
        <div className="flex items-baseline gap-1.5 px-1">
          <span aria-hidden className="text-[13px] text-(--accent)">
            ◆
          </span>
          <h1 className="text-[19px] font-semibold tracking-[-0.01em] text-(--ink)">{BRAND}</h1>
          <span className="text-[13.5px] text-(--ink-soft)">选团助手</span>
        </div>
        <form
          onSubmit={submit}
          className="mt-3 rounded-(--radius-lg) border border-(--line) bg-(--card) px-5 py-5 shadow-(--shadow)"
        >
          <h2 className="text-[15px] font-semibold text-(--ink)">用 ERP 账号登录</h2>
          <p className="mt-1 text-[13px] leading-snug text-(--ink-soft)">
            门店和同业价都按你的 ERP 账号来。
          </p>

          {notice ? (
            <p
              role="status"
              className="mt-3 rounded-(--radius) bg-(--warn-soft) px-3 py-2 text-[13px] leading-snug text-(--warn)"
            >
              {notice}
            </p>
          ) : null}

          <label className="mt-4 block text-[13px] font-semibold text-(--ink-2)" htmlFor="mobile">
            ERP 手机号
          </label>
          <input
            id="mobile"
            name="mobile"
            type="tel"
            inputMode="numeric"
            autoComplete="username"
            autoFocus
            disabled={pending}
            value={mobile}
            onChange={(event) => setMobile(event.target.value)}
            placeholder="13800000000"
            className="tg-num mt-1.5 block w-full rounded-(--radius) border border-(--line-strong) bg-(--surface) px-3 py-2.5 text-[15px] text-(--ink) placeholder:text-(--ink-faint) focus:border-(--accent) focus:outline-none disabled:opacity-60"
          />

          <label className="mt-3.5 block text-[13px] font-semibold text-(--ink-2)" htmlFor="password">
            密码
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            disabled={pending}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1.5 block w-full rounded-(--radius) border border-(--line-strong) bg-(--surface) px-3 py-2.5 text-[15px] text-(--ink) focus:border-(--accent) focus:outline-none disabled:opacity-60"
          />

          {error ? (
            <p
              role="alert"
              className="mt-3 rounded-(--radius) bg-(--danger-soft) px-3 py-2 text-[13px] leading-snug text-(--danger)"
            >
              {error}
            </p>
          ) : null}

          <button type="submit" disabled={!ready} className="btn-primary mt-4 w-full">
            {pending ? "登录中…" : "登录"}
          </button>
          <p className="mt-3 text-[12px] leading-relaxed text-(--ink-soft)">
            工作台不保存密码，只记住这次登录；ERP 的登录有效期 8 小时，到期后重新登录即可。
          </p>
        </form>
      </div>
    </main>
  );
}
