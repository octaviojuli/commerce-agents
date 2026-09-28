"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api } from "@/lib/api";

function Login() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="login"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        try {
          await api.post("/login", { email, password });
          router.replace(params.get("next") || "/");
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <span className="tag t-br" style={{ alignSelf: "flex-start" }}>
        顾问搭档
      </span>
      <h1>记住、对比、核对、起草</h1>
      <p className="lbl" style={{ margin: 0 }}>
        用云仓账号登录。客人的话粘贴进来，搭档帮你整理需求、查线路、写回复。
      </p>
      <label className="field">
        账号邮箱
        <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label className="field">
        密码
        <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </label>
      {error && <div className="err">{error}</div>}
      <button className="b b-br" disabled={busy}>
        {busy ? "正在登录…" : "登录"}
      </button>
    </form>
  );
}

export default function Page() {
  return (
    <Suspense>
      <Login />
    </Suspense>
  );
}
