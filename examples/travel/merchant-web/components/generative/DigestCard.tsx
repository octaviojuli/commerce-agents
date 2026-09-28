// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { DigestList, DigestRow, formatMoney, formatNumber, GenCard, GenCardHeader, type IconName, type Tone, useCopy } from "web-shared";
import { INVENTORY_KINDS, inventoryPrompt } from "@/lib/kinds";
import type { DigestEntry, DigestPayload } from "@/lib/types";

const KINDS: Record<DigestEntry["kind"], { icon: IconName; tone: Tone }> = {
  low_stock: INVENTORY_KINDS.low_stock,
  slow_mover: INVENTORY_KINDS.slow_mover,
  order_issue: { icon: "calendar", tone: "danger" },
  metric: { icon: "chart", tone: "ok" },
  pending_change: { icon: "edit", tone: "violet" },
  note: { icon: "message", tone: "muted" },
};

/** Pending changes get no chip; approval stays on the change card. */
function triagePrompt(item: DigestEntry): { label: string; prompt: string } | null {
  const ref = item.listing ? `${item.listing.title} (${item.listing.listing_id})` : item.ref_id;
  switch (item.kind) {
    case "low_stock":
      return ref ? { label: "问一问", prompt: inventoryPrompt("low_stock", ref) } : null;
    case "slow_mover":
      return ref ? { label: "规划房价", prompt: inventoryPrompt("slow_mover", ref) } : null;
    case "order_issue":
      return {
        label: "起草回复",
        prompt: item.ref_id ? `帮我处理预订 ${item.ref_id}。${item.headline}。` : `帮我处理这个预订问题。${item.headline}。`,
      };
    case "metric":
      return { label: "问原因", prompt: `这背后的原因是什么：${item.headline}？` };
    default:
      return null;
  }
}

function Context({ item }: { item: DigestEntry }) {
  const copy = useCopy();
  if (item.listing) {
    return (
      <span>
        {item.listing.listing_id} · {formatNumber(item.listing.stock)} 间可售 · {formatMoney(item.listing.price, "USD", { whole: true })}
      </span>
    );
  }
  if (item.change) {
    return (
      <span>
        {item.change.change_id} · {copy.changeStatus[item.change.status]}
      </span>
    );
  }
  return null;
}

export default function DigestCard({ payload, onPrefill }: { payload: DigestPayload; onPrefill?: (text: string) => void }) {
  const items = payload.items ?? [];
  return (
    <GenCard>
      <GenCardHeader title={payload.title ?? "需要关注"} aside={`${items.length} 项`} />
      <DigestList>
        {items.map((item, index) => {
          const triage = onPrefill ? triagePrompt(item) : null;
          const style = KINDS[item.kind] ?? KINDS.note;
          return (
            <DigestRow
              key={`${item.ref_id ?? item.headline}-${index}`}
              icon={style.icon}
              tone={style.tone}
              headline={item.headline}
              why={item.why_it_matters}
              context={<Context item={item} />}
              action={triage ? { label: triage.label, onClick: () => onPrefill?.(triage.prompt) } : null}
            />
          );
        })}
      </DigestList>
    </GenCard>
  );
}
