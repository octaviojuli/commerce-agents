"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { message, WarehouseClient, type Partner } from "../lib/api";
import { warehouseEvents } from "web-shared/warehouse-client";

type ChangePreview = {
  change_id: string;
  status: string;
  summary: string;
  items: { target: string; field: string; before: unknown; after: unknown }[];
};
type Entry = {
  role: "user" | "assistant";
  text: string;
  changed?: boolean;
  changes?: ChangePreview[];
};
type Event = { type: string; data: Record<string, unknown> };
type Turn = { status: string; message: string; events: Event[] };

function changesFrom(events: Event[], existing: ChangePreview[] = []) {
  const changes = new Map(existing.map((change) => [change.change_id, change]));
  for (const event of events) {
    const payload = event.data.payload as Record<string, unknown> | undefined;
    const value = event.type === "change_update"
      ? event.data.change
      : event.type === "ui" && event.data.component === "change_preview"
        ? payload?.change : null;
    if (!value || typeof value !== "object") continue;
    const change = value as ChangePreview;
    if (typeof change.change_id === "string" && typeof change.summary === "string" &&
        Array.isArray(change.items) && change.items.every((item) => item && typeof item.target === "string" && typeof item.field === "string")) {
      changes.set(change.change_id, change);
    }
  }
  return [...changes.values()];
}

function changeValue(value: unknown): string {
  if (value == null) return "未知";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  return JSON.stringify(value);
}

const changeFields: Record<string, string> = {
  stock: "可用库存", price: "成人同业价", status: "上架状态",
  title: "线路名称", long_description: "线路说明",
};

