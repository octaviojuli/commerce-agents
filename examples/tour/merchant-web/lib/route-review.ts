// Turns the server's open review items on a parsed route into questions with clickable answers.
// Every answer is a pure `set` on the content, so choosing again moves from one answer to another.

export type Row = Record<string, any>;
export type Unit = { id: number; text: string; page?: number | null };
export type Listing = Record<"days" | "gateway", { upstream: number | string | null; effective: number | string | null; origin: string; state: string }>;
export type Context = { daysConflict?: boolean; listing?: Listing; departureDays?: { days: number; count: number }[] };
export type Witness = { source: string; value: string };
export type Decision = { field: "days" | "gateway"; value: number | string };
export type Option = {
  id: string;
  label: string;
  hint?: string;
  input?: { label: string; value: string };
  keeps?: boolean;
  decide?: Decision;
  jump?: boolean;
  apply: (content: Row, input?: string) => void;
};
export type Card = {
  key: string;
  serverKey: string;
  code: string;
  title: string;
  question: string;
  where: string;
  day?: number;
  evidence: { lines: Unit[]; needle: string; wide: Unit[] };
  options: Option[];
  editOnly?: boolean;
  ack: boolean;
  facts?: Witness[];
  waiting?: string;
};

// Rebuild server questions even when the issue key and severity stay the same:
// resolving a day decision can turn a waiting card into an actionable scope edit.
export function reconcileCards<T extends { option: string }>(previous: Card[], fresh: Card[], choices: Record<string, T>) {
  const retained = previous.filter(c => !fresh.some(n => n.serverKey === c.serverKey)
    && c.options.some(o => o.id === choices[c.key]?.option && o.decide));
  const cards = [...retained, ...fresh];
  const valid = Object.fromEntries(Object.entries(choices).filter(([key, choice]) => {
    const before = previous.find(c => c.key === key), after = cards.find(c => c.key === key);
    return before && after && before.ack === after.ack
      && !after.waiting && !after.editOnly && after.options.some(o => o.id === choice.option);
  }));
  return { cards, choices: valid };
}

type Base = Pick<Card, "key" | "serverKey" | "code" | "where" | "day">;
const attrNames: Record<string, string> = { visit_mode: "游览方式", ticket: "门票", inclusion: "费用归属", highlight: "特别安排" };
const valueNames: Record<string, string> = {
  inside: "入内参观", outside: "外观", passing: "途经", drive_by: "乘车游览", distant_view: "远眺", walk: "步行",
  included: "已含", excluded: "不含", free_entry: "免费入场", gift: "赠送", optional_paid: "另付费", package_item: "套餐内", recommended_not_included: "推荐（不含）",
};
const typeNames: Record<string, string> = { poi: "景点", activity: "活动", optional: "自费项目", group: "观光段", free: "自由活动", shopping: "购物", package: "套餐", recommend: "推荐", photo_spot: "拍照点", notice: "提醒", transport: "交通", meet: "集合" };
const numeric = new Set(["min_participants", "arrival_day_offset", "consecutive_nights", "nights", "days_count"]);
const mealNames: Record<string, string> = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" };
const noticeTitles: Record<string, string> = { visa_documents: "签证证件", health_age: "健康与年龄", booking_cancellation: "报名与退改", flight_luggage: "航班与行李", safety: "安全", local_customs: "当地习俗", money_tips: "费用提示", shopping_optional: "购物与自费", other: "其他" };
const fieldNames: Record<string, string> = { nights: "住宿晚数", days_count: "行程天数", title: "标题", subtitle: "副标题", depart_city: "出发地", countries: "到访国家", min_participants: "最低成团人数", duration_text: "参考时长", price_text: "附件费用", clock_text: "时间", booking_note: "预约说明", description: "说明", summary: "当天简述", travel_text: "里程与车程", arrival_day_offset: "到达跨日数", departure_dates_text: "出发日期" };

const norm = (s: string) => (s || "").replace(/[\s（）()【】\[\]、,，:：;；.。]/g, "");
export const same = (a: string, b: string) => !!a && !!b && norm(a) === norm(b);

