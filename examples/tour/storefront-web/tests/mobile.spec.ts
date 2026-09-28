import { expect, test, type Page } from "@playwright/test";

// Fictional presentation fixture. Merchant/model integration is tested separately
// against PostgreSQL and the actual model; no credentials or supplier data here.
const id = "00000000-0000-4000-8000-000000000001";
const value = (v: unknown) => ({
  value: v,
  source: "said",
  evidence: "ACME 验收",
  hint: "",
});
const body = Object.fromEntries(
  [
    "destinations",
    "destination_regions",
    "destination_examples",
    "excluded_destinations",
    "themes",
    "window",
    "days",
    "adults",
    "children",
    "seniors",
    "child_ages",
    "party_total",
    "rooms",
    "depart_city",
    "budget",
    "preferences",
  ].map((k) => [k, value(null)]),
);
Object.assign(body, {
  destinations: value(["意大利"]),
  window: value({ start: "2030-12-26", end: "2031-01-03" }),
  adults: value(2),
  children: value(1),
  child_ages: value([8]),
  preferences: value([{ key: "slow_pace", label: "慢节奏" }]),
});
const brief = {
  version: 2,
  body,
  readiness: {
    search: { ready: true, missing: [], inferred: [] },
    quote: { ready: false, missing: ["rooms"], inferred: [] },
  },
  clarity: "clear",
  stage: "select",
  allowed_actions: ["search_routes"],
  quote_stale: false,
};
const products = [1, 2, 3].map((n) => ({
  product_id: `WP-00000000-0000-4000-8000-00000000000${n}`,
  title: `X${n}-ACME 意大利家庭慢游`,
  attributes: {
    days: "12",
    depart_city: "上海",
    next_departure: "2030-12-27",
    match_reasons: JSON.stringify([
      { criterion: "depart_city", verdict: "ok", text: "上海出发，符合需求" },
      { criterion: "no_shopping", verdict: "unknown", text: "购物安排待核实" },
    ]),
  },
}));
const deals = [1, 2, 3, 4, 5].map((n) => ({
  id: id.slice(0, -1) + n,
  title: `ACME 家庭 ${n}`,
  brief: body,
  brief_version: 2,
  updated_at: "2030-12-01T10:00:00Z",
  pending_tasks: 1,
}));

async function fixture(page: Page) {
  await page.addInitScript(() =>
    localStorage.setItem("tour-warehouse-advisor-token", "ACME-fixture-only"),
  );
  await page.route("**/warehouse-api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      "/warehouse-api/v1",
      "",
    );
    let data: unknown = { items: [], next_cursor: null };
    if (path === "/me/organizations")
      data = { items: [{ id, name: "ACME 顾问", roles: ["advisor"] }] };
    else if (path === "/copilot/deals")
      data = { items: deals, next_cursor: null };
    else if (path === `/copilot/deals/${id}`)
      data = {
        ...deals[0],
        brief,
        customer: null,
        records: [],
        assets: [],
        pending_fields: {},
        memory: { concerns: [], qa: [], timeline: [], sent: [] },
        ledger: { sale: null, receipts: [], received: "0" },
      };
    else if (path === `/conversations/${id}`)
      data = {
        busy: false,
        next_cursor: null,
        turns: [
          {
            id: "ACME-turn",
            message: "带孩子去欧洲，想节奏慢一点。",
            status: "complete",
            events: [
              {
                type: "ui",
                data: {
                  component: "warehouse_routes",
                  payload: { brief_version: 2, items: products },
                },
              },
              {
                type: "ui",
                data: {
                  component: "copilot_reply",
                  payload: {
                    brief_version: 2,
                    to_advisor: "先核对亲子安排，再选择团期。",
                    to_customer:
                      "我记下了您希望节奏慢、照顾孩子的想法。您更看重自然风景，还是城市文化？",
                    next_action: "search_routes",
                    allowed_actions: ["search_routes"],
                    chips: [{ action: "search_routes", label: "重新找线" }],
                    customer_may_ask: ["房间怎么安排？"],
                  },
                },
              },
            ],
          },
        ],
      };
    else if (path.endsWith("/nodes")) data = { facts: [] };
    return route.fulfill({ json: data });
  });
}

async function dimensions(page: Page) {
  return page.evaluate(() => {
    const visible = (e: Element) =>
      e.getBoundingClientRect().width > 0 &&
      e.getBoundingClientRect().height > 0 &&
      getComputedStyle(e).visibility !== "hidden";
    return {
      overflow: document.documentElement.scrollWidth - innerWidth,
      smallInputs: [
        ...document.querySelectorAll(
          "input:not([type=checkbox]),textarea,select",
        ),
      ]
        .filter(visible)
        .filter((e) => parseFloat(getComputedStyle(e).fontSize) < 16)
        .map((e) => e.outerHTML.slice(0, 140)),
      smallButtons: [...document.querySelectorAll("button,summary")]
        .filter(visible)
        .filter((e) => e.getBoundingClientRect().height < 43.9)
        .map((e) => e.textContent),
      header: document.querySelector(".cp-fixed-top")?.getBoundingClientRect()
        .height,
      composer: document.querySelector(".cp-composer")?.getBoundingClientRect()
        .height,
      cards: [...document.querySelectorAll(".cp-route-card")].map(
        (e) => e.getBoundingClientRect().height,
      ),
    };
  });
}

for (const [width, height] of [
  [375, 667],
  [390, 844],
  [360, 780],
]) {
  test(`${width}x${height}: touch, text, fixed areas and overflow`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height });
    await fixture(page);
    await page.goto(`/?deal=${id}`);
    await expect(page.locator(".cp-route-card")).toHaveCount(3);
    const chat = await dimensions(page);
    expect(chat.smallInputs).toEqual([]);
    expect(chat.smallButtons).toEqual([]);
    expect(chat.overflow).toBeLessThanOrEqual(1);
    expect(chat.header).toBeLessThanOrEqual(112);
    expect(chat.composer).toBeLessThanOrEqual(72);
    expect(Math.max(...chat.cards)).toBeLessThanOrEqual(360);
    await page.locator(".cp-brief-status button").click();
    await expect(
      page.getByRole("dialog", { name: "需求与记忆" }),
    ).toBeVisible();
    const sheet = await dimensions(page);
    expect(sheet.smallInputs).toEqual([]);
    expect(sheet.smallButtons).toEqual([]);
    expect(sheet.overflow).toBeLessThanOrEqual(1);
    await page.getByRole("button", { name: "关闭需求与记忆" }).click();
    await page.getByRole("button", { name: "返回工作台" }).click();
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "跟单", exact: true })
      .click();
    await expect(page.locator(".cp-deal-card")).toHaveCount(5);
    const cards = await page
      .locator(".cp-deal-card")
      .evaluateAll((elements) =>
        elements.map((e) => ({
          top: e.getBoundingClientRect().top,
          bottom: e.getBoundingClientRect().bottom,
          height: e.getBoundingClientRect().height,
        })),
      );
    expect(cards.every((c) => c.height <= 150)).toBe(true);
    expect(cards[3].bottom).toBeLessThanOrEqual(height - 52);
    await info.attach("mobile-measurements", {
      body: JSON.stringify({ width, height, chat, sheet, cards }, null, 2),
      contentType: "application/json",
    });
  });
}
