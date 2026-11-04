const API_URL = process.env.API_URL ?? "http://localhost:8000";

export type CompanySummary = {
  ticker: string;
  name: string;
  name_ja: string | null;
  currency: string;
  ip_count: number;
  last_close: number | null;
  last_close_date: string | null;
  return_5d: number | null;
  hype: number | null;
  divergence: number | null;
};

export type IpSummary = {
  id: number;
  slug: string;
  name_en: string;
  name_ja: string | null;
  type: string;
  role: string;
  weight: number;
  hype: number | null;
  hype_date: string | null;
  components: Record<string, number>;
};

export type CompanyDetail = CompanySummary & { ips: IpSummary[] };

export type SeriesPoint = {
  date: string;
  close: number | null;
  hype: number | null;
  divergence: number | null;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    path: string,
  ) {
    super(`API ${status} for ${path}`);
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new ApiError(res.status, path);
  return res.json() as Promise<T>;
}

export const getCompanies = () => get<CompanySummary[]>("/companies");

export const getCompany = (ticker: string) =>
  get<CompanyDetail>(`/companies/${encodeURIComponent(ticker)}`);

export const getCompanySeries = (ticker: string) =>
  get<SeriesPoint[]>(`/companies/${encodeURIComponent(ticker)}/series`);
