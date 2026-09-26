import { useEffect, useState } from "react";
import { api, type ProductDetail as Detail, type SpecMeta, type Specs } from "../api";
import { useApp } from "../context";
import { formatSpec, npr, relTime } from "../format";
import PriceTag, { money } from "./PriceTag";
import DeviceImage from "./DeviceImage";

const SPEC_GROUP_ORDER = ["Display", "Platform", "Memory", "Camera", "Battery", "Build", "Connectivity", "Reviews", "Other"];

// One authored icon per spec group, single stroke, no fill -- a visual anchor for scanning a
// long spec sheet, not decoration standing in for the data.
const GROUP_ICONS: Record<string, string> = {
  Display: "M5 4h14a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM9 20h6",
  Platform: "M8 3v3M16 3v3M8 18v3M16 18v3M3 8h3M3 16h3M18 8h3M18 16h3M7 7h10v10H7z",
  Memory: "M4 5.5C4 6.9 7.6 8 12 8s8-1.1 8-2.5S16.4 3 12 3 4 4.1 4 5.5zM4 5.5V12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5V5.5M4 12v6.5c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5V12",
  Camera: "M8 7l1.2-2h5.6L16 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  Battery: "M3 9a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9zM21 11v2M13 8v8",
  Build: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM4.5 7.5L12 12l7.5-4.5M12 12v9",
  Connectivity: "M3 8.5a15 15 0 0 1 18 0M6 12a10.5 10.5 0 0 1 12 0M9.3 15.5a5.5 5.5 0 0 1 5.4 0M12 19h.01",
  Reviews: "M12 3l2.6 5.9 6.4.6-4.8 4.4 1.4 6.3L12 17l-5.6 3.2 1.4-6.3-4.8-4.4 6.4-.6L12 3z",
  Other: "M20 12.5l-7.3 7.3a1 1 0 0 1-1.4 0l-7.3-7.3a1 1 0 0 1-.3-.7V5a1 1 0 0 1 1-1h6.8a1 1 0 0 1 .7.3l7.8 7.8a1 1 0 0 1 0 1.4zM7.5 7.5h.01",
};

