"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import {
  AssistantRail,
  PortalShell,
  ZH_CHROME_COPY,
  type PortalNavItem,
} from "web-shared";
import { Field, LoadState, useData } from "../components/common";
import {
  Imports,
  Inventory,
  Overview,
} from "../components/views";
import { Business } from "../components/business";
import { navigate, scoped, useLocation } from "../components/business-navigation";
import { Prices } from "../components/prices";
import { Bindings } from "../components/bindings";
import { Approvals } from "../components/approvals";
import { SyncHistory } from "../components/sync";
import { Documents } from "../components/documents";
import { Grants } from "../components/grants";
import { Sources } from "../components/sources";
import { Invitations } from "../components/invitations";
import { Inquiries } from "../components/inquiries";
import { Assistant } from "../components/assistant";
import { message, Organization, WarehouseClient } from "../lib/api";

type View =
  | "home"
  | "routes"
  | "departures"
  | "orders"
  | "inventory"
  | "imports"
  | "approvals"
  | "sync"
  | "prices"
  | "bindings"
  | "grants"
  | "sources"
  | "invitations"
  | "inquiries"
  | "documents";
const navigation: PortalNavItem<View>[] = [
  { id: "home", label: "工作概览", icon: "home" },
  { id: "inquiries", label: "顾问核实单", icon: "inbox" },
  { id: "routes", label: "线路管理", icon: "plane" },
  { id: "departures", label: "团期管理", icon: "box" },
  { id: "orders", label: "订单管理", icon: "inbox" },
  { id: "inventory", label: "自管库存", icon: "box" },
  { id: "imports", label: "Excel 导入", icon: "inbox" },
  { id: "documents", label: "线路文档", icon: "inbox" },
  { id: "approvals", label: "审批中心", icon: "check" },
  { id: "prices", label: "价表与等级", icon: "tag" },
  { id: "bindings", label: "客户映射", icon: "user" },
  { id: "sync", label: "数据同步", icon: "signal" },
  { id: "grants", label: "分销授权", icon: "user" },
  { id: "sources", label: "供应源管理", icon: "inbox" },
  { id: "invitations", label: "供采邀请", icon: "user" },
];
const supplierRoles = [
  "supplier_admin",
  "product_editor",
  "inventory_manager",
  "auditor",
];
const TOKEN_KEY = "tour-warehouse-token";

export default function Page() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState("");
  const clear = useCallback(() => {
    sessionStorage.removeItem(TOKEN_KEY);
    setToken(null);
  }, []);
  useEffect(() => {
    setToken(sessionStorage.getItem(TOKEN_KEY));
    setReady(true);
    const expired = () => {
      clear();
      setNotice("登录已失效，请重新登录。");
    };
    window.addEventListener("warehouse-expired", expired);
    return () => window.removeEventListener("warehouse-expired", expired);
  }, [clear]);
  async function logout() {
    try {
      await new WarehouseClient(token ?? "").post("/auth/logout");
    } catch {
      setNotice(
        "本地登录已退出；服务暂未确认会话注销，请关闭共用设备上的窗口。",
      );
    } finally {
      clear();
    }
  }
  if (!ready) return <div className="empty">正在恢复登录…</div>;
  if (!token)
    return (
      <Login
        notice={notice}
        onLogin={(access) => {
          sessionStorage.setItem(TOKEN_KEY, access);
          setToken(access);
          setNotice("");
        }}
      />
    );
  return <Organizations key={token} token={token} onLogout={logout} />;
}

function Login({
  notice,
  onLogin,
}: {
  notice: string;
  onLogin: (token: string) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await new WarehouseClient("").post<{
        access_token: string;
      }>("/auth/login", {
        email: data.get("email"),
        password: data.get("password"),
      });
      onLogin(result.access_token);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <section className="login-story">
        <div className="eyebrow">TOUR / SUPPLIER WORKSPACE</div>
        <h1>
          每一条线路，
          <br />
          都有清晰的来处。
        </h1>
        <p>
          统一管理线路、团期与供应商库存，
          <br />
          让每一次发布都经过核对。
        </p>
        <div className="step-list">
          <span>01 数据接入</span>
          <span>02 校验审批</span>
          <span>03 授权分销</span>
        </div>
      </section>
      <section className="login-form stack">
        <div>
          <div className="eyebrow">供应商工作台</div>
          <h2>登录 Tour 云仓</h2>
          <p className="muted">使用平台分配的员工账号登录。</p>
        </div>
        {(error || notice) && (
          <p className="notice error" role="alert">
            {error || notice}
          </p>
        )}
        <form onSubmit={submit} className="stack">
          <Field label="账号邮箱">
            <input
              className="input"
              name="email"
              type="email"
              autoComplete="username"
              required
            />
          </Field>
          <Field label="密码">
            <input
              className="input"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </Field>
          <button className="btn primary" disabled={busy}>
            {busy ? "正在登录…" : "进入工作台"}
          </button>
        </form>
        <p className="muted">
          账号由平台管理员开通。这里使用云仓账号，ERP 的接入凭证由服务端保管。
        </p>
      </section>
    </main>
  );
}

