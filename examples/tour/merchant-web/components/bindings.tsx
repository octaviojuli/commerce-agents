"use client";

import { useState, type FormEvent } from "react";
import { WarehouseClient, Connection, Partner, message } from "../lib/api";
import { Field, LoadState, Pagination, useData } from "./common";

type Binding = {
  id: string;
  supplier_org_id: string;
  buyer_org_id: string;
  connection_id: string;
  supplier_name: string;
  buyer_name: string;
  source_name: string;
  customer_code: string;
  customer_name: string;
  version: number;
  active: boolean;
  valid: boolean;
  accepted: boolean;
};

export function Bindings({
  api,
  revision,
  supplierAdmin,
  buyerAdmin,
  onProposed,
  onRefresh,
}: {
  api: WarehouseClient;
  revision: number;
  supplierAdmin: boolean;
  buyerAdmin: boolean;
  onProposed: () => void;
  onRefresh: () => void;
}) {
  const [cursors, setCursors] = useState([""]);
  const [selected, setSelected] = useState("");
  const [confirmed, setConfirmed] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const state = useData<{ items: Binding[]; next_cursor: string | null }>(
    api,
    `/customer-bindings?limit=50${cursors.at(-1) ? `&after=${cursors.at(-1)}` : ""}`,
    revision,
  );
  const current = state.data?.items.find((row) => row.id === selected);
  async function accept(row: Binding) {
    if (confirmed !== `${row.id}:${row.version}`) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.post(`/customer-bindings/${row.id}/accept`, {
        version: row.version,
      });
      setNotice("当前版本的客户映射已确认。后续修改需再次确认。");
      setSelected("");
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
      setConfirmed("");
      onRefresh();
    }
  }
  return (
    <>
      <p className="muted">
        将采购组织对应到供应商 B2B
        中的客户。供应商批准后，仍须采购方管理员确认，才可用于查询该客户的同业价。
      </p>
      {supplierAdmin && (
        <BindingProposal
          api={api}
          revision={revision}
          onProposed={onProposed}
        />
      )}
      <LoadState {...state} />
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {state.data && (
        <section className="panel stack">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>供应商 / 数据源</th>
                  <th>采购组织</th>
                  <th>上游客户</th>
                  <th>确认状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {state.data.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      {row.supplier_name}
                      <br />
                      {row.source_name}
                    </td>
                    <td>{row.buyer_name}</td>
                    <td>
                      {row.customer_name}
                      <br />
                      {row.customer_code}
                    </td>
                    <td>
                      <span className="badge">
                        {!row.active || !row.valid
                          ? "授权已失效"
                          : row.accepted
                            ? "双方已确认"
                            : "待采购方确认"}
                      </span>
                    </td>
                    <td>
                      <button
                        className="link"
                        disabled={busy}
                        onClick={() => {
                          setSelected(row.id);
                          setConfirmed("");
                        }}
                      >
                        核对映射
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!state.data.items.length && (
            <p className="empty">暂无已获供应商批准的客户映射。</p>
          )}
          <Pagination
            next={state.data.next_cursor}
            previous={cursors.length > 1}
            onPrevious={() => {
              setSelected("");
              setCursors(cursors.slice(0, -1));
            }}
            onNext={() => {
              setSelected("");
              setCursors([...cursors, state.data!.next_cursor!]);
            }}
          />
        </section>
      )}
      {current && (
        <section className="panel details stack">
          <h2>确认客户身份对应关系</h2>
          <p>
            供应商：{current.supplier_name} · {current.source_name}
          </p>
          <p>采购组织：{current.buyer_name}</p>
          <p>
            上游客户：{current.customer_name}（{current.customer_code}）
          </p>
          <p className="muted">
            确认的是当前版本 {current.version}
            。请核对它代表本采购组织；确认后顾问查询将使用此客户对应的报价。
          </p>
          {buyerAdmin &&
          current.buyer_org_id === api.organization &&
          current.active &&
          current.valid &&
          !current.accepted ? (
            <>
              <label className="row">
                <input
                  type="checkbox"
                  checked={confirmed === `${current.id}:${current.version}`}
                  disabled={busy}
                  onChange={(event) => setConfirmed(event.target.checked ? `${current.id}:${current.version}` : "")}
                />
                我已核对，上游客户与本采购组织一致
              </label>
              <button
                className="btn approve"
                disabled={busy || confirmed !== `${current.id}:${current.version}`}
                onClick={() => accept(current)}
              >
                {busy ? "确认中…" : "确认此客户映射"}
              </button>
            </>
          ) : (
            <p className="notice">
              {current.accepted
                ? "当前版本已获采购方确认。"
                : "请由对应采购组织的管理员登录并确认。"}
            </p>
          )}
        </section>
      )}
    </>
  );
}