export function Assistant({
  api,
  role,
  conversation,
  onConversation,
  onClose,
  onRefresh,
  onApprovals,
}: {
  api: WarehouseClient;
  role: "merchant" | "advisor";
  conversation: string;
  onConversation: (id: string) => void;
  onClose: () => void;
  onRefresh: () => void;
  onApprovals: () => void;
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(!!conversation);
  const [error, setError] = useState("");
  const [buyer, setBuyer] = useState("");
  const [partners, setPartners] = useState<Partner[]>([]);
  const [partnersLoading, setPartnersLoading] = useState(role === "merchant");
  const [partnersError, setPartnersError] = useState("");
  const [partnersRevision, setPartnersRevision] = useState(0);
  const [restoreFailed, setRestoreFailed] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const identifier = useRef(conversation);
  const initialized = useRef(false);
  useEffect(() => {
    if (role !== "merchant") return;
    const cancel = new AbortController();
    setPartnersLoading(true);
    setPartnersError("");
    api.get<{ items: Partner[] }>("/merchant/partners", cancel.signal)
      .then((result) => {
        if (!cancel.signal.aborted) setPartners(result.items);
      })
      .catch((error) => {
        if (!cancel.signal.aborted) {
          setPartners([]);
          setPartnersError(message(error));
        }
      })
      .finally(() => {
        if (!cancel.signal.aborted) setPartnersLoading(false);
      });
    return () => cancel.abort();
  }, [api, role, partnersRevision]);
  const buyers = [...new Map(
    partners.filter((row) => row.valid).map((row) => [row.buyer_org_id, row]),
  ).values()];
  useEffect(() => {
    const cancel = new AbortController();
    if (!initialized.current && identifier.current) {
      setLoading(true);
      api
        .get<{ turns: Turn[]; buyer_context: string | null }>(
          `/conversations/${identifier.current}`,
          cancel.signal,
        )
        .then((result) => {
          if (cancel.signal.aborted) return;
          setBuyer(result.buyer_context ?? "");
          setRestoreFailed(false);
          setEntries(
            result.turns.flatMap((turn) => [
              { role: "user" as const, text: turn.message },
              {
                role: "assistant" as const,
                text:
                  turn.events
                    .filter((event) => event.type === "text_delta")
                    .map((event) => String(event.data.text ?? ""))
                    .join("") ||
                  (turn.status === "complete"
                    ? "本轮已完成，请核对业务页面。"
                    : "本轮未完成，请先核对审批状态。"),
                changed: turn.events.some(
                  (event) => event.type === "change_update",
                ),
                changes: changesFrom(turn.events),
              },
            ]),
          );
        })
        .catch((error) => {
          if (!cancel.signal.aborted) {
            setError(message(error));
            setRestoreFailed(true);
          }
        })
        .finally(() => {
          if (!cancel.signal.aborted) {
            setLoading(false);
            initialized.current = true;
          }
        });
    } else {
      initialized.current = true;
      setLoading(false);
    }
    return () => {
      cancel.abort();
      controller.current?.abort();
    };
  }, [api]);

  function newConversation() {
    if (busy || loading) return;
    identifier.current = "";
    initialized.current = true;
    onConversation("");
    setEntries([]);
    setBuyer("");
    setInput("");
    setError("");
    setRestoreFailed(false);
    setPartnersRevision((value) => value + 1);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy || loading || restoreFailed) return;
    const cancel = new AbortController();
    controller.current = cancel;
    setBusy(true);
    setError("");
    setInput("");
    setEntries((previous) => [
      ...previous,
      { role: "user", text },
      { role: "assistant", text: "" },
    ]);
    let complete = false;
    try {
      if (!identifier.current) {
        const response = await api.response("/conversations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            role,
            ...(role === "merchant" && buyer ? { buyer_context: buyer } : {}),
          }),
          signal: cancel.signal,
        });
        const result = await response.json();
        identifier.current = result.id;
        onConversation(result.id);
      }
      const response = await api.response(
        `/conversations/${identifier.current}/chat`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: JSON.stringify({ message: text }),
          signal: cancel.signal,
        },
      );
      if (!response.body)
        throw new Error("助手没有返回内容，请核对审批状态后重试。");
      for await (const { type: kind, data } of warehouseEvents(response.body)) {
        if (kind === "text_delta")
          setEntries((previous) =>
            previous.map((row, i) =>
              i === previous.length - 1
                ? { ...row, text: row.text + String(data.text ?? "") }
                : row,
            ),
          );
        if (
          kind === "change_update" ||
          (kind === "ui" &&
            String(data.component).toLowerCase().includes("change"))
        ) {
          setEntries((previous) =>
            previous.map((row, i) =>
              i === previous.length - 1
                ? { ...row, changed: true, changes: changesFrom([{ type: kind, data }], row.changes) }
                : row,
            ),
          );
          onRefresh();
        }
        if (kind === "error")
          throw new Error(String(data.message ?? "助手处理未完成"));
        if (kind === "turn_complete") complete = true;
      }
      if (!complete)
        throw new Error("连接已中断，本轮结果未确认保存，请先核对审批状态。");
      setEntries((previous) =>
        previous.map((row, i) =>
          i === previous.length - 1 && !row.text
            ? { ...row, text: "处理已完成，请查看业务页面。" }
            : row,
        ),
      );
      onRefresh();
    } catch (error) {
      if (!cancel.signal.aborted) setError(message(error));
    } finally {
      if (!cancel.signal.aborted) setBusy(false);
    }
  }

  return (
    <section className="chat" aria-label="云仓助手">
      <header className="chat-head row between">
        <div>
          <strong>云仓助手</strong>
          <p className="muted">查询、整理与变更提议</p>
        </div>
        <div className="row">
          <button className="btn" onClick={newConversation} disabled={busy || loading}>
            新会话
          </button>
          <button className="btn" onClick={onClose}>收起</button>
        </div>
      </header>
      {role === "merchant" && (
        <div className="chat-context stack">
          <label className="field">
            <span>采购价格上下文</span>
            <select
              className="input"
              value={buyer}
              onChange={(event) => setBuyer(event.target.value)}
              disabled={busy || loading || partnersLoading || !!identifier.current}
            >
              <option value="">通用查询（不指定协议价）</option>
              {buyer && !buyers.some((row) => row.buyer_org_id === buyer) && (
                <option value={buyer}>
                  {partners.find((row) => row.buyer_org_id === buyer)?.buyer_name ?? buyer}（请核对授权）
                </option>
              )}
              {buyers.map((row) => (
                <option key={row.buyer_org_id} value={row.buyer_org_id}>
                  {row.buyer_name} · {row.buyer_org_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            {identifier.current
              ? buyer
                ? "本会话的采购方已固定。切换采购方请新建会话，重新查询价格。"
                : "本会话使用通用查询。查询协议价请新建会话并选择采购方。"
              : "查询或调整协议价前，请选择采购旅行社；未选择时可查询线路与库存。"}
          </p>
          {partnersLoading && <p className="muted" role="status">正在读取已授权采购方…</p>}
          {partnersError && (
            <div className="notice error" role="alert">
              <p>采购方读取失败：{partnersError}</p>
              <button className="link" onClick={() => setPartnersRevision((value) => value + 1)}>
                重新读取采购方
              </button>
            </div>
          )}
        </div>
      )}
      <div className="chat-log" aria-live="polite">
        {!entries.length && (
          <div className="stack">
            <h2>从一条线路开始。</h2>
            <p className="muted">
              {role === "merchant"
                ? "可以查询已接入的线路，或提出库存与内容修改。变更须前往审批中心，由管理员核对。"
                : "可以查询已授权的线路、团期与报价。当前不提供占位、下单或支付。"}
            </p>
          </div>
        )}
        {loading && <p>正在恢复会话…</p>}
        {entries.map((entry, i) => (
          <article key={i} className={`chat-msg ${entry.role}`}>
            {entry.text}
            {(entry.changed || !!entry.changes?.length) && (
              <div className="chat-card">
                {entry.changes?.map((change) => (
                  <section key={change.change_id} className="stack chat-change">
                    <strong>变更预览</strong>
                    <p>{change.summary}</p>
                    <p className="muted">生成时状态：{({ staged: "待审批", applied: "已应用", discarded: "已作废" } as Record<string, string>)[change.status] ?? "待核对"}</p>
                    {change.items.map((item, index) => (
                      <div key={`${item.target}-${item.field}-${index}`} className="chat-diff">
                        <strong>{changeFields[item.field] ?? item.field}</strong>
                        <p>变更前：{changeValue(item.before)}</p>
                        <p>变更后：{changeValue(item.after)}</p>
                        <small className="muted">对象编号：{item.target}</small>
                      </div>
                    ))}
                    <small className="muted">提议编号：{change.change_id}</small>
                  </section>
                ))}
                <p>对话预览仅供回顾。请前往审批中心核对完整内容、采购方与当前状态，确认后再审批。</p>
                <button className="link" onClick={onApprovals}>
                  前往审批中心
                </button>
              </div>
            )}
          </article>
        ))}
        {busy && (
          <p className="muted" role="status">
            正在处理…
          </p>
        )}
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
      </div>
      <form className="chat-compose stack" onSubmit={submit}>
        <label className="field">
          <span>发送给助手</span>
          <textarea
            className="input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            maxLength={4000}
            placeholder="查找线路，或描述需要修改的内容"
            required
          />
        </label>
        <button
          className="btn primary"
          disabled={busy || loading || restoreFailed || !input.trim()}
        >
          发送
        </button>
      </form>
    </section>
  );
}
