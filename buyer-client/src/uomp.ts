/**
 * UOMP (User-Owned Memory Protocol) context layer.
 *
 * Connects to a running UOMP Memory Guard over HTTP.
 * The Guard runs locally on the buyer's machine and exposes the user's
 * portfolio context (holdings, risk profile) via its REST API.
 *
 * Required env vars:
 *   UOMP_GUARD_URL   — Guard base URL (default: http://127.0.0.1:9374)
 *   UOMP_GUARD_TOKEN — JWT bearer token issued by the Guard
 *
 * Guard endpoints used:
 *   GET /v1/memory?tag={tag}  → MemoryItem[]
 *   GET /v1/memory/{key}      → MemoryItem
 */

export interface MemoryItem<T = unknown> {
  key: string;
  value: T;
  tags: string[];
  sensitivity: "low" | "medium" | "high";
  source: "user" | "agent";
  createdAt: string;
  updatedAt: string;
  description?: string;
}

export interface Holding {
  symbol: string;
  shares: number;
  avgCost: number;
  currency: string;
}

export interface RiskProfile {
  tolerance: "conservative" | "moderate" | "aggressive";
  horizonMonths: number;
  preferredIndicators: string[];
}

/** Interface the Guard client satisfies — matches the @uomp/sdk UserMemory shape. */
export interface UserMemory {
  get<T>(key: string): Promise<MemoryItem<T> | null>;
  getByTag<T>(tag: string): Promise<MemoryItem<T>[]>;
}

/** UOMP Guard HTTP client. Connects to a locally-running Memory Guard. */
export class GuardUserMemory implements UserMemory {
  private baseUrl: string;
  private headers: Record<string, string>;

  constructor(baseUrl?: string, token?: string) {
    this.baseUrl = (baseUrl ?? process.env["UOMP_GUARD_URL"] ?? "http://127.0.0.1:9374").replace(/\/$/, "");
    const tok = token ?? process.env["UOMP_GUARD_TOKEN"] ?? "";
    this.headers = tok
      ? { Authorization: `Bearer ${tok}`, Accept: "application/json" }
      : { Accept: "application/json" };
  }

  async get<T>(key: string): Promise<MemoryItem<T> | null> {
    const res = await fetch(`${this.baseUrl}/v1/memory/${encodeURIComponent(key)}`, {
      headers: this.headers,
    });
    if (res.status === 404) return null;
    if (!res.ok) {
      throw new Error(`UOMP Guard GET /v1/memory/${key} → HTTP ${res.status}: ${await res.text()}`);
    }
    return await res.json() as MemoryItem<T>;
  }

  async getByTag<T>(tag: string): Promise<MemoryItem<T>[]> {
    const res = await fetch(`${this.baseUrl}/v1/memory?tag=${encodeURIComponent(tag)}`, {
      headers: this.headers,
    });
    if (!res.ok) {
      throw new Error(`UOMP Guard GET /v1/memory?tag=${tag} → HTTP ${res.status}: ${await res.text()}`);
    }
    const body = await res.json() as { items?: MemoryItem<T>[] } | MemoryItem<T>[];
    return Array.isArray(body) ? body : (body.items ?? []);
  }
}

/** Build the task description from UOMP portfolio context fetched from the Guard. */
export async function buildTaskFromMemory(memory: UserMemory): Promise<{
  symbols: string[];
  task: string;
  deliverables: string;
  quality: string;
  portfolio: Holding[];
  riskProfile: RiskProfile;
}> {
  const holdings = await memory.getByTag<Holding>("portfolio:holdings");
  const [riskItem] = await memory.getByTag<RiskProfile>("profile:risk");

  if (holdings.length === 0) {
    throw new Error(
      "No portfolio:holdings found in UOMP Guard. " +
      "Seed your memory store before running the buyer client.\n" +
      `  Guard: ${process.env["UOMP_GUARD_URL"] ?? "http://127.0.0.1:9374"}`
    );
  }

  const portfolio = holdings.map((h) => h.value);
  const symbols = portfolio.map((h) => h.symbol);
  const riskProfile: RiskProfile = riskItem?.value ?? {
    tolerance: "moderate" as const,
    horizonMonths: 12,
    preferredIndicators: ["RSI-14", "MACD", "Bollinger Bands", "MA50/200", "ADX"],
  };
  const indicators = riskProfile.preferredIndicators.join(", ");

  const holdingSummary = portfolio
    .map((h) => `${h.symbol} (${h.shares}sh @ ${h.currency}${h.avgCost})`)
    .join(", ");

  const task =
    `Comprehensive stock analysis for ${symbols.join(", ")} ` +
    `(${riskProfile.tolerance} risk, ${riskProfile.horizonMonths}mo horizon). ` +
    `Holdings: ${holdingSummary}`;
  const deliverables =
    `Markdown report: fundamentals, ${indicators}, options sentiment, ` +
    `insider activity, macro context, P&L vs avg cost, buy/hold/sell recommendation with target`;
  const quality =
    `Real market data: yfinance, SEC EDGAR, FRED macro, Alpha Vantage sentiment, NewsAPI. ` +
    `All technical indicators computed (${indicators}). Portfolio P&L personalised.`;

  return { symbols, task, deliverables, quality, portfolio, riskProfile };
}
