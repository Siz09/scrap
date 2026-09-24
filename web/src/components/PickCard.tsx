import type { Pick } from "../api";
import { useApp } from "../context";
import { formatSpec, keySpecs, npr } from "../format";

function confidenceLabel(c: number): [string, string] {
  if (c >= 0.75) return ["High", "good"];
  if (c >= 0.45) return ["Medium", "ok"];
  return ["Low", "low"];
}

export default function PickCard({ pick, rank, tone, savings }: {
  pick: Pick;
  rank?: number;
  tone?: "value" | "stretch";
  savings?: number;
}) {
  const { meta, compare, toggleCompare } = useApp();
  const [conf, confClass] = confidenceLabel(pick.confidence);
  const inCompare = compare.includes(pick.key);
  const specs = keySpecs(pick.category, pick.specs);
  const score = Math.max(0, Math.min(100, Math.round(pick.score)));

  return (
    <article className={`card pick ${tone ?? ""}`}>
      <div className="pick-head">
        {rank && <span className="pick-rank" aria-label={`Rank ${rank}`}>{rank}</span>}
        <div className="pick-title">
          <h3><a href={`#/product/${encodeURIComponent(pick.key)}`}>{pick.name}</a></h3>
          <div className="pick-price">
            <strong>{npr(pick.price_npr)}</strong>
            {pick.best_seller && <span className="muted"> at {pick.best_seller}</span>}
            {pick.best_official && <span className="badge good">Official</span>}
            {savings != null && savings > 0 && <span className="badge good">Save {npr(savings)}</span>}
          </div>
        </div>
        <div className="score" title="Fit for your needs, relative to the other devices compared">
          <svg viewBox="0 0 36 36" aria-hidden="true">
            <circle cx="18" cy="18" r="15.9" className="score-bg" />
            <circle cx="18" cy="18" r="15.9" className="score-fg" strokeDasharray={`${score} 100`} />
          </svg>
          <span className="score-num">{score}</span>
          <span className={`conf ${confClass}`} title={`${Math.round(pick.confidence * 100)}% of the score is backed by data`}>
            {conf} confidence
          </span>
        </div>
      </div>

      {specs.length > 0 && (
        <ul className="specline">
          {specs.map((k) => (
            <li key={k}><span className="muted">{meta.specs[k]?.label ?? k}</span> {formatSpec(k, pick.specs[k], meta.specs[k])}</li>
          ))}
        </ul>
      )}

      <div className="reasons">
        {pick.strengths.map((s) => <p key={s} className="plus">{s}</p>)}
        {pick.weaknesses.map((s) => <p key={s} className="minus">{s}</p>)}
        {pick.warnings.map((s) => <p key={s} className="warn">{s}</p>)}
      </div>

      {pick.where_to_buy.length > 0 && (
        <details className="buy">
          <summary>Where to buy ({pick.where_to_buy.length})</summary>
          <ul>
            {pick.where_to_buy.map((o) => (
              <li key={o.url + (o.variant ?? "")}>
                <a href={o.url} target="_blank" rel="noopener noreferrer">{o.seller}</a>
                <span className="price">{npr(o.price_npr)}</span>
                {o.variant && <span className="tag">{o.variant}</span>}
                {o.official && <span className="badge good">Official</span>}
                {o.in_stock === false && <span className="badge low">Out of stock</span>}
                {o.listed_price_only && <span className="badge">Listed price</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="actions">
        <a className="btn ghost" href={`#/product/${encodeURIComponent(pick.key)}`}>Details</a>
        <button type="button" className={`btn ghost ${inCompare ? "on" : ""}`} aria-pressed={inCompare}
                onClick={() => toggleCompare(pick.key)}>
          {inCompare ? "✓ In compare" : "+ Compare"}
        </button>
      </div>
    </article>
  );
}
