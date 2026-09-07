// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/**
 * The workbench's chrome, in the advisor's language; `app/page.tsx` hands it to `StoreShell`, which
 * lays it over the English `DEFAULT_COPY` in `web-shared/copy.ts` and gives the chrome the result.
 * Everything not named here keeps its English. The words are an agency's: a 线路 is a route, a 团期
 * one dated departure of it, 占位 is a seat held in the ERP, and 报价单 is the quote the advisor
 * sends the customer.
 */

import type { Copy } from "web-shared";

export const TOUR_COPY: Partial<Copy> = {
  tools: {
    search_products: "搜索线路中…",
    get_product_details: "读取团期中…",
    search_policies: "查阅政策条款中…",
    get_orders: "查询报名单中…",
    get_order_status: "查询报名单中…",
    get_cart: "核对占位中…",
    add_to_cart: "更新占位中…",
    update_cart_item: "更新占位中…",
    remove_from_cart: "更新占位中…",
    checkout: "整理报价单中…",
    get_preferences: "读取顾问资料中…",
    recall_memories: "回忆你说过的事…",
    save_memory: "记下来，下次直接用…",
    load_skill: "加载中…",
    web_search: "搜索网络中…",
  },
  toolPresenting: "整理答复中…",
  toolStaging: "准备一份待确认的改动…",
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
  newFacts: (count) => ` · 本次新增 ${count} 条`,
  nothingSaved: "还没有记下任何事。",
  newFact: "新",

  views: "视图",
  openBag: (label, count, noun) => `打开${label}，${count}${noun}`,
  accountSheet: (name) => `${name}：资料与记忆`,

  close: "关闭",
  signedInAs: (name) => `当前登录：${name}。`,
  switchTo: (name) => `切换到${name}`,
  factsCount: (count) => `已记 ${count} 条`,
  factsIntro: (assistant) => `${assistant}推荐线路时会用到这些。任何一条都可以改或忘掉；忘掉即删除。`,
  factsPrivacy: "只保留偏好和长期规则。银行卡、账号、电话和邮箱一律不记。",
  categories: { preference: "偏好", constraint: "规则", context: "关于你" },
  edit: "修改",
  forget: "忘掉",
  correctThis: "改写这一条",
  factRejected: "这一条存不下来。请只写偏好或长期规则。",
  save: "保存",
  cancel: "取消",
  newThisSession: "本次新增",
  // The store keeps a UTC timestamp; the advisor reads the day their own clock is on.
  savedOn: (isoDate) => {
    const saved = new Date(isoDate);
    return Number.isNaN(saved.getTime())
      ? `记于 ${isoDate}`
      : `记于 ${saved.getMonth() + 1}/${saved.getDate()}`;
  },

  closeBag: (title) => `关闭${title}`,

  unknownBlock: (component) => `这个页面还没有“${component}”的展示方式。`,
  quotedAsData: (subject) => `${subject}，原文照录。`,
};
