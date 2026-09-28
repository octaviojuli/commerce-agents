// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * The shared chrome's strings in Simplified Chinese: the composer, the transcript, the Activity
 * panel, the shopper's sheet, the orders view, and the merchant portal's frame and change cards.
 * Domain words stay out of it: a vertical spreads `ZH_CHROME_COPY` into its own `Partial<Copy>`
 * and adds its tool lines, gate names, and nouns over it. A vertical that also sets
 * `setDateLocale("zh-CN")` gets its dates in the same language.
 */

import type { Copy } from "./copy";
import { formatDate } from "./format";

export const ZH_CHROME_COPY: Partial<Copy> = {
  toolPresenting: "整理答复中…",
  toolStaging: "准备一份待批准的改动…",
  turnError: "出错了，请再试一次。",

  send: "发送",
  working: "处理中…",
  messageAssistant: (assistant) => `给${assistant}发消息`,

  workingStatus: "处理中",
  latest: "↓ 最新",

  activity: "活动",
  factsSaved: (count) => `本次会话记下了 ${count} 条`,
  closeActivity: "关闭活动面板",
  replyGroup: "回复",
  previousReply: "上一条回复",
  nextReply: "下一条回复",
  replyNumber: (turn) => `第 ${turn} 条回复`,
  ofReplies: (count) => ` / 共 ${count} 条`,
  workingInline: "· 处理中…",
  stepCount: (count) => `· ${count} 步`,
  tokens: (detail) => `token ${detail}`,
  steps: "步骤",
  noReplies: "还没有回复。",
  noToolCalls: "本轮没有工具调用。",
  running: "运行中…",
  held: (gate) => `已拦下 · ${gate}`,
  gates: { provenance: "来源校验", approval: "审批校验", guardrail: "安全规则" },
  gateFallback: "安全校验",
  error: "出错",
  milliseconds: (ms) => (ms < 1 ? "<1 毫秒" : `${Math.round(ms)} 毫秒`),
  elapsedSeconds: (seconds) => `${seconds.toFixed(1)} 秒`,
  ok: "成功",
  input: "入参",
  result: "结果",
  resultExcerpt: "结果（节选）",
  empty: "（空）",

  memoryTitle: (assistant) => `${assistant}记住的事`,
  memoryDefaultTitle: "记忆",
  newFacts: (count) => ` · 本次新增 ${count} 条`,
  nothingSaved: "还没有记下任何事。",
  newFact: "新",

  views: "视图",
  openBag: (label, count) => `打开${label}，${count} 项`,
  accountSheet: (name) => `${name}：资料与记忆`,

  close: "关闭",
  signedInAs: (name) => `当前登录：${name}。`,
  switchTo: (name) => `切换到${name}`,
  factsCount: (count) => `已记 ${count} 条`,
  factsIntro: (assistant) => `${assistant}推荐时会用到这些。任何一条都可以改或忘掉；忘掉即删除。`,
  factsPrivacy: "只保留偏好和长期规则。银行卡、账号、电话和邮箱一律不记。",
  categories: { preference: "偏好", constraint: "规则", context: "关于你" },
  edit: "修改",
  forget: "忘掉",
  correctThis: "改写这一条",
  factRejected: "这一条存不下来。请只写偏好或长期规则。",
  save: "保存",
  cancel: "取消",
  newThisSession: "本次新增",
  // The store keeps a UTC timestamp; the reader sees the day their own clock is on.
  savedOn: (isoDate) => {
    const saved = new Date(isoDate);
    return Number.isNaN(saved.getTime()) ? `记于 ${isoDate}` : `记于 ${saved.getMonth() + 1}月${saved.getDate()}日`;
  },

  closeBag: (title) => `关闭${title}`,
  remove: "移除",
  removeItem: (title) => `移除${title}`,
  decreaseQuantity: (title) => `减少${title}的数量`,
  increaseQuantity: (title) => `增加${title}的数量`,
  viewSummary: "查看摘要",
  checkOut: "去结算",
  showSummaryAgain: "再给我看一次结算摘要。",

  unknownBlock: (component) => `这个页面还没有“${component}”的展示方式。`,
  quotedAsData: (subject) => `${subject}，原文照录。`,

  orderStatuses: {
    processing: "处理中",
    shipped: "已发货",
    out_for_delivery: "派送中",
    delayed: "已延误",
    delivered: "已送达",
    cancelled: "已取消",
    return_initiated: "已申请退货",
    refunded: "已退款",
  },
  all: "全部",
  expected: "预计",
  placedOn: (date) => `下单于 ${date}`,
  moreItems: (first, count) => `${first} 等 ${count + 1} 项`,
  filterList: (title) => `筛选${title}`,
  loadFailed: (title) => `无法加载${title}。`,
  loadFailedAssistant: (title) => `无法加载${title}，但助手仍然可以帮你查。`,
  noneHere: () => "这里没有记录。",
  allOf: (title) => `全部${title}`,

  portalViews: "工作台视图",
  assistant: "助手",
  showAssistant: "显示助手",
  hideAssistant: "隐藏助手",
  approveEveryChange: "每项改动都由你批准",
  fullScreen: "全屏",
  exitFullScreen: "退出全屏",
  resizeAssistant: "调整助手面板宽度",

  greeting: (now) => {
    const hour = now.getHours();
    if (hour < 12) return "早上好";
    if (hour < 18) return "下午好";
    return "晚上好";
  },
  askWhy: "问原因",
  overPeriod: (label) => `${label}的周期走势`,
  awaitingApproval: (count) => `${count} 项改动等待批准`,
  review: "查看",
  moreInQueue: (count) => `队列中还有 ${count} 项`,
  recentChanges: "最近改动",
  describeProposer: (change) => (change.created_by_kind === "agent" ? `由 ${change.created_by} 的助手提议` : `由 ${change.created_by} 暂存`),
  describeResolver: (change) => {
    if (change.status === "applied" && change.applied_by) return `由 ${change.applied_by} 批准`;
    if (change.status === "discarded" && change.discarded_by) {
      return change.discarded_by_kind === "agent" ? `由 ${change.discarded_by} 的助手驳回` : `由 ${change.discarded_by} 驳回`;
    }
    return null;
  },

  changeStatus: { staged: "等待批准", applied: "已批准", discarded: "已驳回" },
  approve: "批准",
  approving: "应用中…",
  dismiss: "驳回",
  dismissing: "驳回中…",
  nothingUntilApproved: "批准之前不会应用任何改动。",
  approvedLine: (by, date) => `已批准${by ? `，由 ${by}` : ""}${date ? `，${formatDate(date)}` : ""}。`,
  dismissedLine: (by, byAssistant) => `已驳回${by ? `，由 ${by}${byAssistant ? "的助手" : ""}` : ""}。未做任何更改。`,
  actionFailed: "这个操作没有生效。请检查 API 后重试。",
  before: "改前",
  after: "改后",
  headroom: (amount) => `余量 ${amount}`,
  marginPoints: (delta) => `利润率 ${delta >= 0 ? "+" : ""}${delta.toFixed(1)} 个百分点`,
  marginBarLabel: (after, before, costLabel, cost) => `新价格 ${after}，原价格 ${before}，${costLabel} ${cost}`,
  priceNow: (value) => `现价 ${value}`,
  priceFloor: (value) => `下限 ${value}`,
  priceCeiling: (value) => `上限 ${value}`,
  priceBandLabel: (current, floor, ceiling) => `现价 ${current}，下限 ${floor}，上限 ${ceiling}`,
};
