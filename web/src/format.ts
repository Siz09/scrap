import type { SpecMeta, SpecValue, Specs } from "./api";

// Nepal uses lakh grouping (1,49,999), same as India.
const nf = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function npr(n: number | null | undefined): string {
  return n == null ? "—" : `Rs ${nf.format(n)}`;
}

export function nprShort(n: number): string {
  if (n >= 100000) return `Rs ${+(n / 100000).toFixed(n % 100000 ? 2 : 0)} lakh`;
  if (n >= 1000) return `Rs ${+(n / 1000).toFixed(n % 1000 ? 1 : 0)}k`;
  return `Rs ${n}`;
}

/** "50k", "1.5 lakh", "60,000" -> number (NPR). Empty -> null. */
export function parseAmount(text: string): number | null {
  const s = text.trim().toLowerCase().replace(/,/g, "").replace(/^(rs\.?|npr)\s*/, "");
  if (!s) return null;
  const m = s.match(/^(\d+(?:\.\d+)?)\s*(k|lakh|lac|l)?$/);
  if (!m) return null;
  const mult = m[2] === "k" ? 1000 : m[2] ? 100000 : 1;
  return Math.round(parseFloat(m[1]) * mult);
}

const OS_NAMES: Record<string, string> = {
  android: "Android", ios: "iOS", ipados: "iPadOS", macos: "macOS", windows: "Windows",
  chromeos: "ChromeOS", linux: "Linux", wearos: "Wear OS", watchos: "watchOS", harmonyos: "HarmonyOS",
  tizen: "Tizen",
};

export function osName(os: string): string {
  return OS_NAMES[os] ?? os;
}

export function formatSpec(key: string, value: SpecValue, meta?: SpecMeta): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (key === "os" && typeof value === "string") return osName(value);
  if (typeof value !== "number") return String(value);
  if (key === "storage_gb" && value >= 1024) return `${+(value / 1024).toFixed(1)} TB`;
  if (key === "resolution_px") return `${(value / 1e6).toFixed(1)} MP`;
  if (key === "weight_g" && value >= 1000) return `${+(value / 1000).toFixed(2)} kg`;
  if (key === "release_year") return String(value);
  const unit = meta?.unit && meta.unit !== "bool" ? meta.unit : "";
  const num = value >= 1000 ? nf.format(value) : String(+value.toFixed(2));
  if (!unit) return num;
  return unit.startsWith("/") || unit === "x" ? `${num}${unit}` : `${num} ${unit}`;
}

// The specs worth showing on a card, per category, in order.
export const KEY_SPECS: Record<string, string[]> = {
  phone: ["chipset", "ram_gb", "storage_gb", "main_camera_mp", "optical_zoom_x", "battery_mah", "charging_w", "refresh_rate_hz"],
  laptop: ["chipset", "gpu", "ram_gb", "storage_gb", "display_size_in", "refresh_rate_hz", "battery_wh", "weight_g"],
  tablet: ["chipset", "ram_gb", "storage_gb", "display_size_in", "refresh_rate_hz", "battery_mah"],
  smartwatch: ["os", "has_gps", "battery_mah", "water_resistance", "weight_g"],
  power_bank: ["capacity_mah", "output_w", "weight_g"],
  charger: ["output_w", "ports"],
  earbuds: ["battery_mah", "water_resistance"],
};

export function keySpecs(category: string, specs: Specs, limit = 6): string[] {
  return (KEY_SPECS[category] ?? Object.keys(specs)).filter((k) => specs[k] != null).slice(0, limit);
}

// For comparison highlighting: which direction is "better". Some specs are preferences, not quality.
export const LOWER_IS_BETTER = new Set(["weight_g"]);
export const NO_BEST = new Set(["display_size_in", "camera_count"]);

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
