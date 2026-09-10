// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The 行程附件 a route card offers. `GET /api/attachments/{product_id}` answers the file on the
 * advisor's session under the name the agency gave it, so the download is a fetch with the
 * session header and a click on a blob link — a plain anchor could carry neither the header
 * nor, cross-origin, the name.
 */

import type { AgentApi } from "web-shared";

export type AttachmentResult = { ok: true } | { ok: false; detail: string };

const UNREACHABLE = "连不上工作台的接口，附件没有下载。";

/** The name in `Content-Disposition`, the UTF-8 form first; empty when the header has none. */
export function filenameOf(disposition: string | null): string {
  if (!disposition) return "";
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      // A name that does not decode falls through to the plain one.
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1].trim() : "";
}

export async function downloadAttachment(
  api: AgentApi,
  productId: string,
  fallbackName: string,
): Promise<AttachmentResult> {
  let response: Response;
  try {
    response = await fetch(`${api.base}/attachments/${encodeURIComponent(productId)}`, {
      headers: api.headers(),
    });
  } catch {
    return { ok: false, detail: UNREACHABLE };
  }
  if (!response.ok) {
    let detail = `附件下载失败（${response.status}）。`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      // The status is the message.
    }
    return { ok: false, detail };
  }
  const blob = await response.blob();
  const name = filenameOf(response.headers.get("content-disposition")) || fallbackName;
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
  return { ok: true };
}