function dayFor(content: Row, n: number): Row | undefined {
  return (content.days || []).find((d: Row) => d.day === n || (d.day_end && d.day <= n && n <= d.day_end));
}
function nodesOf(day: Row): Row[] {
  return (day.items || []).flatMap((item: Row) => [item, ...(item.children || [])]);
}
function locate(day: Row | undefined, subject: string | undefined, id?: string): Row | undefined {
  if (!day) return undefined;
  const all = nodesOf(day);
  return (id && all.find((n) => n.node_id === id)) || (subject ? all.find((n) => same(n.name, subject)) : undefined);
}
function follow(root: Row, path: string): { parent: Row; key: string } | undefined {
  const parts = path.split(".").filter(Boolean);
  let parent: any = root;
  for (const part of parts.slice(0, -1)) {
    parent = parent?.[part];
    if (parent == null || typeof parent !== "object") return undefined;
  }
  const key = parts.at(-1);
  return key && parent && typeof parent === "object" && !/\[/.test(key) ? { parent, key } : undefined;
}
const empty = (v: unknown) => v == null || v === "" || (Array.isArray(v) && !v.length);
const label = (v: string) => valueNames[v] ?? v;
const blankNode = (id: string, name: string): Row => ({ node_id: id, type: "poi", name, local_name: "", description: "", visit_mode: "unknown", ticket: "unknown", inclusion: "unknown", duration_text: "", includes: [], children: [], cite: [] });

function evidenceFor(content: Row, units: Unit[], day: Row | undefined, node: Row | undefined, cite: number[] | undefined, needle: string) {
  const byId = new Map(units.map((u) => [u.id, u]));
  const own = (node?.cite ?? cite ?? []).map((i: number) => byId.get(i)).filter(Boolean) as Unit[];
  const wide = ((day?.units ?? []) as number[]).map((i) => byId.get(i)).filter(Boolean) as Unit[];
  const key = norm(needle);
  const mentioned = key.length >= 2 ? wide.filter((u) => norm(u.text).includes(key.slice(0, 12))) : [];
  return { lines: own.length ? own : mentioned, needle, wide };
}

type Entry = { code: string; path: string; detail: string; subject?: string };

export function buildCards(issues: Row[], content: Row, units: Unit[], ctx: Context = {}): Card[] {
  const entries: Entry[] = (content.quality?.issues || []).map((e: Row) => ({ code: e.code, path: String(e.path ?? "source"), detail: String(e.detail ?? ""), subject: e.subject }));
  const cards: Card[] = [];
  for (const issue of issues) {
    if (issue.code === "CONTENT_FACT_REVIEW") continue;
    const serverKey = `${issue.code}|${issue.path}`;
    const found = entries.filter((e) => e.code === issue.code && e.path === issue.path);
    const made = (found.length ? found : [{ code: issue.code, path: issue.path, detail: "" } as Entry]).flatMap((entry, n) => {
      const card = one(issue, entry, content, units, `${serverKey}|${n}`, serverKey, {...ctx,daysConflict:issues.some(i=>i.code==="DAYS_DIFFER_FROM_LISTING")});
      return card ? [{ ...card, ack: !!issue.acknowledgeable }] : [];
    });
    cards.push(...made);
  }
  // Conflicts with the listing come first, then the card that waits on them.
  const rank = (c: Card) => (c.waiting ? 1 : c.facts ? 0 : 2);
  return cards.map((c, i) => ({ c, i })).sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i).map((x) => x.c);
}

