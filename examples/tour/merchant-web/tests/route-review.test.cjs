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
const ctx = { listing };

test("a day decision changes display facts without asking for departure changes", () => {
  const previous = buildCards([dayConflict], content, [], ctx);
  const decision = previous[0];
  assert.equal(decision.facts.some(f => f.source.includes("团期")), false);
  const before = structuredClone(content);
  const option = decision.options.find(o => o.decide);
  option.apply(content);
  assert.deepEqual(content, before);
  const choices = { [decision.key]: { option: option.id } };
  const next = reconcileCards(previous, buildCards([], content, [], ctx), choices);
  assert.deepEqual(next.cards, previous);
  assert.deepEqual(next.choices, choices);
});

test("historical departure duration warnings cannot add a reassignment step", () => {
  for (const acknowledgeable of [true, false]) {
    assert.deepEqual(buildCards([{ ...mismatch, acknowledgeable }], content, [], ctx), []);
  }
});

test("a newly blocking content issue invalidates its earlier confirmation", () => {
  const issue = { code: "FEE_CONFLICT", path: "fees", message: "费用包含与不含冲突" };
  const previous = buildCards([{ ...issue, acknowledgeable: true }], content, [], ctx);
  const choices = { [previous[0].key]: { option: previous[0].options[0].id } };
  const next = reconcileCards(previous, buildCards([{ ...issue, acknowledgeable: false }], content, [], ctx), choices);
  assert.deepEqual(next.choices, {});
  assert.equal(next.cards[0].editOnly, true);
});
