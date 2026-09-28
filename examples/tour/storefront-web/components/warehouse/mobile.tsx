"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";

let memoryToken: string | null = null;
const KEY = "tour-warehouse-advisor-token";
export function loginToken(value?: string | null) {
  if (value !== undefined) {
    memoryToken = value;
    try {
      if (value) localStorage.setItem(KEY, value);
      else localStorage.removeItem(KEY);
    } catch {}
    try {
      sessionStorage.removeItem(KEY);
    } catch {}
  } else {
    try {
      memoryToken = localStorage.getItem(KEY) || memoryToken;
    } catch {}
  }
  return memoryToken;
}
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {}
  const input = document.createElement("textarea");
  input.value = text;
  input.style.cssText = "position:fixed;top:0;left:0;opacity:0;font-size:16px";
  document.body.append(input);
  input.focus();
  input.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    input.remove();
  }
}
export function useMobile() {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const q = matchMedia("(max-width:767px)");
    const update = () => setMobile(q.matches);
    update();
    q.addEventListener("change", update);
    return () => q.removeEventListener("change", update);
  }, []);
  return mobile;
}
export function useViewport() {
  useEffect(() => {
    const view = window.visualViewport;
    const update = () => {
      // Ignore pinch zoom; only compensate for a keyboard/viewport resize.
      if (!view || view.scale !== 1) return;
      document.documentElement.style.setProperty(
        "--advisor-height",
        `${view.height}px`,
      );
      document.documentElement.style.setProperty(
        "--advisor-top",
        `${view.offsetTop}px`,
      );
    };
    update();
    view?.addEventListener("resize", update);
    view?.addEventListener("scroll", update);
    return () => {
      view?.removeEventListener("resize", update);
      view?.removeEventListener("scroll", update);
      document.documentElement.style.removeProperty("--advisor-height");
      document.documentElement.style.removeProperty("--advisor-top");
    };
  }, []);
}
export function Sheet({
  title,
  close,
  children,
  wide = false,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current!;
    const previous = document.activeElement as HTMLElement | null;
    d.showModal();
    return () => {
      d.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`aw-sheet ${wide ? "aw-sheet-wide" : ""}`}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
      aria-label={title}
    >
      <div className="aw-sheet-content">
        <div className="aw-section-head">
          <h2>{title}</h2>
          <button type="button" onClick={close} aria-label={`关闭${title}`}>
            关闭
          </button>
        </div>
        {children}
      </div>
    </dialog>
  );
}
export function BriefAside({
  open,
  close,
  children,
}: {
  open: boolean;
  close: () => void;
  children: ReactNode;
}) {
  const mobile = useMobile();
  if (mobile)
    return open ? (
      <Sheet title="需求单与当前选择" close={close}>
        {children}
      </Sheet>
    ) : null;
  return (
    <aside className="aw-brief" aria-label="需求单与当前选择">
      {children}
    </aside>
  );
}
