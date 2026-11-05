# IP-Alpha

**Measuring fandom hype for Japanese entertainment IPs and comparing it with the stock prices of the companies that own them.**

Fandom activity for games, anime, and light novels shows up in player counts, list sites, sales charts, and social posts weeks before it shows up in a company's earnings. That data is scattered across Steam, MyAnimeList, Oricon, and Reddit, and much of it is in Japanese. IP-Alpha collects it on a schedule and links each IP to the publicly traded company that owns it, for example *Elden Ring → FromSoftware → KADOKAWA (9468.T)*. It then reduces the data to one **Hype Score** per IP and per company, and flags **divergence**: hype rising while the stock lags the market.

> Research and portfolio project. **Not investment advice.**

<!-- Add a dashboard screenshot here, e.g. ![Dashboard](docs/dashboard.png) -->

## Status

| Phase | Scope | State |
| --- | --- | --- |
| 1. Data pipeline MVP | Ingestion, Postgres, Hype Score, divergence, read API, dashboard | ✅ Done |
| 2. Agentic autonomy | LangGraph pipeline, translation/normalization, IP-to-equity mapper, analyst theses, RAG chat ("Why is Kadokawa up 4% today?") | ✅ Done |
| 3. Production hardening | Redis rate limiting, arq workers, dead-letter replay, Langfuse tracing, CI/CD | ⏳ Next |
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
  DB --> Agents["LangGraph agents: normalize, map IPs, embed"]
  Agents --> DB
  DB --> Score["Scoring job: Hype Score + divergence"]
  Score --> Analyst["Analyst Agent: cited theses"]
  Analyst --> DB
  API["FastAPI"] --> DB
  API --> Chat["Chat graph (RAG)"]
  Chat --> DB
  Web["Next.js dashboard + chat"] --> API
  Ollama[("Ollama: qwen2.5:7b, bge-m3")] -.-> Agents
  Ollama -.-> Analyst
  Ollama -.-> Chat
```

## Agents

All model calls go through one client ([`llm.py`](backend/ipalpha/llm.py)) that talks to any OpenAI-compatible server. By default that's a local Ollama running `qwen2.5:7b` for text and `bge-m3` for multilingual embeddings. Structured outputs are requested as a JSON schema and validated with Pydantic. On a validation failure, the model is shown its errors and asked again, up to twice.

**Pipeline graph** ([`pipeline_graph.py`](backend/ipalpha/agents/pipeline_graph.py)). The stages run in this order: `fetch → normalize → map_ip → embed → score → analyze`. Each stage reads from and commits to the database, so any subset can run on its own. A failing stage is recorded and the graph moves on. Items the model can't handle go to a `dead_letters` table instead of crashing the run.

- **Normalization Agent.** It detects the language with lingua (kana is treated as decisive for Japanese) and translates non-English posts. Its prompt includes a glossary of fandom slang such as アニメ化, 神ゲー, 爆死, and 重版. It extracts the titles mentioned, an event type, sentiment from −1 to 1, and a one-line summary. A post stored once per matched IP gets a single model call.
- **IP-to-Equity Mapper.** For posts that matched no IP at ingestion, it tries three steps in order: the alias matcher, `bge-m3` similarity against every name and alias of each tracked IP, and finally the LLM choosing among the closest candidates (or "none"). It never invents an owner. Unresolved titles go to a review queue (`GET /review/ip-candidates`). For example, ナイトレイン is mapped to Elden Ring and "FF7R" to FF7 Rebirth, while 薬屋のひとりごと is queued for review because it isn't tracked.
- **Analyst Agent.** When a company's |divergence| reaches `ANALYST_MIN_DIVERGENCE`, it retrieves the most relevant posts from the past week and writes a `Thesis`. A thesis has a headline, stance, claim, evidence, risks, confidence, and a disclaimer. Evidence that cites a document the model wasn't shown is dropped and logged.

**Chat graph** ([`chat_graph.py`](backend/ipalpha/agents/chat_graph.py)). The steps are `understand → retrieve → answer → verify`.

- **Understand** finds companies by name, Japanese name, ticker, or any IP they own. Follow-up questions fall back to the previous turn. The LLM only picks a company when nothing matches.
- **Retrieve** gathers prices, Hype Scores, the latest thesis, and a pgvector search over posts.
- **Answer** streams the reply over server-sent events.
- **Verify** checks every `[id]` citation against the posts that were actually retrieved.

The prompt spells out directions ("down", "hype ahead of price") because a 7B model misreads signs. It also tells the model to correct false premises. For example, "Why is Kadokawa up 4%?" gets the answer "it's down 1.3%".

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
- **Agents:** LangGraph, OpenAI-compatible client, Ollama (`qwen2.5:7b`, `bge-m3`), lingua language detection
- **Database:** PostgreSQL 16 with pgvector (an HNSW index on document embeddings for RAG)
- **Frontend:** Next.js 16 (App Router, Cache Components), React 19, Tailwind CSS 4, Recharts
- **Infrastructure:** Docker Compose (Postgres, Redis, API, worker, web)

## Getting started

The agents need [Ollama](https://ollama.com) running on the host (about 6 GB of GPU memory for both models):

```bash
ollama pull qwen2.5:7b
ollama pull bge-m3
```

Without it, ingestion, scoring, and the dashboard still work. The agent stages log that the model server is unreachable and skip their work, and the chat shows an error.

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
ipalpha enrich              # translate, normalize, map, and embed new posts (needs Ollama)
ipalpha score               # compute Hype Scores and divergence
ipalpha analyze             # write theses for divergent companies
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
ipalpha enrich                     Normalize, map, and embed up to NORMALIZE_BATCH_SIZE new posts
ipalpha analyze [--ticker T] [--force]
                                   Write theses (--force ignores the divergence threshold)
ipalpha pipeline [--stages a,b]    Run the agent graph (fetch,normalize,map_ip,embed,score,analyze)
ipalpha chat "question"            Ask the analyst from the terminal
ipalpha worker                     Run the scheduler
```