function one(issue: Row, entry: Entry, content: Row, units: Unit[], key: string, serverKey: string, ctx: Context): Omit<Card, "ack"> | undefined {
  const { code, path, detail, subject } = entry;
  const m = /^D(\d+)\.(.*)$/.exec(path);
  const day = m ? dayFor(content, Number(m[1])) : undefined;
  const rest = m ? m[2] : path;
  const where = m ? `第 ${m[1]} 天` : "全线路";
  const base = { key, serverKey, code, where, day: m ? Number(m[1]) : undefined };
  const keep = (text = "保持现状"): Option => ({ id: "keep", label: text, keeps: true, apply: () => {} });
  const edit = (question: string, title: string): Omit<Card, "ack"> => ({ ...base, title, question, evidence: evidenceFor(content, units, day, undefined, undefined, detail), options: [], editOnly: true });
  const facts = conflictCard(base, code, issue, content, ctx);
  if (facts) return facts;
  if (!issue.acknowledgeable) return edit(issue.message, "需要先修改这一处");

  const node = locate(day, subject);
  const nid: string | undefined = node?.node_id;
  const nodeName = subject || detail;
  const ev = (n?: Row, cite?: number[], needle = nodeName) => evidenceFor(content, units, day, n, cite, needle);

  if (code === "ATTRIBUTE_UNSUPPORTED" && rest === "shopping_status") {
    return { ...base, title: "有没有购物安排", question: "原文没有明确写“无购物”。请确认这条线路的购物情况。", evidence: ev(undefined, undefined, "购物"), options: [
      { id: "unknown", label: "原文没写，标为“未说明”", keeps: true, apply: (c) => { c.shopping_status = "unknown"; } },
      { id: "none", label: "原文明确写了无购物", apply: (c) => { c.shopping_status = "none"; } },
    ] };
  }
  if (code === "NO_SHOPPING_CONFIRM") {
    return { ...base, title: "确认“无购物”", question: "线路被标为“无购物”，请确认原文有明确约定。", evidence: ev(undefined, undefined, "购物"), options: [
      { id: "confirm", label: "原文明确写了无购物", keeps: true, apply: (c) => { c.shopping_status = "none"; } },
      { id: "unknown", label: "原文没写，改为“未说明”", apply: (c) => { c.shopping_status = "unknown"; } },
    ] };
  }
  const attr = /(?:^|\.)(visit_mode|ticket|inclusion|highlight)$/.exec(rest)?.[1];
  if (code === "ATTRIBUTE_UNSUPPORTED" && attr) {
    const target = node;
    const shown = attr === "highlight" ? "特别安排" : label(detail);
    const title = `《${nodeName}》· ${attrNames[attr]}`;
    if (!target) return { ...base, title, question: `原文没有明确写“${shown}”，系统按“未说明”处理。未能自动定位到这一项，请确认保持现状。`, evidence: ev(), options: [keep("保持“未说明”")] };
    const off = attr === "highlight" ? false : "unknown";
    const on = attr === "highlight" ? true : detail;
    const set = (v: unknown) => (c: Row) => { const t = locate(dayFor(c, base.day ?? 0), subject, nid); if (t) t[attr] = v; };
    return { ...base, title, question: `原文没有明确写“${shown}”，系统先按“未说明”处理。`, evidence: ev(target), options: [
      { id: "off", label: attr === "highlight" ? "不是特别安排" : "原文没写，保持“未说明”", keeps: true, apply: set(off) },
      { id: "on", label: attr === "highlight" ? "是特别安排" : `原文写了：${shown}`, apply: set(on) },
    ] };
  }
  if (code === "AUTO_ATTACHED") return attached(base, path, rest, day, detail, content, units);
  if (["IMAGE_TEXT_TRANSCRIBED", "PICTURES_NOT_READ", "PICTURE_NOT_READ"].includes(code)) {
    const count = /(\d+)\s*张/.exec(detail)?.[1];
    const q = code === "IMAGE_TEXT_TRANSCRIBED" ? `原文里有 ${count ?? "若干"} 张图片，图中的文字由系统转写，可能有误。请打开原文件对照图片。` : `原文里有${count ? ` ${count} 张` : ""}图片的文字没有读取。请打开原文件看一眼，确认没有遗漏酒店、航空公司或限制条件。`;
    return { ...base, title: "图片内容", question: q, evidence: { lines: [], needle: "", wide: [] }, options: [keep(code === "IMAGE_TEXT_TRANSCRIBED" ? "已对照原图，转写无误" : "已看过，没有需要补充的")] };
  }
  if (code === "DAYS_DIFFER_FROM_LISTING") {
    return { ...base, title: "行程天数", question: `原文件是 ${content.days_count} 天，线路登记是 ${content.quality?.days_expected} 天。`, evidence: { lines: [], needle: "", wide: [] }, options: [keep(`以原文件为准（${content.days_count} 天）`)] };
  }
  if (["TYPE_CHECK", "TYPE_FROM_SECTION", "TYPE_UNSUPPORTED"].includes(code) && node) {
    const set = (type: string, inclusion?: string) => (c: Row) => { const t = locate(dayFor(c, base.day ?? 0), subject, nid); if (t) { t.type = type; if (inclusion) t.inclusion = inclusion; } };
    const original = node.type;
    if (code === "TYPE_CHECK") return { ...base, title: `《${nodeName}》是自费项目吗`, question: "名称附近出现“自费”或“另付”字样。", evidence: ev(node), options: [
      { id: "no", label: "不是，团费已含", keeps: true, apply: set(original === "optional" ? "poi" : original, node.inclusion) },
      { id: "yes", label: "是自费项目", apply: set("optional", "optional_paid") },
    ] };
    if (code === "TYPE_FROM_SECTION") return { ...base, title: `《${nodeName}》是自费项目吗`, question: "它排在原文“自费”标题下，系统已归为自费项目。", evidence: ev(node), options: [
      { id: "yes", label: "是自费项目", keeps: true, apply: set("optional", "optional_paid") },
      { id: "no", label: "不是，改回普通景点", apply: set("poi", "unknown") },
    ] };
    const wanted = detail.split(" ")[0];
    return { ...base, title: `《${nodeName}》的类型`, question: `原文没有支持“${typeNames[wanted] ?? wanted}”的说法，已改为“${typeNames[node.type] ?? node.type}”。`, evidence: ev(node), options: [
      { id: "fallback", label: `保持“${typeNames[node.type] ?? node.type}”`, keeps: true, apply: set(node.type) },
      ...(typeNames[wanted] ? [{ id: "wanted", label: `原文是“${typeNames[wanted]}”`, apply: set(wanted) }] : []),
    ] };
  }
  if (code === "NAME_APPROXIMATE" && node) {
    return { ...base, title: `《${nodeName}》名称核对`, question: "名称与原文写法略有差异，请对照下面的原文。", evidence: ev(node), options: [
      { id: "ok", label: "名称正确", keeps: true, apply: () => {} },
      { id: "rename", label: "改成这样写", input: { label: "名称", value: node.name }, apply: (c, input) => { const t = locate(dayFor(c, base.day ?? 0), subject, nid); if (t && input?.trim()) t.name = input.trim(); } },
    ] };
  }
  if (code === "IMAGE_PAGES_NOT_READ") return { ...base, title: "图片页", question: `${detail || "有页面是图片，文字没有读取"}。请打开原文件看一眼，确认没有遗漏酒店、航空公司或限制条件。`, evidence: { lines: [], needle: "", wide: [] }, options: [keep("已看过，没有需要补充的")] };
  if (/^day\.(cities|countries)$/.test(rest) && day) return { ...base, title: "当天到访地点", question: "有的地点在当天原文里找不到依据，系统已经去掉。请对照当天原文，看当天到过的城市和国家是否都还在。", evidence: evidenceFor(content, units, day, undefined, undefined, ""), options: [keep("都还在，没有漏")] };
  const dropped = droppedCard(base, code, rest, day, node, subject, detail, key, content, units);
  if (dropped) return dropped;
  const scalar = scalarCard(base, code, rest, day, node, subject, detail, content, units);
  if (scalar) return scalar;
  const known = issue.message.startsWith("解析问题需核对原文及修订结果") ? "" : issue.message;
  return { ...base, title: subject || "需要确认的一处", question: `系统对这一处不确定${detail && detail !== subject ? `：${detail}` : ""}。${known}请对照原文。`, evidence: ev(node), options: [keep("已对照原文，保持现状")] };
}

