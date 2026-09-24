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
  jobs_mode: "local" | "queue" | "off";
  admin_required: boolean;
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
  brands?: string[];
  exclude_brands?: string[];
  official_only: boolean;
  in_stock_only: boolean;
  top?: number;
}

export interface Parsed {
  category: string | null;
  budget_min: number | null;
  budget_max: number | null;
  uses: string[];
  os: string[];
  brands: string[];
  exclude_brands: string[];
  must: Must[];
  understood: string[];
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
  check?: "OK" | "PARTIAL" | "FAIL" | "RUNNING";
  scrape_running?: boolean;
  check_detail?: string;
  checked_at?: string;
  last_scrape_count?: number;
  last_scraped_at?: string;
  last_rejected?: number;
}

export interface ScraperInfo {
  name: string;
  available: boolean;
  browser: boolean;
  missing: string | null;
}

export interface Quality {
  raw_records: number;
  by_kind: Record<string, number>;
  top: { source: string; kind: string; field: string; n: number; example: string }[];
}

export interface DealItem {
  key: string;
  name: string;
  brand: string | null;
  category: string;
  image: string | null;
  rating: number | null;
  specs: Specs;
  seller: string;
  url: string;
  variant: string | null;
  official: boolean | null;
  in_stock: boolean | null;
  price: number;
  original_price: number | null;
  valid_until: string | null;
  claimed_pct: number | null;
  market_price: number | null;
  saving: number | null;
  saving_pct: number | null;
  lowest_seen: number | null;
  dropped_from: number | null;
  verdicts: string[];
  verified: boolean;
}

export interface Job {
  id: string;
  kind: "check" | "scrape";
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  origin: "ui" | "schedule" | "cli" | null;
  names: string[];
  progress: { done?: number; total?: number; current?: string | null };
  log: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const ADMIN_KEY = "devicescout.adminKey";

export function adminKey(): string {
  try {
    return localStorage.getItem(ADMIN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setAdminKey(key: string): void {
  try {
    if (key) localStorage.setItem(ADMIN_KEY, key);
    else localStorage.removeItem(ADMIN_KEY);
  } catch {
    /* private mode: key lasts for this page only */
  }
}

function adminHeaders(): Record<string, string> {
  const k = adminKey();
  return k ? { "X-Admin-Key": k } : {};
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
  parse: (q: string) => request<Parsed>("/api/parse", { method: "POST", body: JSON.stringify({ q }) }),
  advise: (needs: NeedsRequest) => request<Advice>("/api/advise", { method: "POST", body: JSON.stringify(needs) }),
  products: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
    return request<{ total: number; items: Summary[] }>(`/api/products?${q}`);
  },
  product: (key: string) => request<ProductDetail>(`/api/products/${encodeURIComponent(key)}`),
  sources: () => request<{ sources: SourceRow[]; file: string; scrapers: ScraperInfo[]; error?: string }>("/api/sources"),
  quality: () => request<Quality>("/api/quality"),
  deals: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
    return request<{ total: number; items: DealItem[] }>(`/api/deals?${q}`);
  },
  startJob: (kind: "check" | "scrape", names: string[] = [], limit?: number) =>
    request<Job>("/api/jobs", { method: "POST", headers: adminHeaders(), body: JSON.stringify({ kind, names, limit }) }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  jobs: () => request<{ jobs: Job[]; worker_seen_at: string | null; jobs_mode: string }>("/api/jobs"),
  cancelJob: () => request<{ ok: boolean }>("/api/jobs/cancel", { method: "POST", headers: adminHeaders() }),
  addSource: (url: string, role: string) =>
    request<{ source: SourceRow; job: Job | null }>("/api/sources", {
      method: "POST", headers: adminHeaders(), body: JSON.stringify({ url, role }),
    }),
  setSourceEnabled: (name: string, enabled: boolean) =>
    request<SourceRow>(`/api/sources/${encodeURIComponent(name)}`, {
      method: "PATCH", headers: adminHeaders(), body: JSON.stringify({ enabled }),
    }),
  removeSource: (name: string) =>
    request<{ ok: boolean }>(`/api/sources/${encodeURIComponent(name)}`, { method: "DELETE", headers: adminHeaders() }),
  verifyAdmin: (key: string) =>
    request<{ ok: boolean }>("/api/admin/verify", { method: "POST", headers: { "X-Admin-Key": key } }),
};
