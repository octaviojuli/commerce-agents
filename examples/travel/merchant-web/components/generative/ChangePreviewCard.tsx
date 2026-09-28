// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import {
  ApproveBar,
  type ChangeAction,
  ChangeStatusPill,
  DiffRows,
  formatDate,
  formatMoney,
  GenCard,
  GenCardHeader,
  GuardrailNotes,
  isLongTextDiff,
  LongTextDiff,
  MarginHeadroom,
  titleCase,
  useChangeActions,
  useCopy,
} from "web-shared";
import { CHANGE_KIND_LABELS } from "@/lib/copy";
import type { ChangePreviewPayload, StagedChange } from "@/lib/types";

/** Rate moves carry a margin headroom bar under the diff. */
export default function ChangePreviewCard({
  payload,
  onAct,
}: {
  payload: ChangePreviewPayload;
  onAct?: (changeId: string, action: ChangeAction) => Promise<StagedChange | null>;
}) {
  const copy = useCopy();
  const { change, busy, error, act, canAct } = useChangeActions(payload.change, onAct);
  const shortItems = change.items.filter((item) => !isLongTextDiff(item));
  const longItems = change.items.filter(isLongTextDiff);

  return (
    <GenCard>
      <GenCardHeader
        title={payload.headline ?? "拟议改动"}
        meta={
          <>
            <ChangeStatusPill status={change.status} />
            <span>{CHANGE_KIND_LABELS[change.kind] ?? titleCase(change.kind)}</span>
            <span aria-hidden>·</span>
            <span>{copy.describeProposer(change)}</span>
            <span aria-hidden>·</span>
            <span>{formatDate(change.created_at)}</span>
          </>
        }
      />
      <p className="px-3.5 pt-2 text-[14px] leading-snug text-(--ink)">{change.summary}</p>
      {payload.note ? <p className="px-3.5 pt-1 text-[12.5px] leading-snug text-(--ink-soft)">{payload.note}</p> : null}

      <DiffRows items={shortItems} fields={{ currency: ["nightly_rate"] }} />
      {longItems.map((item, index) => (
        <LongTextDiff key={`${item.target}-${item.field}-${index}`} item={item} />
      ))}
      <MarginHeadroom change={change} costLabel="每晚成本" />

      {change.margin_impact != null ? (
        <p className="mx-3.5 mt-2 text-[12.5px] tabular-nums text-(--ink-soft)">
          利润影响{" "}
          <b className={`font-semibold ${change.margin_impact < 0 ? "text-(--danger)" : "text-(--ok)"}`}>
            {change.margin_impact > 0 ? "+" : ""}
            {formatMoney(change.margin_impact, change.currency ?? undefined)}
          </b>
        </p>
      ) : null}

      <GuardrailNotes notes={change.guardrail_notes} />
      <ApproveBar change={change} busy={busy} error={error} canAct={canAct} onAct={(action) => void act(action)} />
    </GenCard>
  );
}
