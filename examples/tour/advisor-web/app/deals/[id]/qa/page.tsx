"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorBox, Loading, Top, useToast } from "@/components/ui";
import { api, copy } from "@/lib/api";
import type { Answer } from "@/lib/types";

type Book = {
  route: { product_id: string; title: string } | null;
  items: { id: string; topic: string; question: string; answer: string; facts: { section: string }[]; status: string; count: number; critical: boolean; questions: string[]; updated_at: string }[];
  said: { id: string; text: string; status: string; at: string }[];
  outdated: number;
};

export default function QaPage() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const [book, setBook] = useState<Book | null>(null);
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Book>(`/deals/${id}/qa`).then(setBook).catch((e) => setError(e.message));
  }, [id]);
  const critical = book?.items.filter((i) => i.critical) ?? [];
  const outdated = book?.said.filter((s) => s.status === "outdated") ?? [];
  const total = book?.items.reduce((n, i) => n + i.count, 0) ?? 0;
  return (
    <div className="app">
      <Top title={`问过的问题${book?.route ? " · " + book.route.title : ""}`} sub={book ? `${total} 个问题 · ${book.items.length} 个主题` : ""} back={`/deals/${id}`} />
      <div className="sc">
        <div className="pad" style={{ paddingTop: 12 }}>
          <ErrorBox error={error} />
          {!book && !error && <Loading />}
          {critical.map((c) => (
            <div className="card insight" key={c.id}>
              <b>
                “{c.topic}”她问了 {c.count} 次，是这一单的关键
              </b>
              <span>建议整理成一段话一次讲清，发给客人。</span>
              <div className="row" style={{ gap: 6, marginTop: 6 }}>
                <button
                  className="b sm b-br"
                  onClick={async () => {
                    const text = `关于${c.topic}，一次给您说清楚：\n` + book!.items.filter((i) => i.topic === c.topic).map((i) => i.answer).join("\n");
                    if (await copy(text)) toast("已复制，去微信粘贴");
                  }}
                >
                  生成一次讲清的话
                </button>
              </div>
            </div>
          ))}
          {outdated.map((s) => (
            <div className="alert" key={s.id} style={{ background: "var(--sun-soft)", color: "var(--sun-ink)" }}>
              <span>⚠</span>
              <span>
                <b style={{ color: "var(--sun-ink)" }}>要更正一句话</b>：{s.at.slice(5, 10)} 你说“{s.text}”，这条线的资料已经更新，原来的依据不在了。
                <button
                  className="b sm b-soft"
                  style={{ marginTop: 6 }}
                  onClick={async () => {
                    if (await copy(`之前跟您说的“${s.text}”，供应商资料有更新，我核对后马上发您最新的说明。`)) toast("更正话术已复制");
                  }}
                >
                  复制更正话术
                </button>
              </span>
            </div>
          ))}
          {book && !book.items.length && <div className="empty">客人问过的问题会按主题记在这里。</div>}
          {book?.items.map((i) => (
            <div className="card qb" key={i.id}>
              <div className="tp">
                {i.topic}
                <span className={"tag " + (i.count >= 3 ? "t-sun" : "t-gray")}>{i.count} 次</span>
              </div>
              <div className="qi">
                <span className="grow">{i.question}</span>
                <span className="lbl">{i.updated_at.slice(5, 10).replace("-", "/")}</span>
              </div>
              <div className="an">
                {i.answer || "还没有答案"}{" "}
                {i.status === "unknown" ? (
                  <span className="tag t-sun">待核实</span>
                ) : (
                  [...new Set(i.facts.map((f) => f.section))].map((s) => (
                    <span className="src" key={s}>
                      {s}
                    </span>
                  ))
                )}
              </div>
              {i.questions.length > 1 && <span className="lbl">也问过：{i.questions.slice(0, -1).join("；")}</span>}
            </div>
          ))}
          {answer && (
            <div className="card qa">
              <div className={"it" + (answer.kind === "unknown" ? " unk" : "")}>
                <span className="q">{answer.question}</span>
                <span className="a">{answer.answer}</span>
              </div>
            </div>
          )}
        </div>
      </div>
      {book?.route && (
        <form
          className="composer"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!q.trim()) return;
            const r = await api.post<{ items: Answer[] }>(`/routes/${book.route!.product_id}/ask`, { question: q, deal_id: id });
            setAnswer(r.items[0] ?? null);
            setQ("");
          }}
        >
          <textarea aria-label="问这条线" rows={1} value={q} onChange={(e) => setQ(e.target.value)} placeholder="问这条线：比如“签证要几天”" />
          <button className="p">问</button>
        </form>
      )}
    </div>
  );
}
