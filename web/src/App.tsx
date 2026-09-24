import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Meta } from "./api";
import Advisor from "./components/Advisor";
import Browse from "./components/Browse";
import Compare from "./components/Compare";
import Deals from "./components/Deals";
import ProductDetail from "./components/ProductDetail";
import Sources from "./components/Sources";
import { AppContext } from "./context";

// Tiny hash router: #/advisor, #/browse, #/compare, #/sources, #/product/<key>
function useHashRoute(): [string, string | null] {
  const read = () => {
    const [page = "advisor", ...rest] = window.location.hash.replace(/^#\/?/, "").split("/");
    return [page || "advisor", rest.length ? decodeURIComponent(rest.join("/")) : null] as [string, string | null];
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

const NAV = [
  { id: "advisor", label: "Find a device" },
  { id: "deals", label: "Deals" },
  { id: "browse", label: "Browse" },
  { id: "compare", label: "Compare" },
  { id: "sources", label: "Data sources" },
];

const COMPARE_KEY = "devicescout.compare";

function loadCompare(): string[] {
  try {
    return JSON.parse(localStorage.getItem(COMPARE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

export default function App() {
  const [page, param] = useHashRoute();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [compare, setCompare] = useState<string[]>(loadCompare);

  const refreshMeta = useCallback(() => {
    api.meta().then(setMeta).catch((e) => setError(String(e.message ?? e)));
  }, []);
  useEffect(refreshMeta, [refreshMeta]);

  useEffect(() => {
    try {
      localStorage.setItem(COMPARE_KEY, JSON.stringify(compare));
    } catch {
      /* private mode */
    }
  }, [compare]);

  const toggleCompare = useCallback((key: string) => {
    setCompare((c) => (c.includes(key) ? c.filter((k) => k !== key) : [...c, key].slice(-4)));
  }, []);

  const ctx = useMemo(
    () => (meta ? { meta, compare, toggleCompare, refreshMeta } : null),
    [meta, compare, toggleCompare, refreshMeta]
  );

  const active = page === "product" ? "browse" : page;

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/advisor" aria-label="DeviceScout home">
          <img src="/icon.svg" alt="" width={28} height={28} />
          <span>DeviceScout</span>
          <span className="brand-sub">Nepal</span>
        </a>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <a key={n.id} href={`#/${n.id}`} className={active === n.id ? "active" : ""}
               aria-current={active === n.id ? "page" : undefined}>
              {n.label}
              {n.id === "compare" && compare.length > 0 && <span className="count">{compare.length}</span>}
            </a>
          ))}
        </nav>
      </header>

      {meta?.sample && (
        <div className="banner" role="note">
          <strong>Sample data.</strong> Fictional devices and prices for trying the app. Start without
          <code>--sample</code> and scrape real sources to see real prices.
        </div>
      )}

      <main className="main">
        {error && (
          <div className="empty">
            <h2>Can't reach the DeviceScout server</h2>
            <p>{error}</p>
            <p className="muted">Start it with <code>devicescout serve</code>, then reload this page.</p>
          </div>
        )}
        {!error && !ctx && <div className="loading">Loading…</div>}
        {ctx && (
          <AppContext.Provider value={ctx}>
            {page === "advisor" && <Advisor />}
            {page === "deals" && <Deals />}
            {page === "browse" && <Browse />}
            {page === "compare" && <Compare />}
            {page === "sources" && <Sources />}
            {page === "product" && param && <ProductDetail productKey={param} />}
          </AppContext.Provider>
        )}
      </main>

      <footer className="footer">
        <span>DeviceScout {meta?.version}</span>
        {meta && (
          <span>
            {meta.stats.products.toLocaleString("en-IN")} devices · {meta.stats.offers.toLocaleString("en-IN")} prices
            {meta.stats.last_scraped && <> · updated {new Date(meta.stats.last_scraped).toLocaleDateString()}</>}
          </span>
        )}
        <span className="muted">Prices change often: always confirm with the seller and check warranty.</span>
      </footer>
    </div>
  );
}
