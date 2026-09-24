import { useEffect, useMemo, useState } from "react";
import { api, type Advice, type Must, type MustInfo, type Parsed } from "../api";
import { useApp } from "../context";
import { npr, nprShort, osName, parseAmount } from "../format";
import PickCard from "./PickCard";

type MustState = Record<string, number | boolean>;

const TOP = 5;   // best picks shown first; every other match is listed below them

const EXCLUDED_LABELS: Record<string, string> = {
  no_nepal_price: "not sold in Nepal",
  no_price: "with no price yet (can't check the budget)",
  over_budget: "over budget",
  under_min_budget: "under your minimum",
  os: "other OS",
  brand: "brand filter",
  must_have: "missing a must-have",
};

export default function Advisor() {
  const { meta } = useApp();
  const categories = meta.categories.filter((c) => c.uses.length > 0);
  const [category, setCategory] = useState("phone");
  const cat = categories.find((c) => c.id === category) ?? categories[0];

  const [minText, setMinText] = useState("");
  const [maxText, setMaxText] = useState("60k");
  const [uses, setUses] = useState<string[]>([]);
  const [os, setOs] = useState<string[]>([]);
  const [musts, setMusts] = useState<MustState>({});
  const [officialOnly, setOfficialOnly] = useState(false);
  const [brands, setBrands] = useState<string[]>([]);
  const [excludeBrands, setExcludeBrands] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [understood, setUnderstood] = useState<string[] | null>(null);
  const [inStock, setInStock] = useState(false);
  const [nepalOnly, setNepalOnly] = useState(false);
  const [moreShown, setMoreShown] = useState(20);   // matches beyond the top 5, shown 20 at a time

  const [advice, setAdvice] = useState<Advice | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const budgetMin = parseAmount(minText);
  const budgetMax = parseAmount(maxText);
  const budgetInvalid = (minText && budgetMin == null) || (maxText && budgetMax == null);

  function pickCategory(id: string) {
    setCategory(id);
    setUses([]);
    setOs([]);
    setMusts({});
    const presets = categories.find((c) => c.id === id)?.budgets ?? [];
    setMinText("");
    setMaxText(presets.length ? nprShort(presets[Math.floor(presets.length / 2)]).replace("Rs ", "") : "");
  }

  // Plain words -> form. The form stays editable, so a misunderstanding is visible and fixable.
  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    let p: Parsed;
    try {
      p = await api.parse(query);
    } catch (err) {
      setError((err as Error).message);
      return;
    }
    const target = categories.find((c) => c.id === p.category) ?? cat;
    setCategory(target.id);
    setMinText(p.budget_min ? String(p.budget_min) : "");
    setMaxText(p.budget_max ? String(p.budget_max) : "");
    setUses(p.uses.filter((u) => target.uses.some((x) => x.id === u)));
    setOs(p.os.filter((o) => target.os.includes(o)));
    setMusts(Object.fromEntries(p.must.map((m) => [m.key, m.value])));
    setBrands(p.brands);
    setExcludeBrands(p.exclude_brands);
    setUnderstood(p.understood);
  }

  function toggleUse(id: string) {
    setUses((u) => (u.includes(id) ? u.filter((x) => x !== id) : [...u, id]));
  }

  const request = useMemo(() => {
    // Earlier choices matter more: with 3 picks the weights are 3, 2, 1.
    const weighted = Object.fromEntries(uses.map((u, i) => [u, uses.length - i]));
    const must: Must[] = Object.entries(musts).map(([key, value]) => {
      const info = cat.musts.find((m) => m.key === key);
      const op = info?.type === "max" ? "<=" : info?.type === "bool" ? "==" : ">=";
      return { key, op, value };
    });
    return {
      category: cat.id, budget_min: budgetMin, budget_max: budgetMax,
      uses: uses.length ? weighted : { balanced: 1 }, os, must,
      brands, exclude_brands: excludeBrands,
      official_only: officialOnly, in_stock_only: inStock, nepal_only: nepalOnly, top: 500,
    };
  }, [cat, budgetMin, budgetMax, uses, os, musts, brands, excludeBrands, officialOnly, inStock, nepalOnly]);

  useEffect(() => {
    if (budgetInvalid) return;
    let cancelled = false;
    setLoading(true);
    const t = setTimeout(() => {
      api.advise(request)
        .then((a) => !cancelled && (setAdvice(a), setError(null), setMoreShown(20)))
        .catch((e) => !cancelled && setError(e.message))
        .finally(() => !cancelled && setLoading(false));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [request, budgetInvalid]);

  const inCategory = meta.stats.by_category[cat.id] ?? 0;
  const excluded = advice
    ? Object.entries(advice.excluded).filter(([k, v]) => v > 0 && k !== "wrong_category")
    : [];

  return (
    <div className="advisor">
      <section className="panel form" aria-label="Your needs">
        <h1>What are you looking for?</h1>

        <form className="ask" onSubmit={ask} role="search">
          <label htmlFor="ask-input" className="sr-only">Describe what you want</label>
          <input id="ask-input" type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                 placeholder='e.g. "photography phone under 1.2 lakh"' />
          <button type="submit" className="btn primary">Ask</button>
        </form>
        {understood ? (
          <div className="understood" aria-live="polite">
            <span className="muted small">Understood:</span>
            {understood.length ? understood.map((u) => <span key={u} className="tag">{u}</span>)
              : <span className="muted small">nothing specific. Try naming a device, a use and a budget.</span>}
            {(brands.length > 0 || excludeBrands.length > 0) && (
              <button type="button" className="link small" onClick={() => { setBrands([]); setExcludeBrands([]); }}>
                clear brand filter
              </button>
            )}
          </div>
        ) : (
          <p className="hint">Or choose below. Try: "gaming phone below 50k with 5g", "long lasting laptop for coding under 1.5 lakh".</p>
        )}

        <fieldset>
          <legend>Device</legend>
          <div className="chips">
            {categories.map((c) => (
              <button key={c.id} type="button" className={`chip ${c.id === cat.id ? "on" : ""}`}
                      aria-pressed={c.id === cat.id} onClick={() => pickCategory(c.id)}>
                {c.label}
                <span className="chip-count">{meta.stats.by_category[c.id] ?? 0}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend>Budget (NPR)</legend>
          <div className="budget">
            <label>
              <span>From</span>
              <input inputMode="decimal" placeholder="any" value={minText} onChange={(e) => setMinText(e.target.value)} />
            </label>
            <span className="dash">–</span>
            <label>
              <span>Up to</span>
              <input inputMode="decimal" placeholder="any" value={maxText} onChange={(e) => setMaxText(e.target.value)} />
            </label>
          </div>
          <p className={`hint ${budgetInvalid ? "bad" : ""}`}>
            {budgetInvalid
              ? "Write amounts like 45000, 45k or 1.5 lakh"
              : budgetMin || budgetMax
                ? `${budgetMin ? npr(budgetMin) : "Any"} to ${budgetMax ? npr(budgetMax) : "any"}`
                : "Any price"}
          </p>
          <div className="chips small">
            {cat.budgets.map((b) => (
              <button key={b} type="button" className={`chip ${budgetMax === b && !budgetMin ? "on" : ""}`}
                      onClick={() => { setMinText(""); setMaxText(nprShort(b).replace("Rs ", "")); }}>
                under {nprShort(b)}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend>What matters most? <span className="muted">Tap in order of importance</span></legend>
          <div className="uses">
            {cat.uses.map((u) => {
              const rank = uses.indexOf(u.id);
              return (
                <button key={u.id} type="button" className={`use ${rank >= 0 ? "on" : ""}`}
                        aria-pressed={rank >= 0} onClick={() => toggleUse(u.id)} title={u.description}>
                  {rank >= 0 && <span className="rank">{rank + 1}</span>}
                  <span className="use-label">{u.label}</span>
                  <span className="use-desc">{u.description}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        {cat.musts.length > 0 && (
          <fieldset>
            <legend>Must have</legend>
            <div className="musts">
              {cat.musts.map((m) => (
                <MustControl key={m.key} info={m} value={musts[m.key]}
                             onChange={(v) => setMusts((s) => {
                               const next = { ...s };
                               if (v === null) delete next[m.key];
                               else next[m.key] = v;
                               return next;
                             })} />
              ))}
            </div>
          </fieldset>
        )}

        {cat.os.length > 0 && (
          <fieldset>
            <legend>Operating system</legend>
            <div className="chips">
              <button type="button" className={`chip ${os.length === 0 ? "on" : ""}`} onClick={() => setOs([])}>Any</button>
              {cat.os.map((o) => (
                <button key={o} type="button" className={`chip ${os.includes(o) ? "on" : ""}`} aria-pressed={os.includes(o)}
                        onClick={() => setOs((s) => (s.includes(o) ? s.filter((x) => x !== o) : [...s, o]))}>
                  {osName(o)}
                </button>
              ))}
            </div>
          </fieldset>
        )}

        <fieldset>
          <legend>Where to buy</legend>
          <label className="check">
            <input type="checkbox" checked={officialOnly} onChange={(e) => setOfficialOnly(e.target.checked)} />
            Only official / authorised sellers <span className="muted">(proper warranty)</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={inStock} onChange={(e) => setInStock(e.target.checked)} />
            Only in stock
          </label>
          <label className="check">
            <input type="checkbox" checked={nepalOnly} onChange={(e) => setNepalOnly(e.target.checked)} />
            Only sold in Nepal <span className="muted">(hide devices priced only abroad)</span>
          </label>
        </fieldset>
      </section>

      <section className="results" aria-live="polite" aria-busy={loading}>
        {inCategory === 0 ? (
          <NoData label={cat.label} />
        ) : error ? (
          <div className="empty"><h2>Something went wrong</h2><p>{error}</p></div>
        ) : !advice ? (
          <div className="loading">Finding devices…</div>
        ) : (
          <>
            <div className="results-head">
              <h2>
                {advice.picks.length ? `Best ${cat.label.toLowerCase()} for you` : "No exact matches"}
                {loading && <span className="spinner" aria-label="updating" />}
              </h2>
              <p className="muted">
                Compared {advice.considered} {cat.label.toLowerCase()}
                {excluded.length > 0 && <> · skipped {excluded.map(([k, v]) => `${v} ${EXCLUDED_LABELS[k] ?? k}`).join(", ")}</>}
              </p>
            </div>

            {advice.picks.length === 0 && (
              <div className="empty">
                {(advice.excluded.no_price ?? 0) > 0 && advice.considered === 0 ? (
                  <p>
                    {advice.excluded.no_price} {cat.label.toLowerCase()} have specs but no price anywhere yet, so they can't
                    be checked against a budget. Clear the budget to see them, or wait for the scraper to reach more
                    stores (<a href="#/sources">Data sources</a>).
                  </p>
                ) : (
                  <p>Nothing fits every requirement. Try raising the budget, removing a must-have, or allowing any OS.</p>
                )}
              </div>
            )}

            {advice.picks.length > 0 && (
              <ol className="picks">
                {advice.picks.slice(0, TOP).map((p, i) => (
                  <li key={p.key}><PickCard pick={p} rank={i + 1} /></li>
                ))}
              </ol>
            )}

            {advice.picks.length > TOP && (
              <section className="more-picks">
                <h3>More {cat.label.toLowerCase()} that fit ({advice.picks.length - TOP})</h3>
                <p className="muted">Also match everything you asked for, ranked by how well they fit.</p>
                <ol className="picks" start={TOP + 1}>
                  {advice.picks.slice(TOP, TOP + moreShown).map((p, i) => (
                    <li key={p.key}><PickCard pick={p} rank={TOP + i + 1} /></li>
                  ))}
                </ol>
                {advice.picks.length > TOP + moreShown && (
                  <button type="button" className="btn" onClick={() => setMoreShown((n) => n + 20)}>
                    Show {Math.min(20, advice.picks.length - TOP - moreShown)} more
                  </button>
                )}
              </section>
            )}

            {advice.value_pick && (
              <div className="special">
                <h3>Save money</h3>
                <p className="muted">Nearly as good as #1 for less.</p>
                <PickCard pick={advice.value_pick} tone="value"
                          savings={advice.picks[0]?.price_npr != null && advice.value_pick.price_npr != null && !advice.picks[0].price_converted
                            ? advice.picks[0].price_npr - advice.value_pick.price_npr : undefined} />
              </div>
            )}
            {advice.stretch_pick && (
              <div className="special">
                <h3>Worth stretching?</h3>
                <p className="muted">
                  {budgetMax && advice.stretch_pick.price_npr != null ? `${npr(advice.stretch_pick.price_npr - budgetMax)} over your budget, ` : ""}
                  but clearly better for what you asked for.
                </p>
                <PickCard pick={advice.stretch_pick} tone="stretch" />
              </div>
            )}
            <p className="fineprint">
              Scores compare devices against each other for your needs (100 = best available). Confidence shows how much
              of the score comes from real data. Always confirm price, variant and warranty with the seller.
            </p>
          </>
        )}
      </section>
    </div>
  );
}

function MustControl({ info, value, onChange }: { info: MustInfo; value: number | boolean | undefined; onChange: (v: number | boolean | null) => void }) {
  if (info.type === "bool" || (info.value != null && !info.options)) {
    const on = value !== undefined;
    return (
      <button type="button" className={`chip ${on ? "on" : ""}`} aria-pressed={on}
              onClick={() => onChange(on ? null : info.type === "bool" ? true : info.value!)}>
        {info.label}
      </button>
    );
  }
  return (
    <label className="must-select">
      <span>{info.label}</span>
      <select value={value === undefined ? "" : String(value)}
              onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}>
        <option value="">Any</option>
        {info.options?.map((o) => (
          <option key={o} value={o}>
            {info.type === "max" ? "≤ " : ""}{o >= 1024 && info.unit === "GB" ? `${o / 1024} TB` : `${o.toLocaleString("en-IN")} ${info.unit ?? ""}`}{info.type === "min" ? "+" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

function NoData({ label }: { label: string }) {
  const { meta } = useApp();
  return (
    <div className="empty">
      <h2>No {label.toLowerCase()} in the database yet</h2>
      {meta.stats.products === 0 ? (
        <>
          <p>DeviceScout needs data before it can recommend anything.</p>
          <ol className="steps">
            <li>Open <a href="#/sources">Data sources</a> and press <b>Check sources</b> to see which sites work from your connection.</li>
            <li>Press <b>Update prices</b> to scrape them. The first run takes a few minutes.</li>
          </ol>
          <p className="muted">Just want to look around? Run <code>devicescout serve --sample</code> for a fictional demo catalogue.</p>
        </>
      ) : (
        <p>Other categories have data. Scrape more sources from <a href="#/sources">Data sources</a> to fill this one.</p>
      )}
    </div>
  );
}
