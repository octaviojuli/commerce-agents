"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { copy } from "@/lib/api";
import { STAGES, type SupplierView } from "@/lib/types";

export function Top({
  title,
  sub,
  back,
  right,
  white = true,
  children,
}: {
  title: ReactNode;
  sub?: ReactNode;
  back?: string | boolean;
  right?: ReactNode;
  white?: boolean;
  children?: ReactNode;
}) {
  const router = useRouter();
  return (
    <header className={"top" + (white ? " white" : "")} style={children ? { borderBottom: 0, paddingBottom: 6 } : undefined}>
      {back && (
        <button
          className="bk"
          aria-label="返回"
          onClick={() => (typeof back === "string" ? router.push(back) : router.back())}
        >
          ‹
        </button>
      )}
      <div className="tt">
        <b>{title}</b>
        {sub && <small>{sub}</small>}
      </div>
      {right}
    </header>
  );
}

/** The six stages of a deal: 需求 · 选线 · 定团 · 报价 · 成交 · 出行. */
export function Track({ stage }: { stage: number }) {
  return (
    <div className="track" style={{ background: "var(--card)", borderBottom: "1px solid var(--line)" }}>
      {STAGES.map((label, i) => (
        <span key={label} className={i < stage ? "d" : i === stage ? "c" : ""}>
          <i />
          {label}
        </span>
      ))}
    </div>
  );
}

export function Dots({ stage }: { stage: number }) {
  return (
    <>
      {STAGES.map((label, i) => (
        <i key={label} className={i < stage ? "d" : i === stage ? "c" : ""} />
      ))}
    </>
  );
}

export function Tabs({ waiting = 0 }: { waiting?: number }) {
  const path = usePathname();
  const on = (p: string) => (p === "/" ? path === "/" : path.startsWith(p));
  return (
    <nav className="tabs">
      <Link className={on("/") ? "on" : ""} href="/">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 7.5V12l3 2" />
        </svg>
        今日
      </Link>
      <Link className={on("/deals") ? "on" : ""} href="/deals">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <path d="M4 6h16M4 12h16M4 18h10" />
        </svg>
        跟单{waiting > 0 && <span className="n">{waiting}</span>}
      </Link>
      <Link className={on("/routes") ? "on" : ""} href="/routes">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <path d="M3 18l5-8 4 5 3-4 6 7z" />
          <circle cx="16" cy="7" r="2" />
        </svg>
        线路
      </Link>
      <Link className={on("/me") ? "on" : ""} href="/me">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c1-4 4.3-6 8-6s7 2 8 6" />
        </svg>
        我的
      </Link>
    </nav>
  );
}

export function WxIcon() {
  return <span className="wxi">微</span>;
}

/** The green WeChat draft. ``onSent`` records that the advisor sent it. */
export function Draft({
  title = "✎ 建议回复",
  note,
  text,
  removed,
  onSent,
  sent,
}: {
  title?: string;
  note?: ReactNode;
  text: string;
  removed?: string[];
  onSent?: () => void;
  sent?: boolean;
}) {
  const toast = useToast();
  const [value, setValue] = useState(text);
  const [edit, setEdit] = useState(false);
  useEffect(() => setValue(text), [text]);
  return (
    <div className="draft">
      <div className="hd">
        {title}
        <span>{sent ? "已发" : note}</span>
      </div>
      {edit ? (
        <div style={{ padding: "8px 12px" }}>
          <textarea
            aria-label="修改回复"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            style={{ width: "100%", minHeight: 120, font: "inherit", fontSize: 16, border: "1px solid var(--line-2)", borderRadius: 12, padding: 10 }}
          />
        </div>
      ) : (
        <div className="bd">{value}</div>
      )}
      {removed && removed.length > 0 && (
        <details className="cut">
          <summary>已删掉 {removed.length} 句没有依据的话</summary>
          {removed.map((r) => (
            <p key={r} style={{ margin: "4px 0", textDecoration: "line-through" }}>
              {r}
            </p>
          ))}
        </details>
      )}
      <div className="ft">
        <button className="b b-ghost" onClick={() => setEdit(!edit)}>
          {edit ? "完成" : "改一改"}
        </button>
        <button
          className="b b-wx"
          onClick={async () => {
            if (await copy(value)) {
              toast("已复制，去微信粘贴");
              onSent?.();
            }
          }}
        >
          <WxIcon />
          复制去微信
        </button>
      </div>
    </div>
  );
}

export function Chips({ label = "接下来", items, onPick }: { label?: string; items: { label: string; primary?: boolean; will?: boolean; key?: string }[]; onPick: (i: number) => void }) {
  if (!items.length) return null;
  return (
    <div className="nextup">
      <span className="lbl">{label}</span>
      <div className="chips">
        {items.map((c, i) => (
          <button key={c.key ?? c.label} className={"chip" + (c.primary ? " main" : "") + (c.will ? " will" : "")} onClick={() => onPick(i)}>
            {c.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function Working({ text = "助手正在整理…" }: { text?: string }) {
  return (
    <div className="working" role="status">
      <i />
      {text}
    </div>
  );
}

export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="sheet-back" onClick={onClose}>
      <div className="sheet" role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="row">
          <h3 className="grow">{title}</h3>
          <button className="b sm b-ghost" onClick={onClose}>
            关闭
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

let listeners: ((text: string) => void)[] = [];
export function useToast() {
  return useCallback((text: string) => listeners.forEach((l) => l(text)), []);
}

export function Toasts() {
  const [text, setText] = useState("");
  useEffect(() => {
    const show = (t: string) => {
      setText(t);
      setTimeout(() => setText(""), 2200);
    };
    listeners.push(show);
    return () => {
      listeners = listeners.filter((l) => l !== show);
    };
  }, []);
  return text ? (
    <div className="toast" role="status">
      {text}
    </div>
  ) : null;
}

export function Tag({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={"tag t-" + tone}>{children}</span>;
}

// The supplier as the advisor knows it; never rendered on a customer page.
export function Supplier({ s, note = false }: { s?: SupplierView | null; note?: boolean }) {
  if (!s) return null;
  return (
    <span className={"sup" + (s.stance ? " " + s.stance : "")} title={s.note || undefined}>
      {s.name}
      {s.stance_text && <em>{s.stance_text}</em>}
      {note && s.note && <small>{s.note}</small>}
    </span>
  );
}

export function Loading() {
  return <div className="empty">正在读取…</div>;
}

export function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div className="err" role="alert">
      {error}
    </div>
  ) : null;
}
