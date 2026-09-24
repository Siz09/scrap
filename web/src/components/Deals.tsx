import { useEffect, useState } from "react";
import { api, type DealItem } from "../api";
import { useApp } from "../context";
import { chipKeys, chipSpec, npr, parseAmount } from "../format";

// How each verdict is shown. Good news in green, warnings in red, the rest neutral.
const VERDICTS: Record<string, { label: string; tone: string; tip: string }> = {
  "real deal": { label: "Real deal", tone: "good", tip: "At least 12% below what other sellers charge" },
  "good price": { label: "Good price", tone: "good", tip: "7–12% below what other sellers charge" },
  "price drop": { label: "Price dropped", tone: "good", tip: "This seller cut its own price since we last checked" },
  "lowest price seen": { label: "Lowest we've seen", tone: "good", tip: "At or below the lowest price DeviceScout has recorded" },
  "paper discount": { label: "Discount on paper only", tone: "low", tip: "Claims a discount but costs no less than other sellers" },
  "inflated original": { label: "Crossed-out price looks inflated", tone: "low", tip: "The 'was' price is well above anything this model sold for" },
  unverified: { label: "Can't verify", tone: "", tip: "No other seller or price history to check the claim against" },
};

function daysLeft(iso: string): string {
  const d = Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
  return d < 0 ? "ended" : d === 0 ? "ends today" : d === 1 ? "ends tomorrow" : `ends in ${d} days`;
}

export default function Deals() {
  const { meta, compare, toggleCompare } = useApp();
  const [category, setCategory] = useState("");
  const [maxText, setMaxText] = useState("");
  const [verifiedOnly, setVerifiedOnly] = useState(true);
  const [items, setItems] = useState<DealItem[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.deals({ category: category || undefined, verified_only: verifiedOnly ? "true" : "false",
                max_price: parseAmount(maxText) ?? undefined })
      .then((r) => !cancelled && setItems(r.items))
      .catch(() => !cancelled && setItems([]));
    return () => { cancelled = true; };
  }, [category, maxText, verifiedOnly]);

  const cats = meta.categories.filter((c) => (meta.stats.by_category[c.id] ?? 0) > 0);

  return (
    <div className="deals">
      <div className="deals-head">
        <div>
          <h1>Deals</h1>
          <p className="muted">
            Every discount is checked against other sellers' prices and the price history DeviceScout has recorded,
            not the store's crossed-out price.
          </p>
        </div>
      </div>

      <div className="toolbar panel">
        <label>
          <span>Category</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All</option>
            {cats.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
        </label>
        <label>
          <span>Max price</span>
          <input inputMode="decimal" placeholder="any" value={maxText} onChange={(e) => setMaxText(e.target.value)} />
        </label>
        <label className="check toolbar-check">
          <input type="checkbox" checked={!verifiedOnly} onChange={(e) => setVerifiedOnly(!e.target.checked)} />
          Also show store claims we couldn't confirm
        </label>
      </div>

      {items === null ? <div className="loading">Loading…</div> : items.length === 0 ? (
        <div className="empty">
          <p>No {verifiedOnly ? "confirmed " : ""}deals right now.</p>
          <p className="muted small">Deals show up after a scrape finds a price below the market or a price cut. Update prices from <a href="#/sources">Data sources</a>.</p>
        </div>
      ) : (
        <div className="grid deal-grid">
          {items.map((d) => {
            const inCompare = compare.includes(d.key);
            const bad = d.verdicts.some((v) => VERDICTS[v]?.tone === "low");
            return (
              <article key={d.url + (d.variant ?? "")} className={`card deal ${bad ? "doubtful" : d.verified ? "verified" : ""}`}>
                {d.saving_pct != null && d.saving_pct > 0 && !bad && (
                  <div className="deal-ribbon">{d.saving_pct.toFixed(0)}% below market</div>
                )}
                <h3><a href={`#/product/${encodeURIComponent(d.key)}`}>{d.name}</a></h3>
                <div className="deal-price">
                  <strong>{npr(d.price)}</strong>
                  {d.original_price && <s className="muted"> {npr(d.original_price)}</s>}
                  {d.claimed_pct != null && <span className="muted small"> store says {d.claimed_pct.toFixed(0)}% off</span>}
                </div>
                <div className="muted small">
                  at <a href={d.url} target="_blank" rel="noopener noreferrer">{d.seller}</a>
                  {d.variant && <> · {d.variant}</>}
                  {d.official && <span className="badge good">Official</span>}
                  {d.in_stock === false && <span className="badge low">Out of stock</span>}
                </div>
                <div className="verdicts">
                  {d.verdicts.map((v) => (
                    <span key={v} className={`badge ${VERDICTS[v]?.tone ?? ""}`} title={VERDICTS[v]?.tip}>{VERDICTS[v]?.label ?? v}</span>
                  ))}
                </div>
                <dl className="deal-facts">
                  {d.market_price != null && <div><dt>Market price</dt><dd>{npr(d.market_price)}</dd></div>}
                  {d.saving != null && <div><dt>{d.saving >= 0 ? "You save" : "You pay extra"}</dt><dd className={d.saving >= 0 ? "pos" : "neg"}>{npr(Math.abs(d.saving))}</dd></div>}
                  {d.dropped_from != null && <div><dt>Was at this seller</dt><dd>{npr(d.dropped_from)}</dd></div>}
                  {d.valid_until && <div><dt>Sale</dt><dd>{daysLeft(d.valid_until)}</dd></div>}
                </dl>
                <ul className="specline compact">
                  {chipKeys(d.category, d.specs).map((k) => <li key={k}>{chipSpec(k, d.specs[k], meta.specs[k])}</li>)}
                </ul>
                <button type="button" className={`btn ghost small ${inCompare ? "on" : ""}`} onClick={() => toggleCompare(d.key)}>
                  {inCompare ? "✓ In compare" : "+ Compare"}
                </button>
              </article>
            );
          })}
        </div>
      )}
      <p className="fineprint">
        Market price = the typical price other trusted sellers ask for the same model (or its recent recorded price).
        Listings flagged as implausibly cheap are never shown as deals. Always confirm the final price and warranty with the seller.
      </p>
    </div>
  );
}
