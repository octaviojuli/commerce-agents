# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Drive a running example through its hero turns in a headless browser and save screenshots.

Requires the vertical's API and app to be running (scripts/run_demo.py) and Playwright with
Chromium (``pip install playwright && playwright install chromium``):

    python scripts/screenshot_tour.py --vertical travel [--base-url URL] [--out DIR]
    python scripts/screenshot_tour.py --vertical travel --merchant

A storefront tour captures the home, a mid-stream frame, each turn, the vertical's second view
(orders, trips, account, tickets), the shopper's sheet, and the activity panel twice (memory
marked, then the last reply's steps). A portal tour captures the views, the opening turns, the
applied state after approving the staged change, the turns that follow the approval, and the
same two activity-panel frames. Output goes to /tmp/acme-<vertical>-tour or
/tmp/acme-<vertical>-merchant-tour.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# The chrome's words the tour locates by; a vertical in another language overrides them.
LABELS: dict[str, str] = {
    "views": "Views",
    "assistant": "Assistant",
    "activity": "Activity",
    "account_sheet": "profile and memory",
    "new_fact": "new this session",
    "working": "working",
    "portal_views": "Portal views",
    "home": "Home",
    "approve": "Approve",
}

ZH_LABELS: dict[str, str] = {
    "views": "视图",
    "assistant": "助手",
    "activity": "活动",
    "account_sheet": "资料与记忆",
    "new_fact": "本次新增",
    "working": "处理中",
    "portal_views": "工作台视图",
    "home": "首页",
    "approve": "批准",
}

VERTICALS: dict[str, dict] = {
    "retail": {
        "base_url": "http://localhost:3000",
        "view": ("orders", "Orders"),
        "turns": [
            (
                "search-carousel",
                "My niece turns nine next month and she's deep into building sets "
                "and puzzles — what would make a great gift? Somewhere under $45.",
            ),
            (
                "comparison",
                "Compare the top two — which is sturdier and better for a 9-year-old?",
            ),
            (
                "cart-checkout",
                "Add the wooden block set to my cart — that's everything, I'm ready to check out.",
            ),
            ("order-status", "Separate thing: where's my dog bed order? It feels late."),
            (
                "plan",
                "New project: help me set up a home office in a small spare room. "
                "Budget about $800 all-in.",
            ),
            (
                "research-guide",
                "Different question — I want to sort out a proper coffee setup at home but I "
                "don't know where to start. What should I be thinking about?",
            ),
            (
                "research-pick",
                "We get through a lot of it in the mornings and I don't want any fuss — just "
                "tell me what you'd get.",
            ),
        ],
    },
    # The travel apps are in Chinese, so the tour asks in it and locates by ZH_LABELS.
    "travel": {
        "base_url": "http://localhost:3001",
        "labels": ZH_LABELS,
        "view": ("trips", "我的行程"),
        "turns": [
            (
                "itinerary",
                "帮我规划一个十月中旬的里斯本长周末，按天列出来。",
            ),
            (
                "refundable-comparison",
                "把这家住宿和一个可退款的备选比一比，灵活性到底要多花多少钱？",
            ),
            (
                # Volunteers a durable preference so the async extractor saves a NEW fact
                # and the memory-panel frame has a marked fact to show.
                "add-refundable",
                "把可退款的那个加进我的行程。我伴侣的日程经常变，所以我们一向只订可退款的。"
                "它的取消期限是怎样的？",
            ),
            (
                "boarding-pass",
                "目前就这些了，帮我把行程的结算准备好。",
            ),
        ],
    },
    "telecom": {
        "base_url": "http://localhost:3002",
        "view": ("account", "Account"),
        "turns": [
            (
                "plan-matrix",
                "I keep running out of data before the month ends. What plan would "
                "actually fit me? Show the options side by side.",
            ),
            (
                "upgrade-eligibility",
                "Am I eligible to upgrade my ACME Phone 4 yet? Show me what I could "
                "move to and what the trade-in would get me.",
            ),
            (
                "service-facts",
                "Before I change anything — what's the early termination fee on my "
                "plan, really? Show me the official service facts.",
            ),
            (
                "plan-switch",
                "Switch me to the Plus 15GB plan and add it to my order — and confirm "
                "there's no fee for changing plans mid-cycle. For the record, I'd "
                "always rather have a predictable bill than the absolute cheapest "
                "price.",
            ),
            (
                "activation-ticket",
                "That's everything — check out the plan change.",
            ),
        ],
    },
    "entertainment": {
        "base_url": "http://localhost:3003",
        "view": ("tickets", "Tickets"),
        "turns": [
            (
                "venue-map",
                "Two tickets to The Headliner show on the Friday night — somewhere "
                "with actual seats, not the pit. What are my tier options, and where "
                "do they put me in the room?",
            ),
            (
                "tier-comparison",
                "Compare the seated tiers for that night side by side — which is the "
                "better night out for the money?",
            ),
            (
                "fee-breakdown",
                "Before I commit — break down exactly what the Lower Bowl price is "
                "made of. Face value, every fee, the lot.",
            ),
            (
                # Volunteers a durable constraint so the async extractor saves a NEW fact
                # and the memory-panel frame has a marked fact to show.
                "checkout-hold",
                "Hold two Lower Bowl tickets for me while I find my card. Also worth "
                "knowing for next time: weeknights never work for us — weekend shows "
                "only from here on.",
            ),
        ],
    },
}


