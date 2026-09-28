// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";
import { type AgentEvent, OrdersView, StoreShell, type StoreView, upcoming, useAgentTurn, useResource, useSession } from "web-shared";
import Chat from "@/components/Chat";
import TripPanel from "@/components/TripPanel";
import HomeView from "@/components/views/HomeView";
import { api, UNREACHABLE } from "@/lib/api";
import { ASSISTANT, BRAND, TRAVEL_COPY } from "@/lib/copy";
import { formatPrice } from "@/lib/format";
import { NOUNS, TripThumb } from "@/lib/orders";
import type { CartPayload } from "@/lib/types";

type View = "assistant" | "trips";

function Wordmark() {
  return (
    <span className="al-display pr-1 text-[22px] italic leading-none text-(--ink)" style={{ fontWeight: 650 }}>
      <span className="mr-1 not-italic text-[13px] text-(--accent)" aria-hidden>
        ◈
      </span>
      {BRAND}
    </span>
  );
}

export default function StorefrontPage() {
  const session = useSession(api);
  const [view, setView] = useState<View>("assistant");
  const [cart, setCart] = useState<CartPayload | null>(null);
  // A staged checkout owns the panel's primary action until the trip changes again.
  const [checkoutStaged, setCheckoutStaged] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  const onEvent = useCallback((event: AgentEvent) => {
    if (event.type === "cart_update") {
      setCart(event.data.cart as CartPayload);
      setCheckoutStaged(false);
    } else if (event.type === "ui" && event.data.component === "checkout") {
      setCheckoutStaged(true);
    }
  }, []);

  const chat = useAgentTurn(api, { ...session, unreachable: UNREACHABLE, onEvent });
  // A reply may have changed or refunded a booking, so trips re-read after each one.
  const { data: trips, failed: tripsFailed } = useResource(session.sessionId ? () => api.fetchOrders() : null, [session.sessionId, chat.completed]);

  useEffect(() => {
    if (session.sessionId) void api.fetchCart<CartPayload>().then((next) => next && setCart(next));
  }, [session.sessionId]);

  const views: StoreView<View>[] = [
    { id: "assistant", label: "助手", icon: "spark" },
    { id: "trips", label: "我的行程", icon: "plane" },
  ];
  const shopper = session.shopper ?? { name: "访客" };
  const count = cart?.items.length ?? 0;

  return (
    <StoreShell
      brand={<Wordmark />}
      views={views}
      view={view}
      onViewChange={setView}
      chat={chat}
      api={api}
      assistantName={ASSISTANT}
      shopper={shopper}
      bag={{ label: "本次行程", count, noun: "booking", figure: count ? formatPrice(cart?.subtotal ?? 0) : null }}
      panel={<TripPanel cart={cart} checkoutStaged={checkoutStaged} />}
      panelOpen={panelOpen}
      onPanelOpenChange={setPanelOpen}
      placeholder={view === "trips" ? "问问某次行程、改期或退款…" : "问问旅行、航班或预订…"}
      copy={TRAVEL_COPY}
    >
      {/* The conversation stays mounted under the other view so its cards keep their state. */}
      <div className={view === "assistant" ? "h-full" : "hidden"}>
        <Chat chat={chat} home={<HomeView travelerName={shopper.name} trips={trips} tripsFailed={tripsFailed} onSeeTrips={() => setView("trips")} />} />
      </div>
      {view === "trips" ? (
        <OrdersView
          orders={trips}
          failed={tripsFailed}
          nouns={NOUNS}
          subtitle={trips ? `${upcoming(trips).length} 次行程即将出发。可以问其中任何一次，或从过去的行程规划下一次。` : undefined}
          thumb={(order) => <TripThumb order={order} />}
        />
      ) : null}
    </StoreShell>
  );
}