function GroupIcon({ group }: { group: string }) {
  const d = GROUP_ICONS[group] ?? GROUP_ICONS.Other;
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

// gadgetbyte's own review rubric, in the order it publishes them: a labelled bar per category,
// not a flat spec row -- this is a per-source review breakdown, not a device spec.
const REVIEW_BREAKDOWN: [string, string][] = [
  ["review_design", "Design and build"], ["review_display", "Display"],
  ["review_performance", "Performance"], ["review_software", "Software experience"],
  ["review_cameras", "Cameras"], ["review_battery", "Battery life"],
  ["review_value", "Value for money"],
];

function scoreTier(outOf10: number): string {
  if (outOf10 >= 8) return "good";
  if (outOf10 >= 5) return "";
  return "low";
}

function ExpertScoreBreakdown({ specs, sources }: { specs: Specs; sources: string[] }) {
  const rows = REVIEW_BREAKDOWN
    .map(([key, label]) => [label, specs[key]] as const)
    .filter((r): r is [string, number] => typeof r[1] === "number");
  if (rows.length === 0) return null;
  const overall = typeof specs.expert_score === "number" ? specs.expert_score : null;

  return (
    <section className="panel score-breakdown">
      <h2>Expert Score Breakdown</h2>
      {overall != null && (
        <p className="score-breakdown-overall">
          <strong>{overall}</strong><span className="muted">/100 overall</span>
        </p>
      )}
      <ul className="score-bars">
        {rows.map(([label, score]) => (
          <li key={label} className={`score-bar ${scoreTier(score)}`}>
            <span className="score-bar-label">{label}</span>
            <span className="score-bar-track" role="progressbar" aria-label={label}
                  aria-valuenow={score} aria-valuemin={0} aria-valuemax={10}>
              <span className="score-bar-fill" style={{ transform: `scaleX(${score / 10})` }} />
            </span>
            <span className="score-bar-value">{score}<span className="muted">/10</span></span>
          </li>
        ))}
      </ul>
      <p className="muted small">Reviewed by: {sources.join(", ")}.</p>
    </section>
  );
}

// The handful of specs worth reading at a glance, before the full sheet -- what GSMArena-style
// pages put in their hero strip. Skipped entirely below if a device has too few of these.
const QUICK_FACTS: Record<string, string[]> = {
  phone: ["display_size_in", "main_camera_mp", "ram_gb", "battery_mah"],
  tablet: ["display_size_in", "main_camera_mp", "ram_gb", "battery_mah"],
  laptop: ["display_size_in", "ram_gb", "storage_gb", "battery_wh"],
  smartwatch: ["battery_mah", "water_resistance", "has_gps", "weight_g"],
  earbuds: ["battery_mah", "weight_g"],
  power_bank: ["capacity_mah", "output_w", "weight_g"],
};

function QuickFacts({ category, specs, specMeta }: {
  category: string; specs: Specs; specMeta: Record<string, SpecMeta>;
}) {
  const keys = (QUICK_FACTS[category] ?? []).filter((k) => specs[k] != null);
  if (keys.length < 2) return null;
  return (
    <div className="quick-facts">
      {keys.map((k) => (
        <div key={k} className="quick-fact">
          <GroupIcon group={specMeta[k]?.group ?? "Other"} />
          <div>
            <strong>{formatSpec(k, specs[k], specMeta[k])}</strong>
            <span className="muted small">{specMeta[k]?.label ?? k}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function ProductDetail({ productKey }: { productKey: string }) {
  const { meta, compare, toggleCompare } = useApp();
  const [p, setP] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setP(null);
    api.product(productKey).then(setP).catch((e) => setError(e.message));
  }, [productKey]);

  if (error) return <div className="empty"><h2>Not found</h2><p>{error}</p></div>;
  if (!p) return <div className="loading">Loading…</div>;

  // One list, top to bottom, in a fixed order: the sections, and the specs inside each, in the
  // order the server lists them (a spec sheet reads the same on every device). The expert-review
  // breakdown gets its own section below instead of sitting flat in this sheet.
  const isReviewBreakdown = (k: string) => k === "expert_score" || k.startsWith("review_");
  const known = Object.keys(meta.specs);
  const keys = [...known.filter((k) => k in p.specs), ...Object.keys(p.specs).filter((k) => !(k in meta.specs))]
    .filter((k) => !isReviewBreakdown(k));
  const byGroup = new Map<string, string[]>();
  for (const k of keys) {
    const g = meta.specs[k]?.group ?? "Other";
    byGroup.set(g, [...(byGroup.get(g) ?? []), k]);
  }
  const rank = (g: string) => { const i = SPEC_GROUP_ORDER.indexOf(g); return i < 0 ? SPEC_GROUP_ORDER.length : i; };
  const groups = new Map([...byGroup].sort(([a], [b]) => rank(a) - rank(b)));
  const local = p.offers.filter((o) => o.region !== "intl");
  const intl = p.offers.filter((o) => o.region === "intl" && o.price != null);
  const inCompare = compare.includes(p.key);

  return (
    <div className="detail">
      <a href="#/browse" className="back" onClick={(e) => { if (history.length > 1) { e.preventDefault(); history.back(); } }}>← Back</a>
      <header className="detail-head">
        <DeviceImage productKey={p.key} name={p.name} size="lg" />
        <div className="detail-info">
          <p className="muted">{p.brand} · {meta.categories.find((c) => c.id === p.category)?.label}</p>
          <h1>{p.name}</h1>
          <p className="detail-price">
            <PriceTag local={p.best_price} converted={p.converted_price} from={p.converted_from}
                      available={p.available_in_nepal} />
            {p.best_seller && <span className="muted"> lowest trusted price, at {p.best_seller}</span>}
          </p>
          {p.reference_price != null && (
            <p className="muted small">International reference: {npr(p.reference_price)} (before import costs)</p>
          )}
          {p.rating != null && <p className="muted small">★ {p.rating.toFixed(1)} from {p.review_count ?? "?"} buyer reviews</p>}
        </div>
        <button type="button" className={`btn ${inCompare ? "on" : ""}`} onClick={() => toggleCompare(p.key)}>
          {inCompare ? "✓ In compare" : "+ Compare"}
        </button>
      </header>

      <QuickFacts category={p.category} specs={p.specs} specMeta={meta.specs} />
      <ExpertScoreBreakdown specs={p.specs} sources={p.sources} />

      <section className="panel">
        <h2>Prices in Nepal</h2>
        {local.length === 0 ? <p className="muted">No Nepali prices yet.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Seller</th><th>Variant</th><th className="num">Price</th><th>Status</th><th>Seen</th></tr></thead>
              <tbody>
                {local.map((o) => (
                  <tr key={o.url + (o.variant ?? "")} className={o.suspicious ? "suspicious" : ""}>
                    <td><a href={o.url} target="_blank" rel="noopener noreferrer">{o.seller}</a></td>
                    <td>{o.variant ?? "—"}</td>
                    <td className="num">
                      {npr(o.price_npr)}
                      {o.original_price && o.price && o.original_price > o.price && (
                        <s className="muted small"> {npr(o.original_price)}</s>
                      )}
                    </td>
                    <td>
                      {o.suspicious && <span className="badge low" title="Far below other sellers or a clone name: likely fake, used or mislisted">Ignored: implausible</span>}
                      {o.official && <span className="badge good">Official</span>}
                      {o.in_stock === false && <span className="badge low">Out of stock</span>}
                      {o.region === "np-ref" && <span className="badge">Listed price</span>}
                    </td>
                    <td className="muted small">{relTime(o.scraped_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {intl.length > 0 && (
          <p className="muted small">
            Prices abroad (converted at today's rate, before import duty, VAT and shipping):{" "}
            {intl.map((o) => `${o.seller}: ${money(o.price ?? 0, o.currency ?? "USD")} ≈ ${npr(o.price_npr)}`).join(" · ")}
          </p>
        )}
        <PriceHistory history={p.history} />
      </section>

      <section className="panel">
        <h2>Specifications</h2>
        {groups.size === 0 && <p className="muted">No specs yet. Scrape GSMArena or a spec-rich store to fill these in.</p>}
        <div className="spec-groups">
          {[...groups.entries()].map(([g, keys]) => (
            <div key={g} className="spec-group">
              <h3><GroupIcon group={g} />{g}</h3>
              <dl>
                {keys.map((k) => (
                  <div key={k} className="spec-row">
                    <dt>{meta.specs[k]?.label ?? k}</dt>
                    <dd title={p.spec_sources[k] ? `from ${p.spec_sources[k]}` : undefined}>{formatSpec(k, p.specs[k], meta.specs[k])}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
        <p className="muted small">Sources: {p.sources.join(", ")}{p.gtin ? ` · barcode ${p.gtin}` : ""}. Hover a value to see where it came from.</p>
      </section>
    </div>
  );
}

function PriceHistory({ history }: { history: Detail["history"] }) {
  // Lowest price seen per day across all Nepali sellers.
  const byDay = new Map<string, number>();
  for (const h of history) {
    if (h.price == null || (h.currency && h.currency !== "NPR")) continue;
    const day = h.scraped_at.slice(0, 10);
    byDay.set(day, Math.min(byDay.get(day) ?? Infinity, h.price));
  }
  const points = [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b));
  if (points.length < 2) return null;
  const vals = points.map(([, v]) => v);
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const W = 600, H = 120, pad = 8;
  const xy = points.map(([, v], i) => [pad + (i * (W - 2 * pad)) / (points.length - 1), H - pad - ((v - lo) / span) * (H - 2 * pad)]);
  return (
    <figure className="history">
      <figcaption className="muted small">Lowest price over time: {npr(vals[0])} → {npr(vals[vals.length - 1])}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Price history">
        <polygon points={`${pad},${H - pad} ${xy.map((p) => p.join(",")).join(" ")} ${W - pad},${H - pad}`} className="history-area" />
        <polyline points={xy.map((p) => p.join(",")).join(" ")} className="history-line" />
        {xy.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={3.5} className="history-dot"><title>{points[i][0]}: {npr(points[i][1])}</title></circle>)}
      </svg>
    </figure>
  );
}
