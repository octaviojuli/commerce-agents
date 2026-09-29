const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const test = require("node:test");
const ts = require("typescript");
const source = fs.readFileSync(path.join(__dirname, "../lib/route-review.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const loaded = { exports: {} };
new Function("module", "exports", compiled)(loaded, loaded.exports);
const { buildCards, reconcileCards } = loaded.exports;
const content = { days_count: 7, title: "ACME 7天5晚", days: [], quality: { issues: [] } };
const listing = { days: { upstream: 6, effective: 7, origin: "warehouse_decision", state: "pending" } };
const mismatch = { code: "DEPARTURE_DURATION_MISMATCH", path: "days_count", message: "团期跨 10 天，与行程 7 天不一致", acknowledgeable: false };
const dayConflict = { code: "DAYS_DIFFER_FROM_LISTING", path: "days_count", message: "需核定", acknowledgeable: false };
const ctx = { listing, departureDays: [{ days: 6, count: 1 }, { days: 10, count: 1 }] };

test("a decided day conflict unlocks editing a remaining mixed departure span", () => {
  const previous = buildCards([dayConflict, mismatch], content, [], ctx);
  const decision = previous.find(c => c.code === dayConflict.code);
  assert.equal(previous.find(c => c.code === mismatch.code).waiting, "先处理行程天数");
  const choices = { [decision.key]: { option: decision.options.find(o => o.decide).id } };
  const next = reconcileCards(previous, buildCards([mismatch], content, [], ctx), choices);
  const remaining = next.cards.find(c => c.code === mismatch.code);
  assert.equal(remaining.waiting, undefined);
  assert.equal(remaining.editOnly, true);
  assert.match(remaining.question, /10 天/);
  assert.deepEqual(next.choices, choices);
});

test("only a covered upstream span becomes explicitly confirmable", () => {
  const previous = buildCards([dayConflict, mismatch], content, [], ctx);
  const fresh = buildCards([{ ...mismatch, acknowledgeable: true }], content, [], ctx);
  const next = reconcileCards(previous, fresh, {});
  assert.equal(next.cards[0].waiting, undefined);
  assert.equal(next.cards[0].options[0].id, "ok");
  assert.equal(next.cards[0].ack, true);
});

test("a newly blocked span invalidates its earlier confirmation", () => {
  const previous = buildCards([{ ...mismatch, acknowledgeable: true }], content, [], ctx);
  const choices = { [previous[0].key]: { option: "ok" } };
  const next = reconcileCards(previous, buildCards([mismatch], content, [], ctx), choices);
  assert.deepEqual(next.choices, {});
  assert.equal(next.cards[0].editOnly, true);
});
