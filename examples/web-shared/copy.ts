// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * Every English string the shared storefront chrome renders — the store shell, the transcript, and
 * the inspector — in one place. `DEFAULT_COPY` is what a vertical gets when it passes nothing, so a
 * storefront that says nothing renders exactly as before; a vertical in another language passes a
 * `Partial<Copy>` to `StoreShell`, and `mergeCopy` lays it over the default. Components read the
 * result through `useCopy`.
 *
 * The merchant portal's chrome (`portal/`) and the orders view (`storefront/orders.tsx`) are not
 * here: they are English only and keep their own labels, the orders view in its `OrderNouns`.
 */

import { createContext, useContext } from "react";

import { formatDayMonth } from "./format";

/** Copy that is composed rather than fixed takes what it composes around as arguments. */
export interface Copy {
  // Tool activity lines: the one line under a reply while a tool runs.
  /** The line for a tool, by tool name. */
  tools: Record<string, string>;
  /** The tools whose line carries the query the model searched for. */
  queryTools: readonly string[];
  /** A `queryTools` line with the query on it. */
  toolQuery: (line: string, query: string) => string;
  /** Any `present_*` tool. */
  toolPresenting: string;
  /** Any `stage_*` tool. */
  toolStaging: string;
  /** A tool with no line of its own. */
  toolFallback: (tool: string) => string;
  /** The reply's error text when the server sends an error with no message. */
  turnError: string;

  // Composer.
  /** The send button. */
  send: string;
  /** The field's placeholder while a reply streams. */
  working: string;
  /** The field's own label. */
  messageAssistant: (assistant: string) => string;

  // Transcript.
  /** The shimmer that stands in for a reply's first words. */
  workingStatus: string;
  /** The pill that jumps back to the newest reply. */
  latest: string;

  // The Activity button and the Activity panel.
  activity: string;
  factsSaved: (count: number) => string;
  closeActivity: string;
  replyGroup: string;
  previousReply: string;
  nextReply: string;
  replyNumber: (turn: number) => string;
  /** Follows `replyNumber`; keeps its own leading space. */
  ofReplies: (count: number) => string;
  /** Follows the reply's number while it streams. */
  workingInline: string;
  stepCount: (count: number) => string;
  tokens: (detail: string) => string;
  steps: string;
  noReplies: string;
  noToolCalls: string;
  /** A row's trailing text while its tool runs. */
  running: string;
  /** A row a gate held. */
  held: (gate: string) => string;
  /** How a gate names itself in `held`. */
  gates: Record<string, string>;
  gateFallback: string;
  /** A row whose tool failed. */
  error: string;
  milliseconds: (ms: number) => string;
  /** A reply's own elapsed time, in seconds. */
  elapsedSeconds: (seconds: number) => string;
  /** Read out for a row that succeeded. */
  ok: string;
  input: string;
  result: string;
  resultExcerpt: string;
  /** Stands in for a call or result with no text. */
  empty: string;

  // What the assistant has saved, in the Activity panel and the shopper's sheet.
  /** The heading over the facts; the same words in the Activity panel and the sheet. */
  memoryTitle: (assistant: string) => string;
  /** The heading when no assistant is named, as on the merchant portal. */
  memoryDefaultTitle: string;
  newFacts: (count: number) => string;
  nothingSaved: string;
  /** Marks a fact saved this session, in the panel's list. */
  newFact: string;

  // The store's app bar.
  views: string;
  openBag: (label: string, count: number, noun: string) => string;
  accountSheet: (name: string) => string;

  // The shopper's sheet.
  close: string;
  signedInAs: (name: string) => string;
  switchTo: (name: string) => string;
  factsCount: (count: number) => string;
  factsIntro: (assistant: string) => string;
  factsPrivacy: string;
  /** How a fact's category reads to the shopper. */
  categories: Record<string, string>;
  edit: string;
  forget: string;
  correctThis: string;
  /** Shown when an edited fact is refused. */
  factRejected: string;
  save: string;
  cancel: string;
  newThisSession: string;
  /** Takes the fact's ISO date, so a vertical writes it its own way. */
  savedOn: (isoDate: string) => string;

