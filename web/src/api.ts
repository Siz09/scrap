// Typed client for the DeviceScout API (devicescout/server.py).

export type SpecValue = string | number | boolean | null;
export type Specs = Record<string, SpecValue>;

export interface UseInfo {
  id: string;
  label: string;
  description: string;
}

export interface MustInfo {
  key: string;
  label: string;
  type: "bool" | "min" | "max";
  unit?: string;
  options?: number[];
  value?: number;
  categories: string[];
}

export interface CategoryMeta {
  id: string;
  label: string;
  uses: UseInfo[];
  musts: MustInfo[];
  os: string[];
  budgets: number[];
}

export interface SpecMeta {
  label: string;
  unit: string;
  group: string;
}

export interface Stats {
  products: number;
  by_category: Record<string, number>;
  offers: number;
  last_scraped: string | null;
  sources: string[];
}

export interface Meta {
  categories: CategoryMeta[];
  specs: Record<string, SpecMeta>;
  stats: Stats;
  version: string;
  read_only: boolean;
  sample: boolean;
}

export interface Summary {
  key: string;
  name: string;
  brand: string | null;
  category: string;
  image: string | null;
  rating: number | null;
  review_count: number | null;
  best_price: number | null;
  reference_price: number | null;
  best_seller: string | null;
  best_official: boolean | null;
  offer_count: number;
  specs: Specs;
  sources: string[];
}

export interface WhereToBuy {
  seller: string;
  price_npr: number;
  variant: string | null;
  official: boolean | null;
  in_stock: boolean | null;
  listed_price_only: boolean;
  url: string;
}

export interface Pick extends Summary {
  score: number;
  confidence: number;
  price_npr: number;
  strengths: string[];
  weaknesses: string[];
  warnings: string[];
  where_to_buy: WhereToBuy[];
}

export interface Advice {
  picks: Pick[];
  value_pick: Pick | null;
  stretch_pick: Pick | null;
  considered: number;
  excluded: Record<string, number>;
}

export interface Must {
  key: string;
  op: ">=" | "<=" | "==";
  value: number | boolean;
}

export interface NeedsRequest {
  category: string;
  budget_min?: number | null;
  budget_max?: number | null;
  uses: Record<string, number>;
  os: string[];
  must: Must[];
  official_only: boolean;
  in_stock_only: boolean;
  top?: number;
}

export interface Offer {
  seller: string;
  source: string;
  url: string;
  price: number | null;
  currency: string | null;
  price_npr: number | null;
  variant: string | null;
  official: boolean | null;
  in_stock: boolean | null;
  region: string;
  suspicious: boolean;
  original_price: number | null;
  scraped_at: string | null;
}

export interface ProductDetail extends Summary {
  offers: Offer[];
  history: { source: string; price: number; currency: string; scraped_at: string }[];
  spec_sources: Record<string, string>;
  gtin: string | null;
  updated_at: string | null;
}

export interface SourceRow {
  name: string;
  enabled: boolean;
  role: string | null;
  region: string | null;
  type: string;
  platform: string | null;
  verified: boolean;
  notes: string | null;
  url: string | null;
  check?: "OK" | "FAIL";
  check_detail?: string;
  checked_at?: string;
  last_scrape_count?: number;
  last_scraped_at?: string;
}

export interface Job {
  id: string;
  kind: "check" | "scrape";
  status: "running" | "done" | "failed" | "cancelled";
  log: string[];
  started_at: string;
  finished_at: string | null;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  meta: () => request<Meta>("/api/meta"),
  advise: (needs: NeedsRequest) => request<Advice>("/api/advise", { method: "POST", body: JSON.stringify(needs) }),
  products: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
    return request<{ total: number; items: Summary[] }>(`/api/products?${q}`);
  },
  product: (key: string) => request<ProductDetail>(`/api/products/${encodeURIComponent(key)}`),
  sources: () => request<{ sources: SourceRow[]; file: string }>("/api/sources"),
  startJob: (kind: "check" | "scrape", names: string[] = [], limit?: number) =>
    request<Job>("/api/jobs", { method: "POST", body: JSON.stringify({ kind, names, limit }) }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  jobs: () => request<{ jobs: Job[] }>("/api/jobs"),
  cancelJob: () => request<{ ok: boolean }>("/api/jobs/cancel", { method: "POST" }),
};