function Organizations({
  token,
  onLogout,
}: {
  token: string;
  onLogout: () => void;
}) {
  const api = useMemo(() => new WarehouseClient(token), [token]);
  const state = useData<{ items: Organization[] }>(api, "/me/organizations");
  const location = useLocation();
  const selected = new URL(location,"https://warehouse.invalid").searchParams.get("org") ?? "";
  const setSelected = (id:string) => navigate(scoped(organizations.find(org=>org.id===id)?.roles.some(role=>supplierRoles.includes(role)) ? "/routes" : "/bindings",id));
  const organizations =
    state.data?.items.filter((org) =>
      org.roles.some(
        (role) => supplierRoles.includes(role) || role === "buyer_admin",
      ),
    ) ?? [];
  const current =
    selected ? organizations.find((org) => org.id === selected) : organizations.find(org=>org.roles.some(role=>supplierRoles.includes(role))) ?? organizations[0];
  if (!current)
    return (
      <main className="login-form stack">
        <h1>供应商工作台</h1>
        <LoadState {...state} />
        {state.data && (
          <p>当前账号没有可用的供应或采购管理权限，请联系平台管理员。</p>
        )}
        <button className="btn" onClick={onLogout}>
          退出登录
        </button>
      </main>
    );
  return (
    <Workbench
      key={current.id}
      token={token}
      organization={current}
      organizations={organizations}
      onSelect={setSelected}
      onLogout={onLogout}
    />
  );
}