  // The bag panel beside the conversation.
  closeBag: (title: string) => string;
  /** A line's quantity when the unit is named: "2 nights", "1 guest". */
  unitCount: (unit: string, count: number) => string;
  remove: string;
  removeItem: (title: string) => string;
  fewerUnits: (unit: string, title: string) => string;
  moreUnits: (unit: string, title: string) => string;
  decreaseQuantity: (title: string) => string;
  increaseQuantity: (title: string) => string;
  viewSummary: string;
  checkOut: string;
  /** Asked when the staged summary has scrolled out of the transcript. */
  showSummaryAgain: string;

  // Streamed cards.
  unknownBlock: (component: string) => string;
  /** The label under quoted third-party text. */
  quotedAsData: (subject: string) => string;
}

export const DEFAULT_COPY: Copy = {
  tools: {
    search_products: "Searching the catalog…",
    get_product_details: "Reading the details…",
    search_policies: "Checking the policies…",
    get_orders: "Looking up your orders…",
    get_order_status: "Looking up your orders…",
    get_fulfillment_options: "Checking delivery and pickup options…",
    get_cart: "Checking the cart…",
    add_to_cart: "Updating the cart…",
    update_cart_item: "Updating the cart…",
    remove_from_cart: "Updating the cart…",
    checkout: "Preparing your order summary…",
    get_preferences: "Reading your profile…",
    recall_memories: "Recalling what you've told me…",
    save_memory: "Saving that for next time…",
    load_skill: "Loading…",
    web_search: "Searching the web…",
    get_business_snapshot: "Reading the business snapshot…",
    query_metrics: "Querying metrics…",
    search_listings: "Searching the listings…",
    get_listing: "Reading the listing…",
    get_inventory_alerts: "Checking inventory alerts…",
    get_order_issues: "Checking order issues…",
    get_pricing_context: "Reading the pricing context…",
    get_campaign_performance: "Reading campaign performance…",
    get_pending_changes: "Checking pending changes…",
    run_analysis: "Running the analysis…",
    apply_change: "Applying the change…",
    discard_change: "Discarding the change…",
  },
  queryTools: ["search_products", "search_policies", "search_listings", "web_search"],
  toolQuery: (line, query) => `${line.replace(/…$/, "")} · “${query}”`,
  toolPresenting: "Composing the answer…",
  toolStaging: "Staging a change for review…",
  toolFallback: (tool) => `${tool.replaceAll("_", " ")}…`,
  turnError: "Something went wrong.",

  send: "Send",
  working: "Working…",
  messageAssistant: (assistant) => `Message ${assistant}`,

  workingStatus: "Working",
  latest: "↓ Latest",

  activity: "Activity",
  factsSaved: (count) => `${count} fact${count === 1 ? "" : "s"} saved this session`,
  closeActivity: "Close activity",
  replyGroup: "Reply",
  previousReply: "Previous reply",
  nextReply: "Next reply",
  replyNumber: (turn) => `Reply ${turn}`,
  ofReplies: (count) => ` of ${count}`,
  workingInline: "· working…",
  stepCount: (count) => `· ${count} step${count === 1 ? "" : "s"}`,
  tokens: (detail) => `tokens ${detail}`,
  steps: "Steps",
  noReplies: "No replies yet.",
  noToolCalls: "No tool calls this reply.",
  running: "running…",
  held: (gate) => `held · ${gate}`,
  gates: { provenance: "provenance gate", approval: "approval gate", guardrail: "guardrail" },
  gateFallback: "safety gate",
  error: "error",
  // The in-process mock backends answer in under a millisecond.
  milliseconds: (ms) => (ms < 1 ? "<1 ms" : `${Math.round(ms)} ms`),
  elapsedSeconds: (seconds) => `${seconds.toFixed(1)}s`,
  ok: "ok",
  input: "Input",
  result: "Result",
  resultExcerpt: "Result (excerpt)",
  empty: "(empty)",

  memoryTitle: (assistant) => `What ${assistant} knows`,
  memoryDefaultTitle: "Memory",
  newFacts: (count) => ` · ${count} new this session`,
  nothingSaved: "Nothing saved yet.",
  newFact: "new",

  views: "Views",
  openBag: (label, count, noun) => `Open ${label.toLowerCase()}, ${count} ${noun}${count === 1 ? "" : "s"}`,
  accountSheet: (name) => `${name}: profile and memory`,

  close: "Close",
  signedInAs: (name) => `Signed in as ${name}.`,
  switchTo: (name) => `Switch to ${name}`,
  factsCount: (count) => `${count} saved`,
  factsIntro: (assistant) =>
    `${assistant} uses these when it recommends something. Edit or forget any line; a forgotten line is deleted.`,
  factsPrivacy: "Only preferences and standing rules are kept. Card, account, phone, and email details are refused.",
  categories: { preference: "Preference", constraint: "Rule", context: "About you" },
  edit: "Edit",
  forget: "Forget",
  correctThis: "Correct this",
  factRejected: "That could not be saved. Keep it to a preference or a standing rule.",
  save: "Save",
  cancel: "Cancel",
  newThisSession: "New this session",
  savedOn: (isoDate) => `Saved ${formatDayMonth(isoDate)}`,

  closeBag: (title) => `Close ${title.toLowerCase()}`,
  unitCount: (unit, count) => `${count} ${unit}${count === 1 ? "" : "s"}`,
  remove: "Remove",
  removeItem: (title) => `Remove ${title}`,
  fewerUnits: (unit, title) => `Fewer ${unit}s for ${title}`,
  moreUnits: (unit, title) => `More ${unit}s for ${title}`,
  decreaseQuantity: (title) => `Decrease ${title} quantity`,
  increaseQuantity: (title) => `Increase ${title} quantity`,
  viewSummary: "View summary",
  checkOut: "Check out",
  showSummaryAgain: "Show me the checkout summary again.",

  unknownBlock: (component) => `This page has no view for “${component}” yet.`,
  quotedAsData: (subject) => `${subject}, shown as written.`,
};