function BindingProposal({
  api,
  revision,
  onProposed,
}: {
  api: WarehouseClient;
  revision: number;
  onProposed: () => void;
}) {
  const sources = useData<{ items: Connection[] }>(
    api,
    "/merchant/connections",
    revision,
  );
  const partners = useData<{ items: Partner[] }>(
    api,
    "/merchant/partners",
    revision,
  );
  const [connection, setConnection] = useState("");
  const [buyer, setBuyer] = useState("");
  return (
    <section className="panel stack">
      <h2>建立或更新客户映射</h2>
      <LoadState {...sources} />
      <LoadState {...partners} />
      <div className="form-grid">
        <Field label="B2B / ERP 数据源">
          <select
            className="input"
            value={connection}
            onChange={(event) => {
              setConnection(event.target.value);
              setBuyer("");
            }}
          >
            <option value="">选择已接入的 API 数据源</option>
            {sources.data?.items
              .filter((row) => row.active && row.connector_type !== "excel")
              .map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
          </select>
        </Field>
        <Field label="对应采购组织">
          <select
            className="input"
            value={buyer}
            onChange={(event) => setBuyer(event.target.value)}
          >
            <option value="">选择已授权的采购组织</option>
            {partners.data?.items
              .filter((row) => row.valid && row.connection_id === connection)
              .map((row) => (
                <option key={row.id} value={row.buyer_org_id}>
                  {row.buyer_name}
                </option>
              ))}
          </select>
        </Field>
      </div>
      {connection && buyer && (
        <VerifyForm
          key={`${connection}:${buyer}:${revision}`}
          api={api}
          connection={connection}
          buyer={buyer}
          onProposed={onProposed}
        />
      )}
    </section>
  );
}
function VerifyForm({
  api,
  connection,
  buyer,
  onProposed,
}: {
  api: WarehouseClient;
  connection: string;
  buyer: string;
  onProposed: () => void;
}) {
  const existing = useData<{ items: Binding[] }>(
    api,
    `/customer-bindings?connection_id=${connection}&buyer_org_id=${buyer}&limit=1`,
  );
  const [code, setCode] = useState("");
  const [verified, setVerified] = useState<{
    verification_id: string;
    code: string;
    name: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function verify() {
    setBusy(true);
    setError("");
    setVerified(null);
    try {
      setVerified(
        await api.post("/customer-bindings/verify", {
          connection_id: connection,
          customer_code: code.trim(),
        }),
      );
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!verified || !existing.data) return;
    setBusy(true);
    setError("");
    try {
      await api.post("/customer-bindings/proposals", {
        verification_id: verified.verification_id,
        buyer_org_id: buyer,
        expected_version: existing.data.items[0]?.version ?? 0,
      });
      onProposed();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="stack" onSubmit={submit}>
      <LoadState {...existing} />
      {existing.data?.items[0] && (
        <p className="notice warning">
          现有映射：{existing.data.items[0].customer_name}（
          {existing.data.items[0].customer_code}
          ）。替换后，原采购确认失效，必须重新确认。
        </p>
      )}
      <Field label="上游客户代码">
        <input
          className="input"
          value={code}
          maxLength={128}
          required
          onChange={(event) => {
            setCode(event.target.value);
            setVerified(null);
          }}
        />
      </Field>
      <button
        type="button"
        className="btn"
        disabled={busy || !code.trim()}
        onClick={verify}
      >
        {busy ? "处理中…" : "向上游验证客户"}
      </button>
      {verified && (
        <div className="notice">
          <p>
            上游验证结果：{verified.name}（{verified.code}）
          </p>
          <p>验证结果有效 15 分钟，请核对名称和代码后生成审批预览。</p>
        </div>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      <button
        className="btn primary"
        disabled={busy || !verified || !existing.data}
      >
        生成客户映射审批预览
      </button>
    </form>
  );
}
