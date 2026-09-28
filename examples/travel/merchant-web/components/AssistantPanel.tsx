// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { AssistantPanel as PanelShell, type MerchantChat, type Prefill } from "web-shared";
import type { StagedChange } from "@/lib/types";
import GenerativeBlock from "./generative";

const COPY = {
  title: "供应商助手",
  intro: "问问预订、入住率、房价或营销活动。",
  starters: [
    "今天早上有什么需要我关注的？",
    "里斯本的住宿 10 月的预订进度如何？",
    "哪些房源的预订进度落后了？",
    "未来一个月的房价该往哪个方向调？",
  ],
  label: "给供应商助手发消息",
  placeholder: "问问预订、房价、营销活动…",
};

export default function AssistantPanel({
  chat,
  prefill,
  onPrefill,
  ...shell
}: {
  chat: MerchantChat<StagedChange>;
  prefill: Prefill | null;
  onPrefill: (text: string) => void;
  newMemoryCount: number;
  onOpenActivity: () => void;
  onClose: () => void;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
}) {
  return (
    <PanelShell
      chat={chat}
      copy={COPY}
      prefill={prefill}
      renderBlock={(segment) => (
        <GenerativeBlock
          block={segment.block}
          status={segment.status}
          onChangeAction={chat.actOnChange}
          onPrefill={onPrefill}
        />
      )}
      {...shell}
    />
  );
}
