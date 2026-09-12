// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

/**
 * `present_route_days`: one 线路 as its 逐日行程. The card is the whole line — every day of the
 * reviewed 线路文档 open at once, the 去程 at the head and the 回程 at the tail as flight strips,
 * and under them what the document states around the days: 费用包含 and 费用不含, the 购物店 and
 * 自费项目 it lists, and its policies. Nothing here is a price: a 线路 is quoted by its 团期.
 * A streaming partial draws the head and the days that have landed, with the next one sized.
 */

import type { ReactNode } from "react";
import type {
  RouteDay,
  RouteDaysPayload,
  RouteFlight,
  RouteMeal,
  RouteSight,
} from "@/lib/types";
import { Cover } from "./shared";

/** What the 线路文档 makes of a stop, each kind its own muted ground. */
const SIGHT_TONE: Record<string, string> = {
  景点: "bg-(--accent-soft) text-(--accent-ink)",
  外观: "bg-(--info-soft) text-(--info)",
  购物: "bg-(--warn-soft) text-(--warn)",
  自费: "bg-(--violet-soft) text-(--violet)",
  赠送: "bg-(--ok-soft) text-(--ok)",
  自由活动: "bg-(--well) text-(--ink-2)",
};

/** Where a day sleeps when it sleeps nowhere a hotel line could name. */
const OVERNIGHT_TEXT: Record<string, string> = {
  flight: "夜宿飞机",
  ship: "夜宿邮轮",
  home: "抵达",
};

/** The card is a 行程, not a brochure: three 亮点 is what its header carries. */
const MAX_HIGHLIGHTS = 3;

const MEALS: [keyof NonNullable<RouteDay["meals"]>, string][] = [
  ["breakfast", "早"],
  ["lunch", "午"],
  ["dinner", "晚"],
];

/** "国航 CA931 · 上海浦东 → 法兰克福 · 01:45–07:05" on one line. */
function flightText(flight: RouteFlight): string {
  const carrier = [flight.carrier, flight.flight_no].filter(Boolean).join(" ");
  const leg =
    flight.from_place && flight.to_place ? `${flight.from_place} → ${flight.to_place}` : "";
  return [carrier, leg, flight.times].filter(Boolean).join(" · ");
}

/**
 * One leg as a torn ticket: the flight on the stub, the two places and the times on the body.
 * The notches are the card's own ground, so the tear reads at any card width.
 */
function FlightLeg({ flight }: { flight: RouteFlight }) {
  const carrier = [flight.carrier, flight.flight_no].filter(Boolean).join(" ");
  return (
    <div className="flex min-w-0 flex-1 items-stretch overflow-hidden rounded-(--radius) border border-(--line) bg-(--well)">
      <div className="flex shrink-0 flex-col justify-center px-3 py-2">
        <span className="tg-num text-[13px] font-bold leading-tight text-(--ink)">{carrier}</span>
        {flight.day ? <span className="tg-label">第 {flight.day} 天</span> : null}
      </div>
      <div aria-hidden className="relative w-0 self-stretch border-l border-dashed border-(--line-strong)">
        <span className="absolute -left-[6px] -top-[6px] h-3 w-3 rounded-full bg-(--card)" />
        <span className="absolute -bottom-[6px] -left-[6px] h-3 w-3 rounded-full bg-(--card)" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col justify-center px-3 py-2">
        <span className="truncate text-[13px] font-semibold leading-tight text-(--ink)">
          {flight.from_place}
          <span aria-hidden className="mx-1.5 text-(--accent)">
            →
          </span>
          {flight.to_place}
        </span>
        {flight.times ? <span className="tg-num tg-label">{flight.times}</span> : null}
      </div>
    </div>
  );
}

function FlightStrip({ label, flights }: { label: string; flights: RouteFlight[] }) {
  if (!flights.length) return null;
  return (
    <div className="mt-4">
      <div className="tg-label mb-1.5">{label}</div>
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {flights.map((flight, i) => (
          <FlightLeg key={`${flight.flight_no ?? "leg"}-${i}`} flight={flight} />
        ))}
      </div>
    </div>
  );
}

/** 含 is filled, 不含 is hollow, and a meal the document never wrote is dashed. */
function mealDot(meal?: RouteMeal): string {
  const included = meal?.included ?? null;
  if (included === true) return "bg-(--ok) border border-(--ok)";
  if (included === false) return "border border-(--ink-faint)";
  return "border border-dashed border-(--ink-faint)";
}

function mealTitle(meal?: RouteMeal): string {
  const included = meal?.included ?? null;
  const state = included === true ? "含" : included === false ? "不含" : "未写明";
  return meal?.text ? `${state} · ${meal.text}` : state;
}

