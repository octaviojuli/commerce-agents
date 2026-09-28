"use client";
import { useEffect, useState, type FormEvent } from "react";
import type { WarehouseClient } from "web-shared/warehouse-client";
import type { DealDetail, Inquiry } from "./copilot-types";
import { confirmationItems, recordLabels } from "./copilot-types";
import { type WorkbenchAction, amount, stamp } from "@/lib/warehouse";
import { QuoteCard } from "./Quote";
import RoutePreview from "./RoutePreview";
import { copyText } from "./mobile";
import CopilotMaterialReview from "./CopilotMaterialReview";
import CopilotCustomer from "./CopilotCustomer";
import FormSheet from "./CopilotFormSheet";
import { ComparisonCard, displayRoute, stripCodes } from "./CopilotViews";

type Props = {
  api: WarehouseClient;
  detail: DealDetail;
  tab: string;
  inquiries: Inquiry[];
  busy: boolean;
  run: (path: string, body?: Record<string, unknown>) => Promise<any>;
  act: (a: WorkbenchAction) => Promise<any>;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
  fail: (s: string) => void;
};
const form = (event: FormEvent<HTMLFormElement>) => {
  event.preventDefault();
  return new FormData(event.currentTarget);
};

export default function CopilotBusiness({
  api,
  detail,
  tab,
  inquiries,
  busy,
  run,
  act,
  refresh,
  notify,
  fail,
}: Props) {
  const [nodes, setNodes] = useState<any[]>([]),
    [revealed, setRevealed] = useState<Record<number, string>>({});
  useEffect(() => {
    const abort = new AbortController();
    api
      .get<{ facts: any[] }>(
        "/copilot/deals/" + detail.id + "/nodes",
        abort.signal,
      )
      .then((p) =>
        setNodes(p.facts.filter((f) => f.node_id && f.scope?.publication_id)),
      )
      .catch(() => setNodes([]));
    return () => abort.abort();
  }, [
    api,
    detail.id,
    detail.brief.body.route_id,
    detail.brief.body.departure_id,
  ]);
  const prefix = `/copilot/deals/${detail.id}`;
  const brief = detail.brief.body,
    version = detail.brief.version,
    quote = brief.quote;
  const [salePrice, setSalePrice] = useState(""),
    [link, setLink] = useState(""),
    [uploading, setUploading] = useState(false),
    [showDocument, setShowDocument] = useState(false);
  const retail = detail.records.find(
      (r) => r.kind === "retail_quote" && !r.stale,
    ),
    sale = detail.ledger?.sale || detail.records.find((r) => r.kind === "sale");
  const confirmation = detail.records.find(
    (r) => r.kind === "confirmation" && !r.stale,
  );
  const receipts =
    detail.ledger?.receipts ||
    detail.records.filter((r) => r.kind === "receipt");
  const received = detail.ledger
    ? Number(detail.ledger.received)
    : receipts.reduce(
        (s, r) =>
          s + Number(r.body.amount) * (r.body.category === "refund" ? -1 : 1),
        0,
      );
  const adopted = new Set(
    detail.records
      .filter((r) => r.kind === "inquiry_adoption")
      .map((r) => r.body.reply_id),
  );
  useEffect(() => {
    setLink("");
  }, [version, retail?.id]);
  async function submitNote(event: FormEvent<HTMLFormElement>) {
    const data = form(event);
    const result = await run(`${prefix}/notes`, {
      kind: data.get("kind"),
      text: data.get("text"),
      category: data.get("category") || "advisor",
      product_id: brief.route_id,
      ...(data.get("node")
        ? {
            node_id: nodes[Number(data.get("node"))].node_id,
            publication_id:
              nodes[Number(data.get("node"))].scope.publication_id,
          }
        : {}),
      amount: data.get("amount") ? String(data.get("amount")) : null,
    });
    if (result) {
      (event.target as HTMLFormElement).reset();
      notify("已保存到本次跟单。");
    }
  }
  if (tab === "quote")
    return (
      <div className="cp-business-grid">
        <section className="cp-panel">
          <span className="cp-eyebrow">当前方案</span>
          <h2>
            {brief.route_title
              ? displayRoute(brief.route_title)
              : "还没有选定线路"}
          </h2>
          {brief.route_id && (
            <button
              disabled={busy}
              onClick={() =>
                run(prefix + "/plans", { product_ids: [brief.route_id] })
              }
            >
              整理当前线路方案
            </button>
          )}
          {link.includes("/plan#") && (
            <a href={link} target="_blank" rel="noreferrer">
              打开客人方案预览
            </a>
          )}
          <p>
            {brief.departure_title
              ? stripCodes(brief.departure_title)
              : "先与搭档沟通，选择合适的线路和团期。"}
          </p>
          {brief.route_id && (
            <RoutePreview api={api} productId={brief.route_id} />
          )}
          <div className="cp-row">
            {detail.brief.readiness.search.ready && (
              <button
                disabled={busy}
                onClick={() => act({ action: "search_routes" })}
              >
                按需求重新找线
              </button>
            )}
            {brief.route_id && (
              <button
                disabled={busy}
                onClick={() =>
                  act({ action: "departures", product_id: brief.route_id })
                }
              >
                重新查看团期
              </button>
            )}
          </div>
          <p className="cp-muted">
            查询结果会保存在沟通页。方案和历史报价都保留版本。
          </p>
        </section>
        {detail.records
          .filter((r) => r.kind === "plan")
          .map((r) => (
            <article className="cp-panel" key={r.id}>
              <span className="cp-tag">
                方案 v{String(r.body.plan_version || 1)}
                {r.stale ? " · 需更新" : ""}
              </span>
              <p className="cp-pre">{r.body.text}</p>
              {r.body.routes && <ComparisonCard data={r.body} />}
              <p className="cp-muted">{r.body.limitation}</p>
              {r.body.routes && (
                <button
                  disabled={busy || r.stale}
                  onClick={async () => {
                    const shared = await run(
                      prefix + "/plans/" + r.id + "/share",
                    );
                    if (shared?.token) {
                      const url = location.origin + "/plan#" + shared.token;
                      setLink(url);
                      notify(
                        (await copyText(url))
                          ? "客人方案链接已复制。"
                          : "已生成链接，请打开预览。",
                      );
                    }
                  }}
                >
                  生成客人方案链接
                </button>
              )}
            </article>
          ))}
        <section className="cp-panel">
          <div className="cp-section-head">
            <h2>统一结算价</h2>
            {brief.departure_id && (
              <button
                disabled={busy || !detail.brief.readiness.quote.ready}
                onClick={() =>
                  act({
                    action: "quote",
                    product_id: brief.departure_id!,
                    ...(brief.offer_id ? { offer_id: brief.offer_id } : {}),
                  })
                }
              >
                重新核价
              </button>
            )}
          </div>
          {quote ? (
            <QuoteCard
              quote={{
                ...quote,
                snapshot_stale:
                  detail.brief.quote_stale || quote.snapshot_stale,
              }}
            />
          ) : (
            <p className="cp-muted">
              选择团期方案、补齐人数和房型后，再查询结算价。
            </p>
          )}
        </section>
        {quote && (
          <section className="cp-panel">
            <span className="cp-eyebrow">正式报价前</span>
            <h2>把关键事项，逐项确认</h2>
            <p className="cp-muted">
              请先核对完整行程和费用条件，再记录客人的确认原话。未知事项需要先向商户核实。
            </p>
            {confirmation ? (
              <p className="cp-notice">
                已记录当前 v{version} 的确认。原话：{confirmation.body.evidence}
              </p>
            ) : (
              <FormSheet title="核对确认单">
                <form
                  onSubmit={async (e) => {
                    const data = form(e);
                    await run(`${prefix}/confirm`, {
                      confirmed_items: data.getAll("confirmed"),
                      evidence: data.get("evidence"),
                    });
                  }}
                >
                  {confirmationItems.map((item) => (
                    <label className="cp-check" key={item}>
                      <input
                        type="checkbox"
                        name="confirmed"
                        value={item}
                        required
                        disabled={busy}
                      />
                      {item}
                    </label>
                  ))}
                  <label>
                    客人确认原话
                    <textarea
                      name="evidence"
                      placeholder="粘贴客人已确认的内容"
                      required
                      maxLength={1000}
                    />
                  </label>
                  <button
                    className="cp-primary"
                    disabled={busy || detail.brief.quote_stale}
                  >
                    {detail.brief.quote_stale
                      ? "重新询价后确认"
                      : "保存当前版本确认单"}
                  </button>
                </form>
              </FormSheet>
            )}
          </section>
        )}
        {quote && (
          <section className="cp-panel cp-price-panel">
            <span className="cp-eyebrow">我的销售报价</span>
            <h2>销售价与利润</h2>
            <FormSheet title="编辑销售报价">
              <form
                onSubmit={async (e) => {
                  const data = form(e);
                  const result = await run(`${prefix}/retail-quotes`, {
                    sales_total: String(data.get("price")),
                    loss_confirmed: data.get("loss") === "yes",
                  });
                  if (result)
                    notify("正式销售报价已保存。利润按销售价减结算价计算。");
                }}
              >
                <label>
                  销售总价 · {quote.currency}
                  <input
                    key={quote.quote_id}
                    type="number"
                    name="price"
                    min="0"
                    step="0.01"
                    required
                    defaultValue={quote.market_total || ""}
                    onChange={(e) => setSalePrice(e.target.value)}
                  />
                </label>
                <div className="cp-price-summary">
                  <span>
                    结算总价
                    <b>{amount(quote.settlement_total, quote.currency)}</b>
                  </span>
                  <span>
                    预计利润
                    <b>
                      {quote.settlement_total !== null &&
                      (salePrice || quote.market_total)
                        ? `${quote.currency} ${(Number(salePrice || quote.market_total) - Number(quote.settlement_total)).toFixed(2)}`
                        : "待核实"}
                    </b>
                  </span>
                </div>
                <label className="cp-check">
                  <input type="checkbox" name="loss" value="yes" />
                  若低于结算价，我确认本次亏损销售
                </label>
                <button
                  className="cp-primary"
                  disabled={
                    busy ||
                    !confirmation ||
                    detail.brief.quote_stale ||
                    !quote.complete
                  }
                >
                  {detail.brief.quote_stale ? "重新询价" : "保存正式报价"}
                </button>
              </form>
            </FormSheet>
            <p className="cp-muted">
              默认使用市场销售价，可自行调整。报价单默认有效 24
              小时；五分钟后显示核价时间，仍可分享和确认。
            </p>
            {retail && (
              <div className="cp-retail">
                <p>
                  已保存销售总价{" "}
                  <strong>
                    {amount(retail.body.sales_total, retail.body.currency)}
                  </strong>
                </p>
                <p>
                  利润 {amount(retail.body.profit, retail.body.currency)} ·
                  毛利率 {retail.body.margin || "—"}%
                </p>
                {(retail.body.additional_items || []).map(
                  (item: any, n: number) => (
                    <p key={n}>
                      另付：{item.text} ·{" "}
                      {item.amount
                        ? amount(item.amount, item.currency)
                        : "待核实"}
                    </p>
                  ),
                )}
                <button
                  disabled={busy}
                  onClick={async () => {
                    const result = await run(
                      `${prefix}/retail-quotes/${retail.id}/share`,
                    );
                    if (result?.token) {
                      setLink(`${location.origin}/quote#${result.token}`);
                      notify("对客链接已创建，核对后再发送。");
                    } else if (result)
                      notify("链接已创建过；原令牌不再返回，请另建一次分享。");
                  }}
                >
                  生成对客报价链接
                </button>
                {link && (
                  <div className="cp-share">
                    <a href={link} target="_blank" rel="noreferrer">
                      打开客户报价预览 ↗
                    </a>
                    <button
                      onClick={async () => {
                        try {
                          await copyText(link);
                          notify("链接已复制，请自行发送给客人。");
                        } catch {
                          fail("复制未完成，请手动复制链接。");
                        }
                      }}
                    >
                      复制链接
                    </button>
                  </div>
                )}
              </div>
            )}
          </section>
        )}
        <section className="cp-panel">
          <h2>报价与确认历史</h2>
          {detail.records
            .filter((r) => ["retail_quote", "confirmation"].includes(r.kind))
            .map((r) => (
              <div className="cp-record" key={r.id}>
                <b>
                  {recordLabels[r.kind]} · v{r.brief_version}
                  {r.stale ? " · 已过期" : ""}
                </b>
                <small>{stamp(r.created_at)}</small>
                <p>
                  {r.body.sales_total
                    ? amount(r.body.sales_total, r.body.currency)
                    : r.body.evidence}
                </p>
              </div>
            ))}
          <p className="cp-muted">占位和下单将在二期接入。</p>
        </section>
      </div>
    );
  return (
    <div className="cp-business-grid">
      <section className="cp-panel">
        <span className="cp-eyebrow">顾问 ↔ 商户</span>
        <h2>把不确定的细节查清楚</h2>
        <FormSheet title="向商户提交核实">
          <form
            onSubmit={async (e) => {
              const data = form(e);
              const result = await run(`${prefix}/inquiries`, {
                product_id: brief.route_id || data.get("product"),
                question: data.get("question"),
              });
              if (result) notify("核实单已送达该线路所属商户。");
            }}
          >
            {!brief.route_id ? (
              <p className="cp-muted">
                选定一条线路后，可直接向该商户提交核实问题。
              </p>
            ) : (
              <>
                <p>{displayRoute(brief.route_title)}</p>
                <label>
                  需要商户核实的问题
                  <textarea
                    name="question"
                    required
                    maxLength={2000}
                    placeholder="例如：这次出行的儿童不占床，具体费用和适用条件是什么？"
                  />
                </label>
                <p className="cp-muted">
                  将提交问题、所选线路及必要的人数、房型、日期。不包含客户联系方式、你的销售价和利润。
                </p>
                <button className="cp-primary" disabled={busy}>
                  提交商户核实
                </button>
              </>
            )}
          </form>
        </FormSheet>
        {inquiries.map((i) => (
          <article className="cp-inquiry" key={i.id}>
            <div className="cp-section-head">
              <span className="cp-tag">
                {i.status === "adopted"
                  ? "已采纳到本单"
                  : i.status === "replied"
                    ? "商户已回复"
                    : "等待商户回复"}
              </span>
              <small>需求 v{i.brief_version}</small>
            </div>
            <h3>{i.question}</h3>
            {i.replies.map((r) => (
              <div className="cp-merchant-reply" key={r.id}>
                <span>商户回复 · {stamp(r.created_at)}</span>
                <p>{r.answer}</p>
                {adopted.has(r.id) ? (
                  <b className="cp-tag">已采纳到本单</b>
                ) : (
                  <button
                    disabled={busy || Boolean(i.stale_reason)}
                    onClick={() => run(`${prefix}/replies/${r.id}/adopt`)}
                  >
                    核对后采纳
                  </button>
                )}
              </div>
            ))}
            {i.stale_reason && <p className="cp-muted">{i.stale_reason}</p>}
          </article>
        ))}
      </section>
      <section className="cp-panel">
        <h2>问答簿与行程备注</h2>
        {(detail.memory?.qa || []).map((q: any) => (
          <article className="cp-record" key={q.product_id + q.topic}>
            <div className="cp-section-head">
              <b>{q.topic}</b>
              <span className="cp-tag">
                {q.count} 次{q.critical ? " · 关键" : ""}
              </span>
            </div>
            <p>{q.questions.join("；")}</p>
            <p>{q.answer}</p>
            <button
              disabled={busy}
              onClick={async () => {
                const result = await run(prefix + "/explain", {
                  topic: q.topic,
                  product_id: q.product_id,
                });
                if (result)
                  notify(
                    (await copyText(result.text))
                      ? "一次讲清材料已复制，发送前请核对。"
                      : "请重新复制。",
                  );
              }}
            >
              生成一次讲清材料
            </button>
          </article>
        ))}
        {detail.records
          .filter((r) => ["qa", "note", "inquiry_adoption"].includes(r.kind))
          .map((r) => (
            <article className="cp-record" key={r.id}>
              <span className="cp-tag">
                {recordLabels[r.kind]}
                {r.body.status === "pending" ? " · 待核实" : ""}
              </span>
              {r.body.questions?.length > 0 && (
                <h3>{r.body.questions.join("；")}</h3>
              )}
              <p className="cp-pre">{r.body.answer || r.body.text}</p>
              {r.stale && <small>依据或需求已变化，发送前请重新核对。</small>}
            </article>
          ))}
        <FormSheet title="添加行程记录">
          <form onSubmit={submitNote}>
            <label>
              保存到
              <select name="kind">
                <option value="note">行程备注</option>
                <option value="memory">客人记忆</option>
                <option value="qa">问答簿</option>
                <option value="task">出发前待办</option>
              </select>
            </label>
            <label>
              备注类型
              <select name="category">
                <option value="advisor">顾问备注（节点备注进行前待办）</option>
                <option value="fee">费用（进报价另付）</option>
                <option value="special">特殊需求（提交商户核实）</option>
                <option value="question">客人问题（进问答簿）</option>
              </select>
            </label>
            <label>
              行程节点
              <select name="node">
                <option value="">一般记录</option>
                {nodes.map((n, i) => (
                  <option key={n.fact_id} value={i}>
                    {n.text.slice(0, 65)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              另付金额（费用备注，可留空待核实）
              <input type="number" name="amount" min="0" step="0.01" />
            </label>
            <label>
              内容
              <textarea name="text" required maxLength={4000} />
            </label>
            <button disabled={busy}>保存记录</button>
          </form>
        </FormSheet>
      </section>
      <section className="cp-panel">
        <span className="cp-eyebrow">手工台账</span>
        <h2>线下成交与收款</h2>
        <p className="cp-muted">
          这里记录你在线下完成的销售与收款，不代表平台收款、供应商确认或库存占用。
        </p>
        {!sale ? (
          <FormSheet title="登记线下成交">
            <form
              onSubmit={async (e) => {
                const data = form(e);
                if (retail)
                  await run(`${prefix}/sales`, {
                    retail_quote_id: retail.id,
                    supplier_order_number: data.get("number"),
                    note: data.get("note"),
                  });
              }}
            >
              <label>
                供应商订单号（选填）
                <input name="number" maxLength={100} />
              </label>
              <label>
                成交备注
                <textarea name="note" maxLength={2000} />
              </label>
              <button disabled={busy || !retail} className="cp-primary">
                登记线下成交
              </button>
              {!retail && (
                <small>先在“方案与报价”保存当前有效的正式报价。</small>
              )}
            </form>
          </FormSheet>
        ) : (
          <>
            <div className="cp-price-summary">
              <span>
                成交总价
                <b>{amount(sale.body.sales_total, sale.body.currency)}</b>
              </span>
              <span>
                已登记净收款
                <b>
                  {sale.body.currency} {received.toFixed(2)}
                </b>
              </span>
              <span>
                待收余额
                <b>
                  {sale.body.currency}{" "}
                  {(Number(sale.body.sales_total) - received).toFixed(2)}
                </b>
              </span>
            </div>
            <p>供应商订单号：{sale.body.supplier_order_number || "未登记"}</p>
            <FormSheet title="登记收付款">
              <form
                onSubmit={async (e) => {
                  const data = form(e);
                  await run(`${prefix}/receipts`, {
                    sale_id: sale.id,
                    amount: String(data.get("amount")),
                    category: data.get("category"),
                    received_on: data.get("date"),
                    reference: data.get("reference"),
                  });
                }}
              >
                <div className="cp-fields">
                  <label>
                    记录类型
                    <select name="category">
                      <option value="deposit">定金</option>
                      <option value="balance">尾款</option>
                      <option value="refund">退款</option>
                    </select>
                  </label>
                  <label>
                    金额
                    <input
                      type="number"
                      name="amount"
                      min="0.01"
                      step="0.01"
                      required
                    />
                  </label>
                </div>
                <label>
                  实际收付款日期
                  <input type="date" name="date" required />
                </label>
                <label>
                  凭证或备注
                  <input name="reference" required maxLength={500} />
                </label>
                <button disabled={busy}>登记这笔收付款</button>
              </form>
            </FormSheet>
            {receipts.map((r) => (
              <p className="cp-record" key={r.id}>
                {
                  (
                    {
                      deposit: "定金",
                      balance: "尾款",
                      refund: "退款",
                    } as Record<string, string>
                  )[r.body.category]
                }{" "}
                · {amount(r.body.amount, r.body.currency)} ·{" "}
                {r.body.received_on}
                <br />
                <small>{r.body.reference}</small>
              </p>
            ))}
          </>
        )}
      </section>
      <CopilotCustomer api={api} detail={detail} busy={busy} run={run} />
      <section className="cp-panel">
        <h2>旅客与材料</h2>
        <p className="cp-muted">
          客户联系人和实际出行旅客分别管理。材料保存在私有文件库，只有你可以访问。
        </p>
        {detail.customer ? (
          <>
            <button
              onClick={async () => {
                if (showDocument) {
                  setShowDocument(false);
                  setRevealed({});
                  return;
                }
                try {
                  const values = await Promise.all(
                    detail.customer!.body.travelers.map((_, i) =>
                      api.post<{ document_number: string }>(
                        "/copilot/customers/" +
                          detail.customer!.id +
                          "/travelers/" +
                          i +
                          "/reveal",
                        {},
                      ),
                    ),
                  );
                  setRevealed(
                    Object.fromEntries(
                      values.map((v, i) => [i, v.document_number]),
                    ),
                  );
                  setShowDocument(true);
                } catch {
                  fail("证件读取失败，请重新核对权限。");
                }
              }}
            >
              {showDocument ? "隐藏证件号码" : "显示我的旅客证件"}
            </button>
            {detail.customer.body.travelers.map((t, n) => (
              <p className="cp-record" key={n}>
                {t.name} · {t.birthday || "生日待确认"}
                <br />
                {t.document_number
                  ? showDocument
                    ? revealed[n]
                    : `•••• ${t.document_number.slice(-4)}`
                  : "证件待补充"}{" "}
                · {t.confirmed ? "已人工核对" : "待核对"}
              </p>
            ))}
            <details>
              <summary>登记一位旅客</summary>
              <FormSheet title="登记旅客">
                <form
                  onSubmit={async (e) => {
                    const data = form(e);
                    const result = await run(
                      `/copilot/customers/${detail.customer!.id}`,
                      {
                        expected_version: detail.customer!.version,
                        customer: {
                          ...detail.customer!.body,
                          travelers: [
                            ...detail.customer!.body.travelers,
                            {
                              name: String(data.get("name")),
                              birthday: data.get("birthday") || null,
                              document_type: data.get("type"),
                              document_number: String(
                                data.get("document") || "",
                              ),
                              document_expiry: data.get("expiry") || null,
                              confirmed: data.get("confirmed") === "yes",
                            },
                          ],
                        },
                      },
                    );
                    if (result) notify("旅客已保存到客户档案。");
                  }}
                >
                  <label>
                    姓名
                    <input name="name" required maxLength={100} />
                  </label>
                  <label>
                    出生日期
                    <input name="birthday" type="date" />
                  </label>
                  <label>
                    证件类型
                    <select name="type">
                      <option value="passport">护照</option>
                      <option value="identity">身份证</option>
                      <option value="other">其他</option>
                    </select>
                  </label>
                  <label>
                    证件号
                    <input name="document" maxLength={80} autoComplete="off" />
                  </label>
                  <label>
                    证件有效期
                    <input name="expiry" type="date" />
                  </label>
                  <label className="cp-check">
                    <input type="checkbox" name="confirmed" value="yes" />
                    已对照原件人工核对
                  </label>
                  <button disabled={busy}>保存旅客</button>
                </form>
              </FormSheet>
            </details>
          </>
        ) : (
          <p>此跟单尚未关联客户，请回工作台先建立客户档案。</p>
        )}
        <p className="cp-muted">
          先选定团期再上传。证件原图与识别候选加密保存，原图在行程结束后保留 90
          天，到期清理。
        </p>
        <FormSheet title="上传旅行材料">
          <label className="cp-upload">
            上传旅行材料
            <input
              type="file"
              accept="application/pdf,image/png,image/jpeg"
              disabled={uploading || busy || !brief.departure_id}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                if (file.size > 20_000_000) {
                  fail("文件不能超过 20 MB");
                  return;
                }
                setUploading(true);
                try {
                  const data = new FormData();
                  data.set("file", file);
                  data.set("request_id", crypto.randomUUID());
                  await api.response(`${prefix}/assets`, {
                    method: "POST",
                    body: data,
                  });
                  await refresh();
                  notify("材料已保存，识别和人工核对不会自动修改旅客信息。");
                } catch (e) {
                  fail(e instanceof Error ? e.message : "上传失败");
                } finally {
                  setUploading(false);
                }
              }}
            />
          </label>
        </FormSheet>
        {uploading && <p role="status">正在保存私有材料…</p>}
        {detail.assets.map((a) => (
          <div className="cp-record" key={a.id}>
            <span>{a.filename}</span>
            <button
              onClick={() =>
                api
                  .download(`/copilot/assets/${a.id}`, a.filename)
                  .catch(() => fail("材料不可读取，请刷新后重试。"))
              }
            >
              查看材料
            </button>
            <button
              disabled={busy}
              onClick={() => run(`${prefix}/assets/${a.id}/recognize`)}
            >
              识别证件
            </button>
          </div>
        ))}
        {detail.records
          .filter((r) => r.kind === "material_review")
          .map((r) => (
            <CopilotMaterialReview
              key={r.id}
              record={r}
              customer={detail.customer}
              busy={busy}
              run={run}
              notify={notify}
            />
          ))}
      </section>
      <section className="cp-panel">
        <h2>出发前清单</h2>
        {detail.records
          .filter((r) => r.kind === "task")
          .map((r) => {
            const done = detail.records.some(
              (d) => d.kind === "task_done" && d.body.task_id === r.id,
            );
            return (
              <div className="cp-record" key={r.id}>
                <p>
                  {done ? "✓ " : "○ "}
                  {r.body.text}
                </p>
                <button
                  disabled={busy || done}
                  onClick={() => run(`${prefix}/tasks/${r.id}/complete`)}
                >
                  {done ? "已完成" : "标记完成"}
                </button>
              </div>
            );
          })}
        <p className="cp-muted">
          可在上方“保存记录”中添加材料、尾款、航班和行前提醒待办。
        </p>
      </section>
    </div>
  );
}
