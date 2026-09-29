"use client";

import { useEffect, useState, type ReactNode } from "react";
import { message, statuses, WarehouseClient } from "../lib/api";

export function useData<T>(api: WarehouseClient, path: string, revision = 0) {
  const [state, set] = useState<{
    data?: T;
    error?: string;
    loading: boolean;
    api?: WarehouseClient;
    path?: string;
    revision?: number;
  }>({ loading: true });
  useEffect(() => {
    const controller = new AbortController();
    set({ loading: true, api, path, revision });
    api
      .get<T>(path, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted)
          set({ data, loading: false, api, path, revision });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          set({ error: message(error), loading: false, api, path, revision });
      });
    return () => controller.abort();
  }, [api, path, revision]);
  return state.api === api && state.path === path && state.revision === revision
    ? state
    : { loading: true };
}

export function LoadState({
  loading,
  error,
}: {
  loading: boolean;
  error?: string;
}) {
  return error ? (
    <p className="notice error" role="alert">
      {error}
    </p>
  ) : loading ? (
    <p className="empty" role="status">
      正在读取云仓…
    </p>
  ) : null;
}

export function Badge({ status }: { status: string }) {
  return (
    <span
      className={`badge ${["applied", "published", "active", "validated"].includes(status) ? "good" : status === "staged" ? "warn" : ""}`}
    >
      {statuses[status] ?? status}
    </span>
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export function Pagination({
  next,
  previous,
  onNext,
  onPrevious,
}: {
  next?: string | null;
  previous: boolean;
  onNext: () => void;
  onPrevious: () => void;
}) {
  return (
    <div className="row between">
      <span className="muted">使用上一页 / 下一页查看全部记录</span>
      <div className="row">
        <button className="btn" disabled={!previous} onClick={onPrevious}>
          上一页
        </button>
        <button className="btn" disabled={!next} onClick={onNext}>
          下一页
        </button>
      </div>
    </div>
  );
}

// Keep the current page mounted while polling so unsaved operator edits survive.
export function usePolling<T>(api: WarehouseClient, path: string, revision: number) {
  const [state, set] = useState<{
    api?: WarehouseClient; path?: string; data?: T; error?: string;
  }>({});
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const data = await api.get<T>(path, controller.signal);
        if (!controller.signal.aborted) set({ api, path, data });
      } catch (error) {
        if (!controller.signal.aborted) set((previous) => ({
          ...(previous.api === api && previous.path === path ? previous : {}),
          api, path, error: message(error),
        }));
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(read, 10000);
      }
    }
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, path, revision]);
  const current = state.api === api && state.path === path ? state : {};
  return { ...current, loading: !current.data && !current.error };
}