function Meals({ meals }: { meals?: RouteDay["meals"] }) {
  return (
    <div className="flex items-center gap-3">
      {MEALS.map(([key, label]) => {
        const meal = meals?.[key];
        return (
          <span
            key={key}
            title={`${label}餐：${mealTitle(meal)}`}
            aria-label={`${label}餐：${mealTitle(meal)}`}
            className="flex items-center gap-1 text-[12px] text-(--ink-soft)"
          >
            <span aria-hidden className={`h-2.5 w-2.5 rounded-full ${mealDot(meal)}`} />
            {label}
          </span>
        );
      })}
    </div>
  );
}

/** A piece the document opens this way is an aside, not a step of the day. */
const NOTE_MARKERS = ["备注", "温馨提示", "特别提醒", "提醒", "注意", "★"];

/** A piece with nothing but punctuation and brackets in it says nothing. */
function bare(text: string): string {
  return text.replace(/[【】\s·—\-–~,，、.。;；:：!！?？()（）]/g, "");
}

/**
 * The day's prose as the 线路文档 writes it: paragraphs joined with " / " and lines joined with
 * "；". Each piece stands as its own paragraph, and a piece that only repeats the day's title or
 * a 参考航班 the card already shows is dropped rather than said twice.
 */
function textPieces(day: RouteDay): string[] {
  const shown = [day.title ?? "", (day.places ?? []).join("")].map(bare).filter(Boolean);
  const flightNumbers = (day.flights ?? [])
    .map((flight) => flight.flight_no)
    .filter((no): no is string => Boolean(no));
  const pieces: string[] = [];
  for (const paragraph of (day.text ?? "").split(" / ")) {
    for (const line of paragraph.split("；")) {
      const piece = line.trim();
      const stripped = bare(piece);
      if (!stripped) continue;
      if (shown.some((value) => value === stripped || value.includes(stripped))) continue;
      if (flightNumbers.some((no) => piece.includes(no)) && piece.length <= 40) continue;
      pieces.push(piece);
    }
  }
  return pieces;
}

