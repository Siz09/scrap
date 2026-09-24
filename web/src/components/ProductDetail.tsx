import { useEffect, useState } from "react";
import { api, type ProductDetail as Detail } from "../api";
import { useApp } from "../context";
import { formatSpec, npr, relTime } from "../format";

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

  const groups = new Map<string, string[]>();
  for (const k of Object.keys(p.specs)) {
    const g = meta.specs[k]?.group ?? "Other";
    groups.set(g, [...(groups.get(g) ?? []), k]);
  }
  const local = p.offers.filter((o) => o.region !== "intl");
  const intl = p.offers.filter((o) => o.region === "intl" && o.price != null);
  const inCompare = compare.includes(p.key);

  return (
    <div className="detail">
      <a href="#/browse" className="back" onClick={(e) => { if (history.length > 1) { e.preventDefault(); history.back(); } }}>← Back</a>
      <header className="detail-head">
        <div>
          <p className="muted">{p.brand} · {meta.categories.find((c) => c.id === p.category)?.label}</p>
          <h1>{p.name}</h1>
          <p className="detail-price">
            <strong>{npr(p.best_price)}</strong>
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
            International: {intl.map((o) => `${o.seller} ${o.currency} ${o.price?.toLocaleString()} (${npr(o.price_npr)})`).join(" · ")}
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
              <h3>{g}</h3>
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
        <polyline points={xy.map((p) => p.join(",")).join(" ")} fill="none" className="history-line" />
        {xy.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={3.5} className="history-dot"><title>{points[i][0]}: {npr(points[i][1])}</title></circle>)}
      </svg>
    </figure>
  );
}
