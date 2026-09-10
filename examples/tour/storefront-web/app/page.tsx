// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";
import { type AgentEvent, StoreShell, type StoreView, useAgentTurn } from "web-shared";
import Chat from "@/components/Chat";
import HoldBar from "@/components/HoldBar";
import HoldPanel from "@/components/HoldPanel";
import { SessionButton, SessionDrawer } from "@/components/SessionPanel";
import HomeView from "@/components/views/HomeView";
import { api, UNREACHABLE } from "@/lib/api";
import { ASSISTANT, BRAND } from "@/lib/brand";
import { TOUR_COPY } from "@/lib/copy";
import { formatYuan } from "@/lib/format";
import { replayItems, useAdvisorSession } from "@/lib/sessions";
import type { CartPayload } from "@/lib/types";

/** The agency's own name, which `lib/brand.ts` takes from the deployment. */
function Wordmark() {
  return (
    <span className="pr-1 text-[17px] font-semibold tracking-[-0.01em] text-(--ink)">
      <span aria-hidden className="mr-1.5 text-[13px] text-(--accent)">
        ◆
      </span>
      {BRAND}
    </span>
  );
}

export default function StorefrontPage() {
  const session = useAdvisorSession(api);
  const [cart, setCart] = useState<CartPayload | null>(null);
  // A staged 报价单 owns the panel's primary action until the 占位 change again.
  const [quoteStaged, setQuoteStaged] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [sessionsOpen, setSessionsOpen] = useState(false);

  const onEvent = useCallback((event: AgentEvent) => {
    if (event.type === "cart_update") {
      setCart(event.data.cart as CartPayload);
      setQuoteStaged(false);
    } else if (event.type === "ui" && event.data.component === "checkout") {
      setQuoteStaged(true);
    }
  }, []);

  const chat = useAgentTurn(api, {
    sessionId: session.sessionId,
    unreachable: UNREACHABLE,
    onEvent,
  });
  const { setItems } = chat;

  // Moving into a session replaces the conversation with what was said in it: the resumed
  // messages, or nothing at all in one just started.
  useEffect(() => {
    if (session.resumed) setItems(replayItems(session.resumed.messages));
  }, [session.resumed, setItems]);

  useEffect(() => {
    if (session.sessionId) void api.fetchCart<CartPayload>().then((next) => next && setCart(next));
  }, [session.sessionId]);

  // A turn can let a hold expire without changing the cart, so the holds are re-read after one;
  // a reply also retitles its session and adds to its count.
  const { refresh } = session;
  useEffect(() => {
    if (!session.sessionId || !chat.completed) return;
    void api.fetchCart<CartPayload>().then((next) => next && setCart(next));
    refresh();
  }, [session.sessionId, chat.completed, refresh]);

  const openSessions = useCallback(() => {
    setSessionsOpen(true);
    refresh();
  }, [refresh]);

  // The workbench is one view: the conversation, with the 占位 beside it.
  const views: StoreView<"assistant">[] = [{ id: "assistant", label: "选团", icon: "search" }];
  const advisor = session.shopper ?? { name: "顾问" };
  const holds = cart?.holds?.length ?? 0;

  return (
    <StoreShell
      brand={
        <div className="flex items-center gap-1">
          <Wordmark />
          <SessionButton onOpen={openSessions} />
        </div>
      }
      views={views}
      view="assistant"
      onViewChange={() => {}}
      chat={chat}
      api={api}
      assistantName={ASSISTANT}
      shopper={advisor}
      bag={{
        label: "占位",
        count: holds,
        noun: "个团期",
        figure: holds ? formatYuan(cart?.subtotal ?? 0) : null,
      }}
      panel={<HoldPanel cart={cart} quoteStaged={quoteStaged} />}
      panelOpen={panelOpen}
      onPanelOpenChange={setPanelOpen}
      banner={<HoldBar cart={cart} />}
      placeholder="把客人的原话打进来：去哪儿、几天、几大几小、什么时候走…"
      copy={TOUR_COPY}
    >
      <Chat chat={chat} home={<HomeView advisorName={advisor.name} store={advisor.tier} />} />
      <SessionDrawer
        session={session}
        open={sessionsOpen}
        onClose={() => setSessionsOpen(false)}
        busy={chat.busy}
      />
    </StoreShell>
  );
}