function attached(base: Base, path: string, rest: string, day: Row | undefined, detail: string, content: Row, units: Unit[]): Omit<Card, "ack"> {
  const cat = /^notices\.(.+)$/.exec(rest)?.[1];
  const list = (c: Row): { items: Row[]; group?: Row } => {
    if (cat) { const group = (c.notices || []).find((g: Row) => g.category === cat); return { items: group?.items ?? [], group }; }
    const d = dayFor(c, base.day ?? 0); return { items: d?.notes ?? [] };
  };
  const at = list(content).items.findIndex((i) => i.auto && i.text.startsWith(detail.slice(0, 100)));
  const first = list(content).items[at];
  const text: string = first?.text ?? detail;
  const item = first ? structuredClone(first) : { text: detail, cite: [], auto: true };
  const title = cat ? `须知 · ${noticeTitles[cat] ?? cat}` : "当天补充说明";
  const has = (c: Row) => list(c).items.some((i) => i.text === item.text);
  const remove = (c: Row) => {
    if (cat) { const g = (c.notices || []).find((x: Row) => x.category === cat); if (!g) return; g.items = g.items.filter((i: Row) => i.text !== item.text); c.notices = c.notices.filter((x: Row) => x.items.length); return; }
    const d = dayFor(c, base.day ?? 0); if (d) d.notes = d.notes.filter((i: Row) => i.text !== item.text);
  };
  const restore = (c: Row) => {
    if (has(c)) return;
    if (cat) { c.notices ??= []; let g = c.notices.find((x: Row) => x.category === cat); if (!g) { g = { category: cat, title: "", items: [] }; c.notices.push(g); } g.items.splice(Math.max(at, 0), 0, structuredClone(item)); return; }
    const d = dayFor(c, base.day ?? 0); if (d) (d.notes ??= []).splice(Math.max(at, 0), 0, structuredClone(item));
  };
  return { ...base, title, question: "这段原文没有被归入任何栏目，系统原样放进了行程。请确认要不要给客户看。", evidence: { lines: (item.cite ?? []).map((i: number) => units.find((u) => u.id === i)).filter(Boolean) as Unit[], needle: text.slice(0, 20), wide: [] }, options: [
    { id: "keep", label: "保留这段", keeps: true, apply: restore },
    { id: "remove", label: "删除这段", apply: remove },
  ] };
}

