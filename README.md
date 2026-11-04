# IP-Alpha

**Measuring fandom hype for Japanese entertainment IPs and comparing it with the stock prices of the companies that own them.**

Fandom activity for games, anime, and light novels shows up in player counts, list sites, sales charts, and social posts weeks before it shows up in a company's earnings. That data is scattered across Steam, MyAnimeList, Oricon, and Reddit, and much of it is in Japanese. IP-Alpha collects it on a schedule and links each IP to the publicly traded company that owns it, for example *Elden Ring → FromSoftware → KADOKAWA (9468.T)*. It then reduces the data to one **Hype Score** per IP and per company, and flags **divergence**: hype rising while the stock lags the market.

> Research and portfolio project. **Not investment advice.**

<!-- Add a dashboard screenshot here, e.g. ![Dashboard](docs/dashboard.png) -->

## Status

| Phase | Scope | State |
| --- | --- | --- |
| 1. Data pipeline MVP | Ingestion, Postgres, Hype Score, divergence, read API, dashboard | ✅ Done |
| 2. Agentic autonomy | LangGraph pipeline, translation/normalization, IP-to-equity mapper, RAG chat ("Why is Kadokawa up 4% today?") | ⏳ Next |
| 3. Production hardening | Redis rate limiting, arq workers, Pydantic validate-and-repair, Langfuse tracing, CI/CD | Planned |
| 4. Predictive modeling | scikit-learn models with walk-forward validation and a backtest | Planned |

## Architecture

```mermaid
flowchart LR
  subgraph sources [Sources]
    Steam["Steam Web API + SteamSpy"]
    MAL["MyAnimeList (Jikan / MAL API)"]
    Oricon["Oricon weekly ranking"]
    Social["Reddit + Bluesky"]
    YF["Yahoo Finance"]
  end
  Worker["Worker (APScheduler, JST)"] --> sources
  sources --> DB[("Postgres + pgvector")]
  DB --> Score["Scoring job: Hype Score + divergence"]
  Score --> DB
  API["FastAPI"] --> DB
  Web["Next.js dashboard"] --> API
```

Phase 2 adds LangGraph agents between ingestion and storage. They will detect language, translate, map IPs to companies, and write theses, with an LLM served locally by Ollama.

## How the scores work

**Hype Score (per IP, per day).** Each metric is turned into a z-score against that IP's own trailing 28-day baseline. The baseline excludes the current day, so there's no look-ahead. Each z-score is clipped to ±4, then combined with these weights:

| Component | Metric | Weight |
| --- | --- | --- |
| Players | Daily peak concurrent players (Steam) | 0.30 |
| Reviews | New reviews per day | 0.15 |
| MyAnimeList | Change in members and score | 0.20 |
| Oricon | Estimated weekly units sold | 0.15 |
| Social | Mentions on Reddit and Bluesky | 0.20 |