/** A vertical's copy over the default; `tools`, `gates`, and `categories` merge key by key. */
export function mergeCopy(copy?: Partial<Copy>): Copy {
  if (!copy) return DEFAULT_COPY;
  return {
    ...DEFAULT_COPY,
    ...copy,
    tools: { ...DEFAULT_COPY.tools, ...copy.tools },
    gates: { ...DEFAULT_COPY.gates, ...copy.gates },
    categories: { ...DEFAULT_COPY.categories, ...copy.categories },
  };
}

/** The activity line for a tool call: the tool's own line, with the query on it when it has one. */
export function describeToolCall(
  tool: string,
  input: Record<string, unknown> = {},
  copy: Copy = DEFAULT_COPY,
): string {
  const line = copy.tools[tool];
  const query = typeof input.query === "string" ? input.query.trim() : "";
  if (line && query && copy.queryTools.includes(tool)) return copy.toolQuery(line, query);
  if (line) return line;
  if (tool.startsWith("present_")) return copy.toolPresenting;
  if (tool.startsWith("stage_")) return copy.toolStaging;
  return copy.toolFallback(tool);
}

/**
 * What every chrome component reads its strings from. The default is `DEFAULT_COPY`, so a
 * component rendered outside a `StoreShell` (as `/showcase` does) still has its English.
 */
const CopyContext = createContext<Copy>(DEFAULT_COPY);

export const CopyProvider = CopyContext.Provider;

export function useCopy(): Copy {
  return useContext(CopyContext);
}