function droppedCard(base: Base, code: string, rest: string, day: Row | undefined, node: Row | undefined, subject: string | undefined, detail: string, key: string, content: Row, units: Unit[]): Omit<Card, "ack"> | undefined {
  if (!detail || !["NAME_NOT_IN_SOURCE", "NO_EVIDENCE", "TEXT_NOT_IN_SOURCE", "NUMBER_NOT_IN_SOURCE"].includes(code)) return undefined;
  const ev = evidenceFor(content, units, day, undefined, undefined, detail);
  const day0 = () => base.day ?? 0;
  const nid: string | undefined = node?.node_id;
  const id = `restored:${key}`;
  const nodePath = /^items\[\d+\](?:\.children\[\d+\])?(?:\.name)?$/.test(rest);
  if (nodePath && day) {
    const name = detail.split("（")[0];
    return { ...base, title: `原文里找不到“${name}”`, question: "系统没有把它放进行程，因为在原文里找不到对应的文字。", evidence: ev, options: [
      { id: "drop", label: "确认不要", keeps: true, apply: (c) => { const d = dayFor(c, day0()); if (d) d.items = d.items.filter((i: Row) => i.node_id !== id); } },
      { id: "add", label: "原文有，加到当天行程末尾", input: { label: "名称", value: name }, apply: (c, input) => { const d = dayFor(c, day0()); if (!d) return; const at = d.items.find((i: Row) => i.node_id === id); if (at) at.name = input?.trim() || name; else d.items.push(blankNode(id, input?.trim() || name)); } },
    ] };
  }
  if (/^notes\[\d+\]$/.test(rest) && day) {
    return { ...base, title: "当天提示没有被采用", question: `系统没有采用这句话，因为原文里找不到：“${detail}”`, evidence: ev, options: [
      { id: "drop", label: "确认不要", keeps: true, apply: (c) => { const d = dayFor(c, day0()); if (d) d.notes = d.notes.filter((n: Row) => n.text !== detail); } },
      { id: "add", label: "原文有，加回当天提示", apply: (c) => { const d = dayFor(c, day0()); if (d && !d.notes.some((n: Row) => n.text === detail)) d.notes.push({ text: detail, cite: [], auto: false }); } },
    ] };
  }
  if (rest === "stay.names" && day) {
    const names = detail.split("、").filter(Boolean);
    return { ...base, title: "酒店名称没有被采用", question: `原文里找不到这些酒店名称：${names.join("、")}`, evidence: evidenceFor(content, units, day, undefined, day.stay?.cite, names[0]), options: [
      { id: "drop", label: "确认不要", keeps: true, apply: (c) => { const d = dayFor(c, day0()); if (d?.stay) d.stay.names = d.stay.names.filter((n: string) => !names.includes(n)); } },
      { id: "add", label: "原文有，加回", apply: (c) => { const d = dayFor(c, day0()); if (d?.stay) d.stay.names = [...d.stay.names, ...names.filter((n) => !d.stay.names.includes(n))]; } },
    ] };
  }
  const meal = /^meals\.(breakfast|lunch|dinner)$/.exec(rest)?.[1];
  if (meal && code === "NO_EVIDENCE" && day) {
    const set = (status: string, text: string) => (c: Row) => { const d = dayFor(c, day0()); if (d?.meals) d.meals[meal] = { ...d.meals[meal], status, text: status === "unknown" ? "" : text }; };
    return { ...base, title: `${mealNames[meal]}安排`, question: `系统没找到依据，${mealNames[meal]}已改为“未说明”。原来识别的是：“${detail}”`, evidence: ev, options: [
      { id: "unknown", label: "原文没写，保持“未说明”", keeps: true, apply: set("unknown", "") },
      { id: "included", label: "原文写了：含餐", input: { label: "说明", value: detail }, apply: (c, input) => set("included", input?.trim() || detail)(c) },
      { id: "self", label: "原文写了：自理", input: { label: "说明", value: detail }, apply: (c, input) => set("self", input?.trim() || detail)(c) },
    ] };
  }
  const includes = /^items\[\d+\]\.includes$/.test(rest);
  if (includes && node) {
    const parts = detail.split("、").filter(Boolean);
    return { ...base, title: `《${node.name}》的包含内容`, question: `原文里找不到这些包含内容：${parts.join("、")}`, evidence: evidenceFor(content, units, day, node, undefined, parts[0]), options: [
      { id: "drop", label: "确认不要", keeps: true, apply: (c) => { const t = locate(dayFor(c, day0()), subject, nid); if (t) t.includes = t.includes.filter((x: string) => !parts.includes(x)); } },
      { id: "add", label: "原文有，加回", apply: (c) => { const t = locate(dayFor(c, day0()), subject, nid); if (t) t.includes = [...t.includes, ...parts.filter((x) => !t.includes.includes(x))]; } },
    ] };
  }
  return undefined;
}

