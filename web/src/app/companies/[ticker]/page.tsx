import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import HypePriceChart from "@/components/HypePriceChart";
import { ApiError, getCompany, getCompanySeries, getCompanyTheses, type Thesis } from "@/lib/api";
import { fmtNumber, fmtPercent, fmtPrice, signClass } from "@/lib/format";

export default function CompanyPage({ params }: PageProps<"/companies/[ticker]">) {
  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-10">
      <Link href="/" className="text-sm text-neutral-400 hover:underline">
        ← All companies
      </Link>
      <Suspense fallback={<p className="mt-8 text-neutral-500">Loading…</p>}>
        <CompanyView params={params} />
      </Suspense>
    </main>
  );
}

async function CompanyView({ params }: { params: Promise<{ ticker: string }> }) {
  const ticker = decodeURIComponent((await params).ticker);
  let company, series, theses;
  try {
    [company, series, theses] = await Promise.all([
      getCompany(ticker),
      getCompanySeries(ticker),
      getCompanyTheses(ticker),
    ]);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }
  const shortName = company.name.replace(/,?\s+(Co\.,?\s*Ltd\.|Corporation|Holdings|Group)/g, "");
  const question = `Why is ${shortName} moving, and does fan activity explain it?`;

  return (
    <>
      <header className="mt-4 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="text-2xl font-semibold">{company.name}</h1>
        <span className="font-mono text-neutral-400">{company.ticker}</span>
        {company.name_ja && <span className="text-neutral-500">{company.name_ja}</span>}
        <Link
          href={`/chat?q=${encodeURIComponent(question)}`}
          className="ml-auto text-sm text-sky-300 hover:underline"
        >
          Ask the analyst about {shortName} →
        </Link>
      </header>

      <dl className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Close" value={fmtPrice(company.last_close, company.currency)} />
        <Stat
          label="5-day return"
          value={fmtPercent(company.return_5d)}
          className={signClass(company.return_5d)}
        />
        <Stat label="Hype" value={fmtNumber(company.hype)} className={signClass(company.hype)} />
        <Stat
          label="Divergence"
          value={fmtNumber(company.divergence)}
          className={signClass(company.divergence)}
        />
      </dl>

      <section className="mt-8 rounded-lg border border-neutral-800 p-4">
        <h2 className="mb-3 text-sm font-medium text-neutral-300">Hype vs. price (last 180 days)</h2>
        {series.length ? (
          <HypePriceChart data={series} currency={company.currency} />
        ) : (
          <p className="text-neutral-500">No data yet. Run ingestion and scoring first.</p>
        )}
      </section>

      {theses[0] && <ThesisCard thesis={theses[0]} />}

      <section className="mt-8">
        <h2 className="text-sm font-medium text-neutral-300">Tracked IPs</h2>
        <table className="mt-3 w-full text-sm [&_td:not(:first-child)]:whitespace-nowrap [&_td:not(:first-child)]:pl-4 [&_th:not(:first-child)]:pl-4">
          <thead className="border-b border-neutral-800 text-left text-neutral-400">
            <tr>
              <th className="py-2 font-medium">IP</th>
              <th className="py-2 font-medium">Type</th>
              <th className="py-2 font-medium">Role</th>
              <th className="py-2 text-right font-medium">Weight</th>
              <th className="py-2 text-right font-medium">Hype</th>
              <th className="py-2 pl-6 font-medium">Components (z)</th>
            </tr>
          </thead>
          <tbody>
            {company.ips.map((ip) => (
              <tr key={ip.id} className="border-b border-neutral-900">
                <td className="py-3">
                  {ip.name_en}
                  {ip.name_ja && <span className="ml-2 text-neutral-500">{ip.name_ja}</span>}
                </td>
                <td className="py-3 text-neutral-400">{ip.type.replace("_", " ")}</td>
                <td className="py-3 text-neutral-400">{ip.role}</td>
                <td className="py-3 text-right">{ip.weight.toFixed(2)}</td>
                <td className={`py-3 text-right ${signClass(ip.hype)}`}>{fmtNumber(ip.hype)}</td>
                <td className="py-3 pl-6 font-mono text-xs text-neutral-400">
                  {Object.entries(ip.components)
                    .map(([k, v]) => `${k} ${v.toFixed(1)}`)
                    .join(" · ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

const STANCE_LABEL: Record<Thesis["body"]["stance"], string> = {
  hype_ahead_of_price: "Hype ahead of price",
  price_ahead_of_hype: "Price ahead of hype",
  no_clear_signal: "No clear signal",
};

function ThesisCard({ thesis }: { thesis: Thesis }) {
  const { body } = thesis;
  return (
    <section className="mt-8 rounded-lg border border-neutral-800 p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-sm font-medium text-neutral-300">Analyst thesis</h2>
        <span className="text-xs text-neutral-500">
          {thesis.date} · {STANCE_LABEL[body.stance]} · confidence {Math.round(body.confidence * 100)}%
        </span>
      </div>
      <p className="mt-3 font-medium">{body.headline}</p>
      <p className="mt-2 text-sm leading-relaxed text-neutral-300">{body.claim}</p>
      {body.evidence.length > 0 && (
        <ul className="mt-4 space-y-1.5 text-sm text-neutral-400">
          {body.evidence.map((e) => (
            <li key={e.document_id}>
              {e.url ? (
                <a href={e.url} target="_blank" rel="noreferrer" className="text-sky-300 hover:underline">
                  {e.source}
                </a>
              ) : (
                <span className="text-neutral-300">{e.source}</span>
              )}{" "}
              <span className="text-neutral-500">({e.published_at.slice(0, 10)})</span>: {e.point}
            </li>
          ))}
        </ul>
      )}
      {body.risks.length > 0 && (
        <p className="mt-4 text-sm text-neutral-400">
          <span className="text-neutral-300">Risks:</span> {body.risks.join(" ")}
        </p>
      )}
      <p className="mt-4 text-xs text-neutral-600">
        {body.disclaimer} Model: {thesis.model}.
      </p>
    </section>
  );
}

function Stat({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="rounded-lg border border-neutral-800 p-4">
      <dt className="text-xs uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className={`mt-1 text-xl font-semibold ${className ?? ""}`}>{value}</dd>
    </div>
  );
}
