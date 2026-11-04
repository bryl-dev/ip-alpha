import Link from "next/link";
import { Suspense } from "react";

import { getCompanies } from "@/lib/api";
import { fmtNumber, fmtPercent, fmtPrice, signClass } from "@/lib/format";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-10">
      <h1 className="text-2xl font-semibold">Tracked companies</h1>
      <p className="mt-1 text-sm text-neutral-400">
        Hype is a weighted z-score of fandom activity across each company&apos;s IPs. Divergence is
        hype minus the z-score of the stock&apos;s 5-day return over the Nikkei 225; high values
        mean hype is running ahead of the price.
      </p>
      <Suspense fallback={<p className="mt-8 text-neutral-500">Loading companies…</p>}>
        <CompanyTable />
      </Suspense>
    </main>
  );
}

async function CompanyTable() {
  let companies;
  try {
    companies = await getCompanies();
  } catch {
    return (
      <p className="mt-8 text-rose-400">
        Could not reach the API. Is the backend running on {process.env.API_URL ?? "localhost:8000"}?
      </p>
    );
  }

  return (
    <table className="mt-8 w-full text-sm [&_td:not(:first-child)]:whitespace-nowrap [&_td:not(:first-child)]:pl-4 [&_th:not(:first-child)]:pl-4">
      <thead className="border-b border-neutral-800 text-left text-neutral-400">
        <tr>
          <th className="py-2 font-medium">Company</th>
          <th className="py-2 font-medium">Ticker</th>
          <th className="py-2 text-right font-medium">IPs</th>
          <th className="py-2 text-right font-medium">Close</th>
          <th className="py-2 text-right font-medium">5d</th>
          <th className="py-2 text-right font-medium">Hype</th>
          <th className="py-2 text-right font-medium">Divergence</th>
        </tr>
      </thead>
      <tbody>
        {companies.map((c) => (
          <tr key={c.ticker} className="border-b border-neutral-900 hover:bg-neutral-900/60">
            <td className="py-3">
              <Link href={`/companies/${encodeURIComponent(c.ticker)}`} className="hover:underline">
                {c.name}
              </Link>
              {c.name_ja && <span className="ml-2 text-neutral-500">{c.name_ja}</span>}
            </td>
            <td className="py-3 font-mono text-neutral-400">{c.ticker}</td>
            <td className="py-3 text-right">{c.ip_count}</td>
            <td className="py-3 text-right">{fmtPrice(c.last_close, c.currency)}</td>
            <td className={`py-3 text-right ${signClass(c.return_5d)}`}>
              {fmtPercent(c.return_5d)}
            </td>
            <td className={`py-3 text-right ${signClass(c.hype)}`}>{fmtNumber(c.hype)}</td>
            <td className={`py-3 text-right ${signClass(c.divergence)}`}>
              {fmtNumber(c.divergence)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
