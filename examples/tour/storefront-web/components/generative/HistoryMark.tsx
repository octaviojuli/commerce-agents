// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** Where a resumed 历史会话 ends and this sitting begins. */
export default function HistoryMark() {
  return (
    <div className="flex items-center gap-3 py-1 text-[12px] text-(--ink-faint)">
      <span aria-hidden className="h-px flex-1 bg-(--line)" />
      <span>以上为历史记录</span>
      <span aria-hidden className="h-px flex-1 bg-(--line)" />
    </div>
  );
}