# The merchant/supplier portals (mounted on each vertical's API, served on their own ports).
MERCHANT_TOURS: dict[str, dict] = {
    "retail": {
        "base_url": "http://localhost:3100",
        "assistant_box_label": "Message the merchant assistant",
        "views": [
            ("catalog", "Catalog"),
            ("orders", "Orders"),
            ("inventory", "Inventory"),
        ],
        "turns": [
            ("morning-digest", "What needs my attention this morning?"),
            (
                "change-preview",
                "Restock the ocean wall decals with enough to cover the next month at the "
                "current pace, and fix that listing's description so it covers what's been "
                "missing. Show me both before anything goes live.",
            ),
        ],
        "after": [
            (
                "trend-check",
                "Kids-room decor feels like it's having a moment. Pull the numbers — is the "
                "under-the-sea line really outperforming the rest of the store this month?",
            ),
            (
                "sales-drivers",
                "Why did sales move over the last two weeks — which category or listings drove "
                "it, and by how much?",
            ),
        ],
    },
    "travel": {
        "base_url": "http://localhost:3101",
        "labels": ZH_LABELS,
        "assistant_box_label": "给供应商助手发消息",
        "views": [("properties", "房源"), ("bookings", "预订")],
        "turns": [
            (
                "october-pacing",
                "首页说有两家住宿预订进度偏慢。是哪两家？它们十月的日历怎么样？房价该往哪调？",
            ),
            (
                "rate-preview",
                "把进度最慢的两家房源的平日房价下调一成左右，但只针对它们十月偏慢的那几周，"
                "其余日期保持不变。先给我看影响，再决定要不要生效。",
            ),
        ],
        "after": [
            (
                "campaign-draft",
                "再为这两家房源起草一个配合调价的平季营销活动：给老客人发邮件，预算大约 $600。"
                "先存为草稿，我要先看一遍。",
            ),
            (
                "listing-check",
                "我们哪些房源是在页面本身上流失预订的？如果描述太薄，告诉我是哪一个；"
                "如果页面没问题，直说，并告诉我差距到底在哪。",
            ),
        ],
    },
    "telecom": {
        "base_url": "http://localhost:3102",
        "assistant_box_label": "Message the operations assistant",
        "views": [("catalog", "Catalog"), ("base", "Base")],
        "turns": [
            (
                "churn-by-plan",
                "Show me churn by plan across the base. Which plan is bleeding, and since when?",
            ),
            (
                "retention-preview",
                "Put a retention offer in front of the Essential lines that keep buying "
                "top-ups: a month of Plus 15GB at the Essential price if they move up. Stage "
                "it as a campaign to that cohort, app push, $1,500. Show me the draft before "
                "anything goes out.",
            ),
        ],
        "after": [
            (
                "restock-preview",
                "Stock check while I'm here. Whatever device the alerts are flagging, stage a "
                "restock sized from its sell-through so we're covered for the next month. "
                "Preview first.",
            ),
            (
                "net-adds",
                "Where did last week's net adds come from against the week before? Put gross "
                "adds, deacts, port-ins and port-outs on one card and tell me what moved.",
            ),
        ],
    },
    "entertainment": {
        "base_url": "http://localhost:3103",
        "assistant_box_label": "Message the box-office assistant",
        "views": [("events", "Events"), ("holds", "Holds")],
        "turns": [
            (
                "pacing-review",
                "How is the Friday Headliner show pacing against comparable events?",
            ),
            (
                "release-preview",
                "Release sixty of the promoter-hold seats on the Upper Terrace so they're "
                "on sale for the final stretch. Show me the release before it goes live.",
            ),
        ],
        "after": [
            (
                "price-preview",
                "While we're on that tier: tag the Upper Terrace listing 'almost gone' and "
                "take its price up 15% for the final push. Stage both and show me before "
                "anything goes live.",
            ),
            (
                "weekly-sales",
                "Which shows sold the most tickets this week, and how does each compare with "
                "the week before? Put it on a card.",
            ),
        ],
    },
}


def labels_of(config: dict) -> dict[str, str]:
    return {**LABELS, **config.get("labels", {})}


def wait_for_turn_to_finish(page, labels: dict[str, str], timeout_ms: int = 180_000) -> None:
    """The message box's placeholder reads "Working…" while a reply streams."""
    page.wait_for_timeout(1_200)
    page.wait_for_function(
        "(word) => { const box = document.querySelector('textarea');"
        " return box && !box.placeholder.toLowerCase().includes(word); }",
        arg=labels["working"].lower(),
        timeout=timeout_ms,
    )
    page.wait_for_timeout(800)


def capture(page, output_dir: Path, name: str, **kwargs) -> None:
    page.screenshot(path=str(output_dir / f"{name}.png"), **kwargs)
    print(f"captured {name}")


