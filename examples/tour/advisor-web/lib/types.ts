// Shapes returned by the advisor service (examples/tour/advisor/api.py).

export type Chip = { label: string; action: string; primary?: boolean };

export type NeedField = {
  field: string;
  label: string;
  value: unknown;
  text: string;
  source: "said" | "inferred" | "explore" | "advisor" | null;
  evidence: string;
  hint: string;
};

export type Gates = {
  search: { ready: boolean; missing: string[] };
  quote: { ready: boolean; missing: string[] };
  later: string[];
  inferred: string[];
};

export type ChangeItem = {
  field: string;
  old: unknown;
  new: unknown;
  source: string;
  evidence: string;
  hint: string;
  status: "pending" | "adopted" | "kept" | "stale";
};

export type Impact = { level: "bad" | "warn" | "ok"; title: string; text: string };

export type RouteCard = {
  product_id: string;
  title: string;
  days: number | null;
  depart_city: string;
  three: { hotel: string; meals: string; shopping: string };
  yes: string[];
  no: string[];
  unknown: string[];
  score: number;
  price: { per_person: number; total: string; date?: string } | null;
  alternative: boolean;
  notice: string;
};

export type Search = {
  id?: string;
  query: string;
  must: string[];
  nice: string[];
  steps: { label: string; count: number | null; note?: string; over?: { title: string; by: number }[] }[];
  cards: RouteCard[];
  relax: { field: string; text: string; count: number }[];
  explain: string;
  alternatives_only: boolean;
};

export type Answer = {
  question: string;
  topic: string;
  product_id: string | null;
  route: string;
  answer: string;
  kind: "fact" | "advice" | "unknown";
  facts: { fact_id: string; section: string; text: string }[];
};

export type Card =
  | { type: "read"; items: ChangeItem[]; version: number }
  | { type: "change"; proposal_id: string; from: number; items: ChangeItem[]; impact: Impact[] }
  | ({ type: "routes"; search_id: string } & Search)
  | { type: "answers"; items: Answer[] }
  | { type: "clarity"; clarity: number; known: string[]; unknown: string[] }
  | { type: "directions"; items: { name: string; label: string; days: string; count: number; example: string; region: boolean; start: string; end: string }[] }
  | { type: "chosen"; title: string; product_id: string }
  | { type: "confirm_reply"; status: string; disputes: string[] };

export type TurnResult = {
  seq: number;
  kind: string;
  text: string;
  tags?: string[];
  cards: Card[];
  chips: Chip[];
  notes: string[];
  degraded?: string;
  gates?: Gates;
  clarity?: number;
  asked?: string;
  draft?: {
    text: string;
    removed: string[];
    reasons?: string[];
    claims: { text: string; fact_id: string; section?: string }[];
    simplified: boolean;
  };
  to_advisor?: string;
  may_ask?: string[];
  sent?: boolean;
};

export type Turn = { seq: number; kind: string; text: string; result: TurnResult; at: string };

export type Proposal = { id: string; from: number; items: ChangeItem[]; impact: Impact[]; turn: number | null };

export type QuoteView = {
  id: string;
  kind: string;
  status: string;
  valid: boolean;
  reason: string;
  departure_id: string;
  date: string | null;
  lines: { label: string; quantity: number; unit: string | null; total: string | null }[];
  extras: { text: string; amount: string | null; currency: string; quantity: number }[];
  market_total: string | null;
  settlement_total: string | null;
  sales_total: string | null;
  profit: string | null;
  margin: string | null;
  per_person: number | null;
  currency: string;
  complete: boolean;
  missing: string[];
  valid_until: string | null;
  fresh_until: string | null;
  created_at: string | null;
};

export type Confirmation = {
  id: string;
  status: string;
  need_version: number;
  items: { key: string; label: string; text: string; ok: boolean }[];
  missing: string[];
  evidence: string;
  current: boolean;
  draft: string;
};

export type Deal = {
  id: string;
  title: string;
  status: string;
  stage: number;
  version: number;
  need: { fields: NeedField[]; beds: string; summary: string };
  gates: Gates;
  clarity: number;
  route: { product_id: string; title: string } | null;
  departure: { departure_id: string; offer_id?: string | null; date: string; return_date?: string } | null;
  turns: Turn[];
  pending: Proposal[];
  proposals: Proposal[];
  search: Search | null;
  plans: Plan[];
  quote: QuoteView | null;
  confirmation: Confirmation | null;
  chips: Chip[];
};

export type Plan = {
  id: string;
  version: number;
  need_version: number;
  status: "draft" | "sent" | "void";
  body: {
    need: string;
    routes: {
      product_id: string;
      title: string;
      days: string | null;
      reasons: { concern: string; text: string; source: string }[];
      tell: { concern: string; text: string; source: string }[];
      price: QuoteView | null;
      includes: string;
      dates: string[];
    }[];
  };
  sent_at: string | null;
  views: number;
  signals: { signal: string; route: string; at: string }[];
};

export type DealCard = {
  id: string;
  title: string;
  line: string;
  stage: number;
  status: string;
  tag: { text: string; tone: string } | null;
  next: string;
  needs_me: boolean;
  updated_at: string;
};

export const STAGES = ["需求", "选线", "定团", "报价", "成交", "出行"];