/** The 【】 the document puts around a sight name, read as the emphasis it is. */
function Marked({ text }: { text: string }) {
  return (
    <>
      {text.split(/【([^】]+)】/).map((part, i) =>
        i % 2 === 1 ? (
          <span key={i} className="font-medium text-(--accent-ink)">
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function Prose({ day }: { day: RouteDay }) {
  const pieces = textPieces(day);
  if (!pieces.length) return null;
  return (
    <div className="mt-2 flex flex-col gap-1.5">
      {pieces.map((piece, i) =>
        NOTE_MARKERS.some((marker) => piece.startsWith(marker)) ? (
          <p
            key={i}
            className="border-l-2 border-(--line-strong) pl-2.5 text-[12.5px] leading-relaxed text-(--ink-faint)"
          >
            <Marked text={piece} />
          </p>
        ) : (
          <p key={i} className="text-[13px] leading-[1.75] text-(--ink-soft)">
            <Marked text={piece} />
          </p>
        ),
      )}
    </div>
  );
}

/** A stop as the document wrote it: what it is, how long it takes, and whether the ticket is in. */
function Sight({ sight }: { sight: RouteSight }) {
  const tone = SIGHT_TONE[sight.kind ?? ""] ?? "bg-(--well) text-(--ink-2)";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] ${tone}`}
    >
      <span className="font-semibold">{sight.name}</span>
      {sight.duration ? <span className="tg-num opacity-75">{sight.duration}</span> : null}
      {sight.ticket_included ? <span className="font-semibold opacity-85">含门票</span> : null}
    </span>
  );
}

/** Where the day sleeps: the hotel the document names, or what stands in for one. */
function Overnight({ day }: { day: RouteDay }) {
  const hotel = day.hotel;
  if (hotel?.name) {
    const parts = [hotel.name, hotel.grade, hotel.or_similar ? "或同级" : null].filter(Boolean);
    return (
      <div className="text-[12.5px] leading-snug text-(--ink-2)">
        <span className="tg-label mr-1">住宿</span>
        {parts.join(" · ")}
      </div>
    );
  }
  const text = OVERNIGHT_TEXT[day.overnight ?? ""];
  if (!text) return null;
  return (
    <div className="text-[12.5px] leading-snug text-(--ink-2)">
      <span className="tg-label mr-1">住宿</span>
      {text}
    </div>
  );
}

function DayRow({ day, last, delay }: { day: RouteDay; last: boolean; delay: number }) {
  const places = (day.places ?? []).join(" — ");
  const title = day.title || places;
  const sights = day.sights ?? [];
  const flights = day.flights ?? [];
  // 用餐, 交通 and where the day sleeps read as one row of facts under the stops.
  const facts = Boolean(day.meals || day.transport || day.hotel?.name || day.overnight);
  return (
    <li
      className="ac-reveal grid grid-cols-[46px_1fr] gap-x-3"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex flex-col items-center pt-0.5">
        <span aria-hidden className="tg-num text-[26px] font-bold leading-none text-(--ink)">
          {day.day}
        </span>
        <span className="tg-label leading-none">天</span>
        {!last ? <span aria-hidden className="mt-1.5 w-px flex-1 bg-(--line)" /> : null}
      </div>
      <div className={last ? "pt-0.5" : "pb-6 pt-0.5"}>
        <div className="text-[15px] font-semibold leading-snug text-(--ink)">
          <span className="sr-only">第 {day.day} 天 </span>
          {title}
        </div>
        {day.title && places && places !== day.title ? (
          <div className="mt-0.5 text-[12.5px] leading-snug text-(--ink-soft)">{places}</div>
        ) : null}
        {sights.length ? (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {sights.map((sight, i) => (
              <Sight key={`${sight.name}-${i}`} sight={sight} />
            ))}
          </div>
        ) : null}
        {facts ? (
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-(--radius) bg-(--well) px-2.5 py-1.5">
            <Meals meals={day.meals} />
            {day.transport ? (
              <span className="text-[12.5px] leading-snug text-(--ink-2)">
                <span className="tg-label mr-1">交通</span>
                {day.transport}
              </span>
            ) : null}
            <Overnight day={day} />
          </div>
        ) : null}
        {flights.length ? (
          <div className="tg-num mt-1.5 text-[12px] leading-snug text-(--ink-soft)">
            <span className="tg-label mr-1">航班</span>
            {flights.map((flight) => flightText(flight)).join("；")}
          </div>
        ) : null}
        <Prose day={day} />
      </div>
    </li>
  );
}

/** The day still being written, sized so the days above it do not move. */
function SkeletonDay() {
  return (
    <li aria-hidden className="grid grid-cols-[46px_1fr] gap-x-3">
      <div className="flex flex-col items-center pt-0.5">
        <span className="ac-skeleton h-6 w-6 rounded" />
      </div>
      <div className="flex flex-col gap-2 pt-1">
        <div className="ac-skeleton h-4 w-2/5 rounded" />
        <div className="ac-skeleton h-3 w-3/4 rounded" />
        <div className="ac-skeleton h-3 w-1/2 rounded" />
      </div>
    </li>
  );
}

/** One folded list under the days; the summary counts what is inside it. */
function Fold({ label, count, children }: { label: string; count?: number; children: ReactNode }) {
  return (
    <details className="group border-t border-dashed border-(--line) py-2 first:border-t-0">
      <summary className="cursor-pointer list-none text-[13.5px] font-semibold text-(--ink-2) [&::-webkit-details-marker]:hidden">
        <span
          aria-hidden
          className="mr-1.5 inline-block text-(--ink-faint) transition-transform group-open:rotate-90"
        >
          ▸
        </span>
        {label}
        {count ? <span className="tg-num tg-label ml-1.5">{count}</span> : null}
      </summary>
      <div className="mt-1.5 pl-4">{children}</div>
    </details>
  );
}

function Lines({ items }: { items: string[] }) {
  return (
    <ul className="flex flex-col gap-1 text-[13px] leading-relaxed text-(--ink-soft)">
      {items.map((item, i) => (
        <li key={`${item}-${i}`}>{item}</li>
      ))}
    </ul>
  );
}

export default function RouteDaysCard({
  payload,
  partial,
}: {
  payload: RouteDaysPayload;
  partial?: boolean;
}) {
  const days = payload.days ?? [];
  // The 天数 the document states; until it lands, the days that have arrived stand for it.
  const dayCount = payload.day_count ?? days.length;
  const headline = [
    dayCount ? `${dayCount} 天${payload.nights ? ` ${payload.nights} 晚` : ""}` : null,
    payload.depart_city ? `${payload.depart_city}出发` : null,
    (payload.countries ?? []).join("·") || null,
  ]
    .filter(Boolean)
    .join(" · ");
  const facts = [
    { label: "航空", value: payload.airline },
    { label: "酒店", value: payload.hotel_standard },
    { label: "用餐", value: payload.meal_standard },
  ]
    .filter((fact) => Boolean(fact.value))
    .map((fact) => ({ label: fact.label, value: fact.value as string }));
  const highlights = payload.highlights ?? [];
  const inclusions = payload.inclusions ?? [];
  const exclusions = payload.exclusions ?? [];
  const shopping = payload.shopping ?? [];
  const optional = payload.optional ?? [];
  const policies = payload.policies ?? {};
  const policyRows: [string, string | undefined][] = [
    ["单房差", policies.single_room],
    ["儿童", policies.child],
    ["签证", policies.visa],
    ["退改", policies.cancellation],
    ["定金", policies.deposit],
  ];
  const shownPolicies = policyRows.filter(([, value]) => Boolean(value));

  return (
    <section className="tg-card ac-reveal p-5">
      <div className="flex flex-col gap-3 sm:flex-row">
        {/* The poster where the record carries one; the card says nothing in its place. */}
        {payload.image_url ? (
          <Cover
            url={payload.image_url}
            alt={payload.title ?? payload.route_id}
            name={payload.countries?.[0] ?? "线路"}
            seed={payload.route_id}
            className="h-24 w-full shrink-0 sm:h-[86px] sm:w-32"
          />
        ) : null}
        <div className="min-w-0">
          <h3 className="text-[20px] font-semibold leading-snug tracking-[-0.015em] text-(--ink)">
            {payload.title ?? payload.route_id}
          </h3>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
            {payload.route_code ? (
              <span className="tg-num tg-label">{payload.route_code}</span>
            ) : null}
            {payload.department ? <span className="tg-label">{payload.department}</span> : null}
            {/* A line an editor has passed reads as the agency's own; one the parser wrote
                alone says so, because an unreviewed 行程 is not quoted to a customer. */}
            <span
              className={`rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${
                payload.reviewed ? "bg-(--ok-soft) text-(--ok)" : "bg-(--warn-soft) text-(--warn)"
              }`}
            >
              {payload.reviewed
                ? `已复核${payload.reviewed_by ? ` · ${payload.reviewed_by}` : ""}`
                : "解析稿，待复核"}
            </span>
          </div>
          {headline ? (
            <div className="mt-1 text-[13.5px] leading-snug text-(--ink-2)">{headline}</div>
          ) : null}
        </div>
      </div>

      {highlights.length ? (
        <div className="mt-3">
          <div className="tg-label mb-1">亮点</div>
          <ul className="flex flex-col gap-1">
            {highlights.slice(0, MAX_HIGHLIGHTS).map((line, i) => (
              <li
                key={`${line}-${i}`}
                className="flex gap-2 text-[13px] leading-relaxed text-(--ink-2)"
              >
                <span
                  aria-hidden
                  className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-(--accent)"
                />
                <span className="min-w-0">{line}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {facts.length ? (
        <dl className="mt-3 grid gap-x-4 gap-y-2 rounded-(--radius) bg-(--well) px-3 py-2.5 sm:grid-cols-3">
          {facts.map((fact) => (
            <div key={fact.label} className="min-w-0">
              <dt className="tg-label">{fact.label}</dt>
              <dd className="mt-0.5 text-[13px] leading-snug text-(--ink-2)">{fact.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <FlightStrip label="去程" flights={payload.outbound ?? []} />

      <ol className="mt-4">
        {days.map((day, i) => (
          <DayRow
            key={`${day.day}-${i}`}
            day={day}
            last={i === days.length - 1 && !partial}
            delay={i * 50}
          />
        ))}
        {partial ? <SkeletonDay /> : null}
      </ol>

      <FlightStrip label="回程" flights={payload.inbound ?? []} />

      {inclusions.length ||
      exclusions.length ||
      shopping.length ||
      optional.length ||
      shownPolicies.length ? (
        <div className="mt-4 border-t border-(--line) pt-2">
          {inclusions.length ? (
            <Fold label="费用包含" count={inclusions.length}>
              <Lines items={inclusions} />
            </Fold>
          ) : null}
          {exclusions.length ? (
            <Fold label="费用不含" count={exclusions.length}>
              <Lines items={exclusions} />
            </Fold>
          ) : null}
          {shopping.length ? (
            <Fold label="购物店" count={shopping.length}>
              <Lines
                items={shopping.map((stop) =>
                  [stop.day ? `第 ${stop.day} 天` : null, stop.name, stop.duration]
                    .filter(Boolean)
                    .join(" · "),
                )}
              />
            </Fold>
          ) : null}
          {optional.length ? (
            <Fold label="自费项目" count={optional.length}>
              <Lines
                items={optional.map((item) =>
                  [
                    item.day ? `第 ${item.day} 天` : null,
                    item.name,
                    item.price ? item.price : null,
                  ]
                    .filter(Boolean)
                    .join(" · "),
                )}
              />
            </Fold>
          ) : null}
          {shownPolicies.length ? (
            <Fold label="须知">
              <dl className="flex flex-col gap-1 text-[13px] leading-relaxed text-(--ink-soft)">
                {shownPolicies.map(([label, value]) => (
                  <div key={label} className="flex gap-2">
                    <dt className="tg-label shrink-0">{label}</dt>
                    <dd className="min-w-0">{value}</dd>
                  </div>
                ))}
              </dl>
            </Fold>
          ) : null}
        </div>
      ) : null}

      {payload.attachment_name ? (
        <p className="tg-label mt-2 truncate" title={payload.attachment_name}>
          行程单 · {payload.attachment_name}
        </p>
      ) : null}
    </section>
  );
}
