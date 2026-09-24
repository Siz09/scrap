import type { ConvertedFrom } from "../api";
import { useApp } from "../context";
import { npr } from "../format";

const SYMBOL: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", INR: "₹" };

export function money(price: number, currency: string): string {
  const sym = SYMBOL[currency.toUpperCase()];
  const amount = price.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return sym ? `${sym}${amount}` : `${currency.toUpperCase()} ${amount}`;
}

/** The price of a device, honest about where it comes from:
 *  a Nepali shop price, a price abroad converted to NPR, or no price at all. */
export default function PriceTag({ local, converted, from, available, compact }: {
  local: number | null | undefined;
  converted?: number | null;
  from?: ConvertedFrom | null;
  available: boolean;
  compact?: boolean;
}) {
  const { meta } = useApp();
  if (local != null) {
    return (
      <span className="price-tag">
        <strong>{npr(local)}</strong>
        {!compact && <span className="badge good">Sold in Nepal</span>}
      </span>
    );
  }
  const rate = from && meta.rates?.rates[from.currency.toUpperCase()];
  const how = from
    ? `${money(from.price, from.currency)}${from.seller ? ` at ${from.seller}` : ""}` +
      (rate ? `, converted at 1 ${from.currency.toUpperCase()} = Rs ${rate} (${meta.rates?.source}${meta.rates?.date ? `, ${meta.rates.date}` : ""})` : "") +
      ". Before import duty, VAT and shipping."
    : undefined;
  return (
    <span className="price-tag">
      {converted != null ? (
        <>
          <strong title={how}>≈ {npr(converted)}</strong>
          {from && <span className="muted small"> converted from {money(from.price, from.currency)}</span>}
        </>
      ) : (
        <strong className="muted">No price yet</strong>
      )}
      <span className={`badge ${available ? "good" : "low"}`} title={available ? undefined : "No Nepali store lists it yet"}>
        {available ? "Sold in Nepal" : "Not sold in Nepal yet"}
      </span>
    </span>
  );
}
