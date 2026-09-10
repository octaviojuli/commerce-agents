// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * Whose workbench this is. The agency's own name belongs to the deployment, not to this code:
 * a build sets `NEXT_PUBLIC_TOUR_BRAND` and the chrome carries it, and a build that sets
 * nothing reads as the workbench rather than as somebody's shop. The 门店 the advisor works in
 * is not here either — it comes from their profile as the greeting's eyebrow.
 */

export const BRAND = process.env.NEXT_PUBLIC_TOUR_BRAND?.trim() || "旅行社";

/** The assistant the advisor talks to; the chrome, the copy and the panel all say this. */
export const ASSISTANT = "选团助手";
