"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useState } from "react";
import { Chips, ErrorBox, Loading, Supplier, Top, WxIcon, useToast } from "@/components/ui";
import { api, copy, cover, money } from "@/lib/api";
import type { SupplierView } from "@/lib/types";

type Compare = {
  routes: { product_id: string; title: string; days: number | null; price: { per_person: number; date?: string } | null; supplier?: SupplierView }[];
  rows: { topic: string; concern: string; count: number; cells: { mark: "y" | "n" | "q"; text: string }[] }[];
  delta: { high: string; per_person: number; family: number | null; extras: string[] } | null;
  pros_cons: { title: string; good: string[]; tell: string[] }[];
  verdict: string;
  basis: string;
};

function ComparePage() {
  const { id } = useParams<{ id: string }>();
  const ids = (useSearchParams().get("ids") ?? "").split(",").filter(Boolean);
  const router = useRouter();
  const toast = useToast();
  const [data, setData] = useState<Compare | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.post<Compare>(`/deals/${id}/compare`, { product_ids: ids }).then(setData).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  const letters = ["A", "B", "C"];
  const forCustomer = data
    ? [
        ...data.routes.map((r, i) => `${letters[i]}：${r.title}${r.price ? ` · 每人约 ${money(r.price.per_person)}` : ""}`),
        ...data.rows.map((row) => `${row.concern}：` + row.cells.map((c, i) => `${letters[i]} ${c.mark === "y" ? "✓" : c.mark === "n" ? "✕" : "?"} ${c.text}`).join("；")),
        data.verdict,
      ].join("\n")
    : "";
  return (
    <div className="app">
      <Top
        title={data ? data.routes.map((r) => r.title.replace(/^ACME\s*/, "")).join(" vs ") : "线路比较"}
        sub="按客人最在意的几件事比"
        back={`/deals/${id}`}
      />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!data && !error && <Loading />}
          {data && (
            <>
              <div className="vs" style={data.routes.length > 2 ? { gridTemplateColumns: "1fr 1fr 1fr" } : undefined}>
                {data.routes.map((r, i) => (
                  <Fragment key={r.product_id}>
                    {i > 0 && data.routes.length === 2 && <span className="x">vs</span>}
                    <div>
                      <div className={"cv " + cover(r.title)} />
                      <b>
                        {letters[i]} {r.title}
                      </b>
                      <Supplier s={r.supplier} />
                      <span className="num">
                        {r.price ? `${money(r.price.per_person)}` : "待询价"}
                        {r.days ? ` · ${r.days} 天` : ""}
                      </span>
                    </div>
                  </Fragment>
                ))}
              </div>
              <div className="card concern">
                {data.rows.map((row, n) => (
                  <div className="cn" key={row.topic}>
                    <span className="q">
                      {"①②③④"[n]} {row.concern}
                      {row.count > 0 && <small>{n === 0 ? "最在意 · " : ""}提到 {row.count} 次</small>}
                    </span>
                    <div className="ab" style={data.routes.length > 2 ? { gridTemplateColumns: "1fr 1fr 1fr" } : undefined}>
                      {row.cells.map((c, i) => (
                        <span key={i} className={c.mark === "y" ? "y" : c.mark === "n" ? "n" : "q2"}>
                          {c.mark === "y" ? "✓ " : c.mark === "n" ? "✕ " : "? "}
                          {c.text || "资料没写明"}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              {data.delta && (
                <div className="card delta">
                  <b>
                    {data.delta.high} 多花 {money(data.delta.per_person)}/人{data.delta.family ? `（全家 ${money(data.delta.family)}）` : ""}，多了什么
                  </b>
                  {data.delta.extras.map((e) => (
                    <span key={e}>· {e}</span>
                  ))}
                </div>
              )}
              {!data.delta && <div className="card delta lbl">两条都询价后，这里会先回答“贵的那条多了什么”。</div>}
              <div className="pc">
                {data.pros_cons.map((p, i) => (
                  <div className="card" key={p.title}>
                    <b>{letters[i]} 的好处</b>
                    <span className="p">{p.good.join("；") || "资料里看不出明显优势"}</span>
                    <b style={{ marginTop: 6 }}>{letters[i]} 要说清的</b>
                    <span className="c">{p.tell.join("；") || "暂无"}</span>
                  </div>
                ))}
              </div>
              {data.verdict && (
                <div className="verdict2">
                  <b>建议</b>
                  {data.verdict}
                  <small>{data.basis}</small>
                </div>
              )}
              <Chips
                items={[{ label: "用这个比较写推荐", primary: true }, { label: "只看 A 的行程" }]}
                onPick={(i) => {
                  if (i === 0) router.push(`/deals/${id}/plan?ids=${ids.join(",")}`);
                  else router.push(`/routes/${data.routes[0].product_id}?deal=${id}`);
                }}
              />
            </>
          )}
        </div>
      </div>
      {data && (
        <div className="dock">
          <div className="grow">
            <button
              className="b b-wx"
              onClick={async () => {
                if (await copy(forCustomer)) toast("比较已复制，去微信粘贴");
              }}
            >
              <WxIcon />
              复制比较发给客人
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <ComparePage />
    </Suspense>
  );
}