The worker runs on Japan time:

- Steam player counts every hour at :05.
- Enrichment every hour at :30, working through new posts in batches.
- The other sources each morning.
- Prices at 16:30, scoring at 17:00, and theses at 17:15.

## Configuration

All settings are environment variables; see [`.env.example`](.env.example). Every credential is optional, and a source without credentials logs a warning and is skipped.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Defaults to `postgresql+psycopg://ipalpha:ipalpha@localhost:5433/ipalpha` |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Reddit "script" app credentials |
| `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Bluesky app password |
| `MAL_CLIENT_ID` | Official MyAnimeList API (Jikan is used when unset) |
| `LLM_BASE_URL`, `LLM_API_KEY` | OpenAI-compatible endpoint; defaults to Ollama at `http://localhost:11434/v1` |
| `LLM_MODEL`, `EMBEDDING_MODEL` | Default `qwen2.5:7b` and `bge-m3` (1024 dimensions, matching the schema) |
| `NORMALIZE_BATCH_SIZE` | Distinct posts processed per enrichment run (default 100) |
| `ANALYST_MIN_DIVERGENCE` | Absolute divergence that triggers a thesis (default 1.0) |
| `API_URL` | Where the dashboard server reaches the API |

## Data sources and etiquette

- **Steam:** uses the official Web API for player counts. Review totals come from the store endpoint when it responds, otherwise from SteamSpy. SteamDB is not scraped, because its terms forbid it.
- **MyAnimeList:** Jikan v4, or the official API when `MAL_CLIENT_ID` is set.
- **Oricon:** one request per run to a page that robots.txt allows. The parser is covered by a test against a saved copy of the page, so a layout change fails CI instead of silently producing bad data.
- **Reddit and Bluesky:** posts are matched to IPs by alias, with word boundaries and case-sensitive matching for short aliases. Posts that match no IP are kept for the IP mapper agent.
- **Yahoo Finance:** split-adjusted daily prices for each ticker and the `^N225` benchmark.

All clients pause between requests. Phase 3 replaces those pauses with Redis-backed per-source rate limiters.

## API

| Endpoint | Description |
| --- | --- |
| `GET /health` | Liveness check, including a database ping |
| `GET /companies` | Each company's latest close, 5-day return, hype, and divergence |
| `GET /companies/{ticker}` | Company detail, with tracked IPs ranked by hype |
| `GET /companies/{ticker}/series?from=&to=` | Daily close, hype, and divergence (default: last 180 days) |
| `GET /companies/{ticker}/theses?limit=` | Latest analyst theses, with cited evidence |
| `GET /ips/{id}` | IP detail: owners, latest raw metrics, hype history |
| `POST /chat` | `{message, history}` → server-sent events: `status`, `sources`, `token`…, `citations`, `done` |
| `GET /review/ip-candidates` | Titles the mapper couldn't tie to a tracked IP, most-mentioned first |

The dashboard proxies `/chat` through its own `/api/chat` route, so the browser never needs the API's address.

## Project layout

```text
backend/
  ipalpha/
    config.py, cli.py, jobs.py, worker.py
    llm.py       OpenAI-compatible client: validated structured output, streaming, embeddings
    agents/      normalize, mapper, embed, analyst, tools (retrieval), pipeline_graph, chat_graph
    db/          SQLAlchemy models and session
    alembic/     migrations
    ingestion/   one module per source, plus shared helpers and alias matching
    scoring/     hype.py, divergence.py, job.py
    api/         FastAPI app, routers, schemas
  tests/         unit tests plus a Postgres integration test (agents run against a fake LLM)
web/             Next.js dashboard and chat
seeds/           companies.yaml, ips.yaml (IP -> owner mappings)
docker-compose.yml
```

## Testing

```bash
cd backend
pytest        # unit tests, plus an integration test against Postgres on port 5433
ruff check .
```

The integration test creates and drops a throwaway `ipalpha_test` database. It runs the migrations, seed, scoring job, agent pipeline, chat stream, and API against synthetic data, and is skipped if Postgres isn't reachable. The agents use a fake LLM with deterministic embeddings there, so tests don't need Ollama.

## Known limitations

- Hype Scores can only cover history collected since you started ingesting. Phase 4 backfills where the sources allow.
- Ownership weights are judgment calls, and committee-funded anime makes them especially rough.
- The Hype Score still uses mention counts only. Each post now has a sentiment score, which the chat and theses use, but it isn't part of the score formula yet.
- A 7B local model is fast and free but imperfect. It occasionally mistranslates a title, picks an odd event type, or labels a stance loosely. Every number it writes comes from the database, and its citations are checked, but the prose is a draft rather than research.
- Throughput is roughly 5–10 seconds per post on a consumer GPU, which is why enrichment runs in hourly batches.
- The Oricon chart is public only for the top entries, so mid-list titles are missed.