If an IP has no data for a component (Nintendo games aren't on Steam, for example), the remaining weights are rescaled to sum to 1.

**Company hype** is the average of its IPs' Hype Scores, weighted by the ownership weights in [`seeds/ips.yaml`](seeds/ips.yaml). Those weights are documented estimates, because licensing deals and anime production committees make ownership fuzzy.

**Divergence** = company hype − z-score of the stock's 5-day return minus the Nikkei 225's 5-day return. It's computed on Tokyo Stock Exchange trading days, with all timestamps converted to Japan time. A high value means hype is running ahead of the price.

Each metric needs about a week of its own history before it gets a score, so hype columns stay empty for the first few days after you start collecting data.

## Tracked universe

Phase 1 tracks 5 companies and 29 hand-curated IPs. The full list is in [`seeds/`](seeds/).

| Company | Ticker | Example IPs |
| --- | --- | --- |
| KADOKAWA | 9468.T | Elden Ring, Armored Core VI, Re:Zero, KonoSuba, Mushoku Tensei |
| Sony Group | 6758.T | Helldivers 2, Ghost of Tsushima, Demon Slayer, Solo Leveling |
| Capcom | 9697.T | Monster Hunter Wilds, Street Fighter 6, Resident Evil 4 |
| Square Enix | 9684.T | Final Fantasy XIV, FF VII Rebirth, NieR:Automata, Dragon Quest XI |
| Nintendo | 7974.T | Pokémon, Mario, Zelda, Animal Crossing, Splatoon |

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, pandas, APScheduler
- **Database:** PostgreSQL 16 with pgvector (an HNSW index on document embeddings, which RAG will use in Phase 2)
- **Frontend:** Next.js 16 (App Router, Cache Components), React 19, Tailwind CSS 4, Recharts
- **Infrastructure:** Docker Compose (Postgres, Redis, API, worker, web)

## Getting started

### Option A: full stack in Docker

```bash
cp .env.example .env        # optional: add Reddit / Bluesky / MAL credentials
docker compose up --build
```

- Dashboard: http://localhost:3000
- API docs: http://localhost:8000/docs

The API container runs migrations and loads the seed data on startup. The worker container runs ingestion on a schedule.

### Option B: local development

Start only the database in Docker:

```bash
docker compose up -d db     # Postgres on host port 5433
```

Set up the backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

alembic upgrade head
ipalpha seed                # load companies and IPs
ipalpha ingest all          # one pass over every source
ipalpha score               # compute Hype Scores and divergence
uvicorn ipalpha.api.main:app --reload
```

In a second terminal, start the frontend:

```bash
cd web
npm install
npm run dev                 # reads API_URL, default http://localhost:8000
```

### CLI

```text
ipalpha seed                       Load seeds/*.yaml (safe to re-run)
ipalpha ingest <source|all>        steam | steam_ccu | mal | oricon | reddit | bluesky | prices
ipalpha score                      Rebuild all hype and divergence rows
ipalpha worker                     Run the scheduler (hourly player counts, daily sources, scoring at 17:00 JST)
```

## Configuration

All settings are environment variables; see [`.env.example`](.env.example). Every credential is optional, and a source without credentials logs a warning and is skipped.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Defaults to `postgresql+psycopg://ipalpha:ipalpha@localhost:5433/ipalpha` |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Reddit "script" app credentials |
| `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Bluesky app password |
| `MAL_CLIENT_ID` | Official MyAnimeList API (Jikan is used when unset) |
| `API_URL` | Where the dashboard server reaches the API |

## Data sources and etiquette

- **Steam:** uses the official Web API for player counts. Review totals come from the store endpoint when it responds, otherwise from SteamSpy. SteamDB is not scraped, because its terms forbid it.
- **MyAnimeList:** Jikan v4, or the official API when `MAL_CLIENT_ID` is set.
- **Oricon:** one request per run to a page that robots.txt allows. The parser is covered by a test against a saved copy of the page, so a layout change fails CI instead of silently producing bad data.
- **Reddit and Bluesky:** posts are matched to IPs by alias, with word boundaries and case-sensitive matching for short aliases. Posts that match no IP are kept for the Phase 2 mapper.
- **Yahoo Finance:** split-adjusted daily prices for each ticker and the `^N225` benchmark.

All clients pause between requests. Phase 3 replaces those pauses with Redis-backed per-source rate limiters.

## API

| Endpoint | Description |
| --- | --- |
| `GET /health` | Liveness check, including a database ping |
| `GET /companies` | Each company's latest close, 5-day return, hype, and divergence |
| `GET /companies/{ticker}` | Company detail, with tracked IPs ranked by hype |
| `GET /companies/{ticker}/series?from=&to=` | Daily close, hype, and divergence (default: last 180 days) |
| `GET /ips/{id}` | IP detail: owners, latest raw metrics, hype history |

## Project layout

```text
backend/
  ipalpha/
    config.py, cli.py, jobs.py, worker.py
    db/          SQLAlchemy models and session
    alembic/     migrations
    ingestion/   one module per source, plus shared helpers and alias matching
    scoring/     hype.py, divergence.py, job.py
    api/         FastAPI app, routers, schemas
  tests/         unit tests plus a Postgres integration test
web/             Next.js dashboard
seeds/           companies.yaml, ips.yaml (IP -> owner mappings)
docker-compose.yml
```

## Testing

```bash
cd backend
pytest        # unit tests, plus an integration test against Postgres on port 5433
ruff check .
```

The integration test creates and drops a throwaway `ipalpha_test` database. It runs the migration, seed, scoring job, and API against synthetic data, and is skipped if Postgres isn't reachable.

## Known limitations

- Hype Scores can only cover history collected since you started ingesting. Phase 4 backfills where the sources allow.
- Ownership weights are judgment calls, and committee-funded anime makes them especially rough.
- Social signals are mention counts only. Sentiment scoring arrives with the Phase 2 language model.
- The Oricon chart is public only for the top entries, so mid-list titles are missed.