function scalarCard(base: Base, code: string, rest: string, day: Row | undefined, node: Row | undefined, subject: string | undefined, detail: string, content: Row, units: Unit[]): Omit<Card, "ack"> | undefined {
  if (!["NUMBER_NOT_IN_SOURCE", "TEXT_NOT_IN_SOURCE"].includes(code)) return undefined;
  const nid: string | undefined = node?.node_id;
  const nodeField = /^items\[\d+\](?:\.children\[\d+\])?\.(.+)$/.exec(rest)?.[1];
  const fieldPath = nodeField ?? (day ? rest.replace(/^day\./, "") : rest);
  const resolveIn = (c: Row) => {
    const scope = nodeField ? locate(dayFor(c, base.day ?? 0), subject, nid) : day ? dayFor(c, base.day ?? 0) : c;
    return scope ? follow(scope, fieldPath) : undefined;
  };
  const here = resolveIn(content);
  if (!here || (here.parent[here.key] !== null && typeof here.parent[here.key] === "object")) return undefined;
  const value = detail.split("（")[0];
  if (!value) return undefined;
  const current = here.parent[here.key];
  const convert = (v: string) => (numeric.has(here.key) && v.trim() !== "" && !Number.isNaN(Number(v)) ? Number(v) : v);
  const name = node?.name;
  const field = fieldNames[here.key] ?? "";
  const title = `${name ? `《${name}》· ` : ""}${field || "数字"}核对`;
  const ev = evidenceFor(content, units, day, node, undefined, name ?? value);
  const put = (v: unknown) => (c: Row) => { const t = resolveIn(c); if (t) t.parent[t.key] = v; };
  if (empty(current)) {
    return { ...base, title, question: `“${value}”在原文里找不到，系统已清空这一项。`, evidence: ev, options: [
      { id: "blank", label: "确认留空", keeps: true, apply: put(typeof current === "number" || current === null && numeric.has(here.key) ? null : "") },
      { id: "restore", label: "原文有，填回", input: { label: "内容", value }, apply: (c, input) => put(convert(input?.trim() || value))(c) },
    ] };
  }
  return { ...base, title, question: `“${value}”里有数字在原文里找不到。`, evidence: ev, options: [
    { id: "keep", label: "原文有，保留", keeps: true, apply: put(current) },
    { id: "clear", label: "原文没有，清空", apply: put(typeof current === "number" || numeric.has(here.key) ? null : "") },
  ] };
}

