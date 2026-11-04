export function fmtNumber(value: number | null, digits = 2): string {
  return value == null ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function fmtPrice(value: number | null, currency: string): string {
  if (value == null) return "—";
  return value.toLocaleString("en-US", { style: "currency", currency, maximumFractionDigits: 0 });
}

export function fmtPercent(value: number | null): string {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

export function signClass(value: number | null): string {
  if (value == null || Math.abs(value) < 1e-9) return "text-neutral-400";
  return value > 0 ? "text-emerald-400" : "text-rose-400";
}