function Workbench({
  token,
  organization,
  organizations,
  onSelect,
  onLogout,
}: {
  token: string;
  organization: Organization;
  organizations: Organization[];
  onSelect: (id: string) => void;
  onLogout: () => void;
}) {
  const api = useMemo(
    () => new WarehouseClient(token, organization.id),
    [token, organization.id],
  );
  const supplier = organization.roles.some((role) =>
    supplierRoles.includes(role),
  );
  const location = useLocation();
  const segment=location.split("?")[0].split("/")[1];
  const view = (segment || (supplier ? "routes" : "bindings")) as View;
  const setView = useCallback((value:View | "catalog") => navigate(scoped(`/${value === "catalog" ? "routes" : value}`,organization.id)),[organization.id]);
  useEffect(()=>{
    if(!segment){window.history.replaceState(null,"",scoped(supplier?"/routes":"/bindings",organization.id));window.dispatchEvent(new Event("warehouse-navigation"));}
  },[segment,supplier,organization.id]);
  const [assistant, setAssistant] = useState(false);
  const [conversation, setConversation] = useState("");
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState("");
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const proposed = useCallback(() => {
    refresh();
    setNotice("变更已提交。请在审批中心核对内容后应用。");
    setView("approvals");
  }, [refresh,setView]);
  const published = useCallback(() => {
    refresh();
    setNotice("线路内容已发布。");
  }, [refresh]);
  const mayInventory = organization.roles.some((role) =>
    ["supplier_admin", "inventory_manager"].includes(role),
  );
  const canSync = organization.roles.some((role) =>
    ["supplier_admin", "auditor"].includes(role),
  );
  const nav = navigation.filter((item) => {
    if (item.id === "orders") return organization.roles.includes("supplier_admin");
    if (item.id === "invitations") return organization.roles.some(role => ["supplier_admin", "buyer_admin", "auditor"].includes(role));
    if (!supplier) return item.id === "bindings";
    if (item.id === "bindings")
      return (
        organization.roles.includes("supplier_admin") ||
        organization.roles.includes("buyer_admin")
      );
    if (["prices", "documents", "inquiries"].includes(item.id))
      return organization.roles.some((role) =>
        ["supplier_admin", "product_editor", "auditor"].includes(role),
      );
    return !["sync", "grants", "sources"].includes(item.id) || canSync;
  });
  return (
    <PortalShell
      brand={{
        mark: (
          <span className="badge good" style={{ fontSize: 20 }}>
            云
          </span>
        ),
        name: "Tour 云仓",
        detail: supplier ? "供应商工作台" : "采购管理工作台",
      }}
      nav={nav}
      view={view}
      onViewChange={(value) => {
        setView(value);
        setNotice("");
      }}
      operator={{
        name: organization.name,
        role: organization.roles.includes("supplier_admin")
          ? "供应商管理员"
          : supplier
            ? "供应商员工"
            : "采购管理员",
      }}
      assistantOpen={assistant}
      onToggleAssistant={() => setAssistant((value) => !value)}
      copy={ZH_CHROME_COPY}
      rail={
        <AssistantRail
          open={assistant}
          storageKey="tour-warehouse-rail"
          onClose={() => setAssistant(false)}
        >
          {(controls) => (
            <Assistant
              api={api}
              role={supplier ? "merchant" : "advisor"}
              conversation={conversation}
              onConversation={setConversation}
              onClose={controls.onClose}
              onRefresh={refresh}
              onApprovals={() => setView(supplier ? "approvals" : "bindings")}
            />
          )}
        </AssistantRail>
      }
    >
      <div className="stack">
        <header className="row between">
          <div>
            <div className="eyebrow">TOUR CLOUD WAREHOUSE</div>
            <h1>{nav.find((item) => item.id === view)?.label}</h1>
          </div>
          <div className="row">
            <select
              aria-label="当前组织"
              className="input"
              style={{ width: "auto", maxWidth: 280 }}
              value={organization.id}
              onChange={(event) => onSelect(event.target.value)}
            >
              {organizations.map((org) => (
                <option key={org.id} value={org.id}>
                  {org.name}
                </option>
              ))}
            </select>
            <button className="btn" onClick={refresh}>
              刷新
            </button>
            <button className="link" onClick={onLogout}>
              退出
            </button>
          </div>
        </header>
        {notice && (
          <div className="notice" role="status">
            {notice}
          </div>
        )}
        {view === "home" && (
          <Overview api={api} revision={revision} onNavigate={setView} />
        )}
        {supplier && ["routes","departures","orders"].includes(view) && (
          <Business api={api} location={segment ? location : scoped("/routes",organization.id)} revision={revision}
            onProposed={proposed} onPublished={published} publishable={organization.roles.includes("supplier_admin")} writable={organization.roles.some(role=>["supplier_admin","product_editor"].includes(role))}
            documents={organization.roles.some(role=>["supplier_admin","product_editor","auditor"].includes(role))}
            orders={organization.roles.includes("supplier_admin")} />
        )}
        {!nav.some(item=>item.id===view) && <p className="notice">此页面不存在或当前角色无权访问。</p>}
        {view === "inventory" && (
          <Inventory
            api={api}
            revision={revision}
            onProposed={proposed}
            writable={mayInventory}
          />
        )}
        {view === "imports" && (
          <Imports
            api={api}
            revision={revision}
            onProposed={proposed}
            onRefresh={refresh}
            writable={mayInventory}
          />
        )}
        {view === "documents" && <Documents api={api} revision={revision} onProposed={proposed}
          writable={organization.roles.some((role) => ["supplier_admin", "product_editor"].includes(role))} />}
        {view === "grants" && <Grants key={revision} api={api} writable={organization.roles.includes("supplier_admin")} onRefresh={()=>{refresh();setNotice("授权已更新，相关访问按最新授权校验。");}}/>}
        {view === "sources" && <Sources key={revision} api={api} writable={organization.roles.includes("supplier_admin")} onSaved={notice=>{refresh();setNotice(notice);}}/>}
        {view === "invitations" && <Invitations key={revision} api={api} supplierAdmin={organization.roles.includes("supplier_admin")} buyerAdmin={organization.roles.includes("buyer_admin")} />}
        {view === "inquiries" && <Inquiries key={organization.id} api={api} writable={organization.roles.some(role=>["supplier_admin","product_editor"].includes(role))}/>}
        {view === "approvals" && (
          <Approvals
            api={api}
            revision={revision}
            onRefresh={refresh}
            approver={organization.roles.includes("supplier_admin")}
          />
        )}
        {view === "prices" && (
          <Prices
            administrator={organization.roles.includes("supplier_admin")}
            api={api}
            revision={revision}
            writable={organization.roles.some((role) =>
              ["supplier_admin", "product_editor"].includes(role),
            )}
            onProposed={proposed}
          />
        )}
        {view === "bindings" && (
          <Bindings
            api={api}
            revision={revision}
            supplierAdmin={organization.roles.includes("supplier_admin")}
            buyerAdmin={organization.roles.includes("buyer_admin")}
            onProposed={proposed}
            onRefresh={refresh}
          />
        )}
        {view === "sync" && <SyncHistory api={api} revision={revision} writable={organization.roles.includes("supplier_admin")} />}
      </div>
    </PortalShell>
  );
}
