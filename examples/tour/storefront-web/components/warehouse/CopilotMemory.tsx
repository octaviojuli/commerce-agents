"use client";
import type { DealDetail } from "./copilot-types";
import { fieldNames } from "./BriefPanel";
export default function CopilotMemory({
  detail,
  busy,
  run,
}: {
  detail: DealDetail;
  busy: boolean;
  run: (path: string, body?: any) => Promise<any>;
}) {
  const memory = detail.memory || {};
  return (
    <>
      <section className="cp-panel">
        <h3>客人记忆</h3>
        {(memory.concerns || []).map((c: any) => (
          <details key={c.topic}>
            <summary>
              {c.topic} · 提到 {c.count} 次
            </summary>
            {c.sources.map((s: any) => (
              <p key={s.record_id}>
                {s.text}
                <small> · v{s.version} 原话</small>
              </p>
            ))}
          </details>
        ))}
        {!memory.concerns?.length && (
          <p className="cp-muted">提到的顾虑会记在这里。</p>
        )}
        {memory.avoid?.length > 0 && <p>要避开：{memory.avoid.join("；")}</p>}
        {memory.habits?.length > 0 && (
          <p>沟通习惯：{memory.habits.join("；")}</p>
        )}
        <details>
          <summary>我说过的话 · {memory.sent?.length || 0}</summary>
          {(memory.sent || []).map((s: any) => (
            <div key={s.id} className="cp-record">
              <p>{s.text}</p>
              {s.correction && (
                <p className="cp-error">
                  {s.correction}
                  <br />
                  {s.correction_draft}
                </p>
              )}
            </div>
          ))}
        </details>
      </section>
      <section className="cp-panel">
        <h3>需求演变</h3>
        <div className="cp-timeline">
          {(memory.timeline || []).map((v: any) => (
            <div key={v.record_id}>
              <b>v{v.version}</b>
              <small>
                {v.fields.map((k: string) => fieldNames[k] || k).join("、") ||
                  "初始需求"}
              </small>
              <button
                disabled={busy || v.version === detail.brief.version}
                onClick={() =>
                  run(
                    "/copilot/deals/" +
                      detail.id +
                      "/versions/" +
                      v.record_id +
                      "/revert",
                  )
                }
              >
                回到此需求
              </button>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
