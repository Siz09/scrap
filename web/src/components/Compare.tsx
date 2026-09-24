import { useEffect, useState } from "react";
import { api, type ProductDetail } from "../api";
import { useApp } from "../context";
import { formatSpec, LOWER_IS_BETTER, NO_BEST } from "../format";
import PriceTag from "./PriceTag";
import DeviceImage from "./DeviceImage";

export default function Compare() {
  const { meta, compare, toggleCompare } = useApp();
  const [items, setItems] = useState<ProductDetail[]>([]);

  useEffect(() => {
    Promise.all(compare.map((k) => api.product(k).catch(() => null))).then((r) =>
      setItems(r.filter((x): x is ProductDetail => x !== null))
    );
  }, [compare]);

  if (compare.length === 0) {
    return (
      <div className="empty">
        <h2>Nothing to compare yet</h2>
        <p>Press <b>+ Compare</b> on up to four devices in <a href="#/advisor">Find a device</a> or <a href="#/browse">Browse</a>.</p>
      </div>
    );
  }

  const keys = Object.keys(meta.specs).filter((k) => items.some((p) => p.specs[k] != null));

  function best(k: string): Set<number> {
    if (NO_BEST.has(k)) return new Set();
    const vals = items.map((p) => p.specs[k]);
    const nums = vals.map((v) => (typeof v === "number" ? v : typeof v === "boolean" ? Number(v) : null));
    const present = nums.filter((v): v is number => v !== null);
    if (present.length < 2 || new Set(present).size === 1) return new Set();
    const target = LOWER_IS_BETTER.has(k) ? Math.min(...present) : Math.max(...present);
    return new Set(nums.flatMap((v, i) => (v === target ? [i] : [])));
  }

  const prices = items.map((p) => p.best_price).filter((v): v is number => v != null);
  const cheapest = prices.length > 1 ? Math.min(...prices) : null;

  return (
    <div className="compare">
      <h1>Compare</h1>
      <div className="table-wrap">
        <table className="compare-table">
          <thead>
            <tr>
              <th scope="col"><span className="sr-only">Spec</span></th>
              {items.map((p) => (
                <th key={p.key} scope="col">
                  <DeviceImage productKey={p.key} name={p.name} size="sm" />
                  <a href={`#/product/${encodeURIComponent(p.key)}`}>{p.name}</a>
                  <button type="button" className="link small" onClick={() => toggleCompare(p.key)}>Remove</button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="price-row">
              <th scope="row">Price</th>
              {items.map((p) => (
                <td key={p.key} className={p.best_price != null && p.best_price === cheapest ? "best" : ""}>
                  <PriceTag local={p.best_price} converted={p.converted_price} from={p.converted_from}
                            available={p.available_in_nepal} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Buyer rating</th>
              {items.map((p) => <td key={p.key}>{p.rating != null ? `★ ${p.rating.toFixed(1)}` : "—"}</td>)}
            </tr>
            {keys.map((k) => {
              const b = best(k);
              return (
                <tr key={k}>
                  <th scope="row">{meta.specs[k].label}</th>
                  {items.map((p, i) => (
                    <td key={p.key} className={b.has(i) ? "best" : ""}>{formatSpec(k, p.specs[k] ?? null, meta.specs[k])}</td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">Highlighted: best value in each row (lighter wins for weight). Screen size is left unmarked: bigger isn't better for everyone.</p>
    </div>
  );
}
