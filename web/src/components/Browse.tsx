import { useEffect, useState } from "react";
import { api, type Summary } from "../api";
import { useApp } from "../context";
import { chipKeys, chipSpec, npr, parseAmount } from "../format";

const PAGE = 40;

export default function Browse() {
  const { meta, compare, toggleCompare } = useApp();
  const [category, setCategory] = useState("phone");
  const [q, setQ] = useState("");
  const [minText, setMinText] = useState("");
  const [maxText, setMaxText] = useState("");
  const [sort, setSort] = useState("price");
  const [items, setItems] = useState<Summary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const effectiveSort = q ? (sort === "price" ? "relevance" : sort) : sort === "relevance" ? "price" : sort;
  const params = {
    category: category || undefined, q, sort: effectiveSort,
    min_price: parseAmount(minText) ?? undefined, max_price: parseAmount(maxText) ?? undefined,
    priced_only: effectiveSort.includes("price") ? "true" : undefined,
  };
  const paramKey = JSON.stringify(params);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const t = setTimeout(() => {
      api.products({ ...params, limit: PAGE, offset: 0 })
        .then((r) => { if (!cancelled) { setItems(r.items); setTotal(r.total); } })
        .finally(() => !cancelled && setLoading(false));
    }, 200);
    return () => { cancelled = true; clearTimeout(t); };
  }, [paramKey]);

  function more() {
    api.products({ ...params, limit: PAGE, offset: items.length }).then((r) => setItems((s) => [...s, ...r.items]));
  }

  const cats = meta.categories.filter((c) => (meta.stats.by_category[c.id] ?? 0) > 0);

  return (
    <div className="browse">
      <div className="toolbar panel">
        <label>
          <span>Category</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All</option>
            {cats.map((c) => <option key={c.id} value={c.id}>{c.label} ({meta.stats.by_category[c.id]})</option>)}
          </select>
        </label>
        <label className="grow">
          <span>Search</span>
          <input type="search" placeholder="Model or brand" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <label>
          <span>Min price</span>
          <input inputMode="decimal" placeholder="any" value={minText} onChange={(e) => setMinText(e.target.value)} />
        </label>
        <label>
          <span>Max price</span>
          <input inputMode="decimal" placeholder="any" value={maxText} onChange={(e) => setMaxText(e.target.value)} />
        </label>
        <label>
          <span>Sort</span>
          <select value={effectiveSort} onChange={(e) => setSort(e.target.value)}>
            {q && <option value="relevance">Best match</option>}
            <option value="price">Price: low to high</option>
            <option value="-price">Price: high to low</option>
            <option value="rating">Buyer rating</option>
            <option value="name">Name</option>
          </select>
        </label>
      </div>

      <p className="muted">{loading ? "Loading…" : `${total.toLocaleString("en-IN")} devices`}</p>

      <div className="grid">
        {items.map((p) => {
          const inCompare = compare.includes(p.key);
          return (
            <article key={p.key} className="card tile">
              <h3><a href={`#/product/${encodeURIComponent(p.key)}`}>{p.name}</a></h3>
              <div className="tile-price">
                <strong>{npr(p.best_price)}</strong>
                {p.offer_count > 1 && <span className="muted"> · {p.offer_count} sellers</span>}
                {p.best_official && <span className="badge good">Official</span>}
              </div>
              {p.rating != null && (
                <div className="muted small">★ {p.rating.toFixed(1)}{p.review_count ? ` (${p.review_count.toLocaleString("en-IN")})` : ""}</div>
              )}
              <ul className="specline compact">
                {chipKeys(p.category, p.specs).map((k) => (
                  <li key={k}>{chipSpec(k, p.specs[k], meta.specs[k])}</li>
                ))}
              </ul>
              <button type="button" className={`btn ghost small ${inCompare ? "on" : ""}`} aria-pressed={inCompare}
                      onClick={() => toggleCompare(p.key)}>
                {inCompare ? "✓ In compare" : "+ Compare"}
              </button>
            </article>
          );
        })}
      </div>
      {items.length < total && (
        <div className="center"><button type="button" className="btn" onClick={more}>Show more</button></div>
      )}
      {!loading && total === 0 && (
        <div className="empty"><p>No devices match. Clear the filters, or add data from <a href="#/sources">Data sources</a>.</p></div>
      )}
    </div>
  );
}
