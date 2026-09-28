// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useState } from "react";
import { ArrivingPanel, estimateOf, Greeting, HomeSection, type Order, type Starter, Starters, upcoming, useStoreFrame } from "web-shared";
import { ASSISTANT } from "@/lib/copy";
import { NOUNS, TripThumb } from "@/lib/orders";
import { PostcardWindow } from "../PostcardWindow";

const STARTERS: Starter[] = [
  { icon: "calendar", prompt: "帮我规划一个里斯本的长周末" },
  { icon: "plane", prompt: "比较一下飞京都的航班" },
  { icon: "return", prompt: "雷克雅未克有没有可免费取消的住宿" },
  { icon: "pin", prompt: "我的马拉喀什预订进度如何？" },
];

/** The postcards: the catalog's city names (the keys of DESTINATION_GRADIENTS in lib/format.ts) and how the traveler says them. */
const POSTCARD_CITIES: { city: string; name: string }[] = [
  { city: "Lisbon", name: "里斯本" },
  { city: "Kyoto", name: "京都" },
  { city: "Mexico City", name: "墨西哥城" },
  { city: "Reykjavik", name: "雷克雅未克" },
  { city: "Marrakesh", name: "马拉喀什" },
  { city: "Queenstown", name: "皇后镇" },
];

/** Sends just before the 300ms mail animation ends. */
const MAILING_MS = 260;

const OPENER = `说出目的地，${ASSISTANT}为你找出值得留下的住宿、航班和每一天。`;

function Brief({ trips }: { trips: Order[] | null }) {
  const open = trips ? upcoming(trips) : [];
  if (!open.length) return <>{OPENER}</>;
  const next = estimateOf(open[0])?.date;
  return (
    <>
      {open.length} 次行程即将出发{next ? `，最近一次 ${next} 出发` : ""}。{OPENER}
    </>
  );
}

function Postcards() {
  const { ask, chat } = useStoreFrame();
  const [mailingCity, setMailingCity] = useState<string | null>(null);
  const disabled = !chat || chat.busy || !chat.ready;
  const planTrip = (city: string, name: string) => {
    const request = () => ask(`帮我规划一次${name}之旅`);
    // Reduced motion, or a card already on its way, sends at once.
    if (mailingCity || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      request();
      return;
    }
    setMailingCity(city);
    window.setTimeout(() => {
      setMailingCity(null);
      request();
    }, MAILING_MS);
  };
  return (
    <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
      {POSTCARD_CITIES.map(({ city, name }, index) => (
        <button
          key={city}
          type="button"
          onClick={() => planTrip(city, name)}
          disabled={disabled}
          aria-label={`规划一次${name}之旅`}
          className="al-reveal-item"
          style={{ animationDelay: `${(index + 4) * 60}ms` }}
        >
          {/* Transforms live here; the button's reveal animation would pin them. */}
          <div
            className={`overflow-hidden rounded-(--radius) border border-(--line) ${
              index % 2 ? "al-postcard-rest al-postcard-rest--alt" : "al-postcard-rest"
            } ${mailingCity === city ? "al-postcard-mailing" : ""}`}
          >
            <PostcardWindow city={city} title={city} className="aspect-[4/3] w-full" />
          </div>
        </button>
      ))}
    </div>
  );
}

export default function HomeView({ travelerName, trips, tripsFailed, onSeeTrips }: { travelerName: string; trips: Order[] | null; tripsFailed: boolean; onSeeTrips: () => void }) {
  return (
    <div className="flex flex-col gap-4">
      <Greeting
        title={
          <h1 className="al-hero">
            想去哪儿，<em>{travelerName}</em>？
          </h1>
        }
      >
        <Brief trips={trips} />
      </Greeting>
      <Starters items={STARTERS} />
      <ArrivingPanel orders={trips} failed={tripsFailed} nouns={NOUNS} thumb={(order) => <TripThumb order={order} />} onSeeAll={onSeeTrips} />
      <HomeSection title="从一张明信片开始" subtitle={`选一张，${ASSISTANT}就开始规划`}>
        <Postcards />
      </HomeSection>
    </div>
  );
}
