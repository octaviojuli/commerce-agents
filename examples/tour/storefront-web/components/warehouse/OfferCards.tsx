"use client";
import { useState } from "react";
import type { WorkbenchAction } from "@/lib/warehouse";
export default function OfferCards({
  payload,
  busy,
  act,
  more,
}: {
  payload: any;
  busy: boolean;
  act: (a: WorkbenchAction) => unknown;
  more: (a: WorkbenchAction) => Promise<any>;
}) {
  const [items, setItems] = useState<any[]>(payload.items ?? []),
    [cursor, setCursor] = useState(payload.next_cursor),
    [loading, setLoading] = useState(false);
  return (
    <div className="aw-offers">
      {items.map((o) => (
        <article key={o.offer_id}>
          <h3>{o.name}</h3>
          <p>{o.service_description || "服务说明待确认"}</p>
          <button
            className="aw-primary"
            disabled={busy || loading}
            onClick={() =>
              act({
                action: "quote",
                product_id: payload.departure_id,
                offer_id: o.offer_id,
              })
            }
          >
            选择此方案询价
          </button>
        </article>
      ))}
      {cursor && (
        <button
          disabled={busy || loading}
          onClick={async () => {
            setLoading(true);
            try {
              const r = await more({
                action: "offers",
                product_id: payload.departure_id,
                after: cursor,
              });
              if (r) {
                setItems((old) => [
                  ...old,
                  ...r.items.filter(
                    (v: any) => !old.some((o) => o.offer_id === v.offer_id),
                  ),
                ]);
                setCursor(r.next_cursor);
              }
            } finally {
              setLoading(false);
            }
          }}
        >
          {loading ? "正在读取…" : "更多报价方案"}
        </button>
      )}
    </div>
  );
}