def capture_activity_panel(
    page, output_dir: Path, name: str, wait_for_memory: bool, labels: dict[str, str]
) -> None:
    """Open the activity panel on the latest reply and capture it. With ``wait_for_memory``
    the capture waits for the memory section to mark a fact saved this session, which the
    app re-reads a few seconds after the reply settles."""
    button = page.get_by_role("button", name=labels["activity"])
    if button.count() == 0:
        print(f"no Activity control found — skipped {name}")
        return
    button.first.click()
    page.wait_for_timeout(700)
    if wait_for_memory:
        try:
            page.get_by_text(labels["new_fact"]).first.wait_for(timeout=12_000)
        except PlaywrightTimeoutError:
            print("no fact was saved this session — capturing the panel unmarked")
    capture(page, output_dir, name)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


def launch_browser(playwright):
    """The bundled Chromium, or an installed Chrome/Edge when it will not start."""
    last_error: Exception = RuntimeError("no Chromium-based browser could be launched")
    for kwargs in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return playwright.chromium.launch(**kwargs)
        except Exception as error:  # try the next channel
            last_error = error
    raise last_error


def run_storefront_tour(page, config: dict, output_dir: Path) -> None:
    labels = labels_of(config)
    counter = 0

    def numbered(name: str) -> str:
        nonlocal counter
        counter += 1
        return f"{counter:02d}-{name}"

    capture(page, output_dir, numbered("home"))
    for index, (name, message) in enumerate(config["turns"]):
        box = page.locator("textarea")
        box.fill(message)
        box.press("Enter")
        if index == 0:
            page.wait_for_timeout(4_500)
            capture(page, output_dir, numbered(f"{name}-streaming"))
        wait_for_turn_to_finish(page, labels)
        # Rest the frame on the newest card rather than the end of the closing prose.
        components = page.locator("[data-component]")
        if components.count() > 0:
            components.last.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
        capture(page, output_dir, numbered(name))

    # The vertical's second view, then the shopper's sheet, then back to the conversation.
    views = page.locator(f'nav[aria-label="{labels["views"]}"]').first
    shot, label = config["view"]
    views.get_by_role("button", name=label).click()
    page.wait_for_timeout(900)
    capture(page, output_dir, numbered(shot))
    page.locator(f'header button[aria-label$="{labels["account_sheet"]}"]').first.click()
    page.wait_for_timeout(700)
    capture(page, output_dir, numbered("account-sheet"))
    page.keyboard.press("Escape")
    views.get_by_role("button", name=labels["assistant"]).click()
    page.wait_for_timeout(500)

    capture_activity_panel(page, output_dir, numbered("memory-panel"), True, labels)
    capture_activity_panel(page, output_dir, numbered("inspector"), False, labels)


def run_merchant_tour(page, config: dict, output_dir: Path) -> None:
    labels = labels_of(config)
    # Frames are numbered in capture order: home, the views, the turns, then the fixed tail.
    frame = iter(range(1, 100))

    def numbered(name: str) -> str:
        return f"{next(frame):02d}-{name}"

    capture(page, output_dir, numbered("home"))
    views = page.locator(f'nav[aria-label="{labels["portal_views"]}"]').first
    for name, label in config["views"]:
        views.get_by_role("button", name=label).click()
        page.wait_for_timeout(900)
        capture(page, output_dir, numbered(name))
    views.get_by_role("button", name=labels["home"]).click()
    page.wait_for_timeout(600)

    box = page.locator(f'textarea[aria-label="{config["assistant_box_label"]}"]')

    def send(turns: list[tuple[str, str]]) -> None:
        for name, message in turns:
            box.fill(message)
            box.press("Enter")
            wait_for_turn_to_finish(page, labels)
            capture(page, output_dir, numbered(name), full_page=True)

    send(config["turns"])
    # Approve the first staged change on its card, then carry on with the turns that
    # build on the applied state.
    approve = page.get_by_role("button", name=labels["approve"])
    if approve.count() > 0:
        approve.first.click()
        page.wait_for_timeout(2_500)
        capture(page, output_dir, numbered("applied"), full_page=True)
    else:
        print("no Approve button found — skipped applied capture")
    send(config["after"])

    capture_activity_panel(page, output_dir, numbered("memory-panel"), True, labels)
    capture_activity_panel(page, output_dir, numbered("inspector"), False, labels)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vertical", default="retail", choices=sorted(VERTICALS))
    parser.add_argument("--merchant", action="store_true", help="tour the merchant portal")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    config = (MERCHANT_TOURS if args.merchant else VERTICALS)[args.vertical]
    suffix = "merchant-tour" if args.merchant else "tour"
    output_dir = Path(args.out or f"/tmp/acme-{args.vertical}-{suffix}")
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1480, "height": 940})
        page.goto(args.base_url or config["base_url"], wait_until="networkidle")
        page.wait_for_timeout(900)
        (run_merchant_tour if args.merchant else run_storefront_tour)(page, config, output_dir)
        browser.close()
    print(f"Saved screenshots to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