export const counts = (cards: Card[], chosen: Record<string, { option: string }>) => ({
  total: cards.length,
  done: cards.filter((c) => chosen[c.key]).length,
});

const titleDays = (content: Row) => {
  const text = `${content.listed_name || ""} ${content.title || ""}`;
  const m = /(\d{1,2})\s*天(?:\s*(\d{1,2})\s*晚)?/.exec(text);
  return m ? { days: Number(m[1]), text: m[0].replace(/\s+/g, "") } : undefined;
};

// Where the content disagrees with the route's listing (days, gateway): the choice is a warehouse
// decision recorded beside the upstream value, or an edit of the content. A note never settles it.
function conflictCard(base: Base, code: string, issue: Row, content: Row, ctx: Context): Omit<Card, "ack"> | undefined {
  const none = { lines: [], needle: "", wide: [] };
  if (code === "DAYS_DIFFER_FROM_LISTING" && ctx.listing) {
    const upstream = ctx.listing.days.upstream;
    const mine = content.days_count;
    const title = titleDays(content);
    const spans = (ctx.departureDays ?? []).map((d) => `${d.days} 天（${d.count} 个团期）`).join("、");
    const facts: Witness[] = [
      ...(title ? [{ source: "线路标题", value: title.text }] : []),
      { source: "上游登记", value: `${upstream} 天` },
      ...(spans ? [{ source: "团期日期跨度", value: spans }] : []),
      { source: "附件行程", value: `${mine} 天${content.nights != null ? `${content.nights} 晚` : ""}` },
    ];
    return { ...base, title: "行程天数以哪个为准", question: `附件行程是 ${mine} 天，上游登记是 ${upstream} 天。云仓不改上游数据，选定后本线路在云仓里统一按选定的天数展示和检索。`, facts, evidence: none, options: [
      { id: "mine", label: `以行程为准：${mine} 天（云仓核定，上游保持 ${upstream} 天）`, decide: { field: "days", value: mine }, apply: () => {} },
      { id: "upstream", label: `以上游为准：${upstream} 天（回到高级编辑，改行程）`, jump: true, apply: () => {} },
    ] };
  }
  if (code === "DEPARTURE_DURATION_MISMATCH") {
    const facts = (ctx.departureDays ?? []).map((d) => ({ source: "团期日期跨度", value: `${d.days} 天（${d.count} 个团期）` }));
    if (!issue.acknowledgeable) return ctx.daysConflict
      ? { ...base, title: "团期日期与行程天数不一致", question: `${issue.message} 请先处理行程天数取舍，随后重新检查团期。`, facts, evidence: none, options: [], waiting: "先处理行程天数" }
      : { ...base, title: "仍有团期不适用当前行程", question: `${issue.message} 这些跨度不能由当前核定放行，请核对行程适用日期或采用对应版本。`, facts, evidence: none, options: [], editOnly: true };
    return { ...base, title: "团期日期与行程天数不一致", question: "已按云仓核定的天数处理。团期日期来自上游，仍是其他天数，请确认可以照此发布。", facts, evidence: none, options: [
      { id: "ok", label: "确认，团期日期以上游为准", keeps: true, apply: () => {} },
    ] };
  }
  if (code === "GATEWAY_DIFFERS_FROM_LISTING" && ctx.listing) {
    const upstream = String(ctx.listing.gateway.upstream ?? "");
    const effective = String(ctx.listing.gateway.effective ?? upstream);
    const mine = String(content.depart_city || "");
    return { ...base, title: "出发地写法不一致", question: `行程里写的出发地是“${mine}”，线路登记的出发口岸是“${effective}”。顾问按出发口岸筛选线路，行程页展示的是行程里的出发地。`, facts: [{ source: "线路登记口岸", value: effective }, { source: "行程出发地", value: mine }], evidence: none, options: [
      { id: "gateway", label: `口岸以行程为准：${mine}（云仓核定，上游保持“${upstream}”）`, decide: { field: "gateway", value: mine }, apply: () => {} },
      { id: "listing", label: `以线路登记为准：${effective}（把行程里的出发地改成它）`, apply: (c) => { c.depart_city = effective; } },
      { id: "both", label: "两者都对：行程写的是出发城市，登记的是口岸，不用改", apply: () => {} },
    ] };
  }
  return undefined;
}
