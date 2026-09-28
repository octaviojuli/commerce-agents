"use client";
export default function CatalogSearch({
  value,
  change,
  submit,
  busy,
  ready,
}: {
  value: string;
  change: (v: string) => void;
  submit: () => void;
  busy: boolean;
  ready: boolean;
}) {
  return (
    <div className="aw-catalog">
      <p>按当前需求检索，也可以临时换一个目的地，不会修改需求单。</p>
      <label>
        目的地或线路名称
        <input
          value={value}
          onChange={(e) => change(e.target.value)}
          placeholder="留空使用需求单目的地"
        />
      </label>
      <button className="aw-primary" disabled={busy || !ready} onClick={submit}>
        检索线路
      </button>
      {!ready && <p>请先补充需求单的目的地和出行时间。</p>}
    </div>
  );
}
