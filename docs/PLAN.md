# DataEngReads build plan

Each phase: `/clear` → `/next-phase` → approve the plan → let it build and test → `/review` → commit.

---

## Phase 1 — Skeleton
- [x] Layout: `dataengreads/`, `config/`, `tests/fixtures/`, `data/`, `site/`
- [x] `requirements.txt` (duckdb, pyyaml, requests, feedparser, trafilatura, anthropic, pytest), `.gitignore`
- [x] CLI with stub subcommands: discover, collect, fetch, classify, build, all, demo, eval
- [x] Config loaders for `blogs.yaml` and `taxonomy.yaml` with validation

**Done when:** `python -m dataengreads --help` works and config tests pass.

## Phase 2 — Storage
- [x] Tables `blogs`, `posts` (url PK, blog, title, published_at, author, word_count, text, html_hash, fetched_at, status), `classifications`
- [x] `upsert` helper; tests on a temp DB

**Done when:** upserting twice doesn't duplicate rows.

## Phase 3 — Discover feeds and sitemaps
- [x] Polite HTTP client: User-Agent with repo URL, robots.txt check, 1 req/sec per domain, retries, on-disk cache
- [x] From each homepage: find RSS/Atom via `<link rel="alternate">` and common paths (`/feed`, `/rss`, `/atom.xml`); Medium blogs use `medium.com/feed/<publication>`
- [x] Find sitemaps from robots.txt and `/sitemap.xml`
- [x] Report blogs with no feed or sitemap so you can fix `blogs.yaml`

**Done when:** tests pass on saved fixtures; a real run lists a feed or sitemap for most blogs.

*Your input:* fix the blogs it can't find. Add companies you rate, especially fintech.

## Phase 4 — Collect post URLs
- [x] From feeds: recent posts (title, url, date)
- [x] Backfill from sitemaps: post URLs since `backfill_since`, filtered to blog-post paths
- [x] Store as `status = 'new'`; skip known URLs

**Done when:** a real run reports new URLs per blog; a re-run adds 0.

## Phase 5 — Fetch and extract article text
- [x] Fetch HTML for `new` posts (polite client); extract title, date, author and main text with trafilatura
- [x] Drop posts under `min_words`; mark `fetched` / `too_short` / `failed`
- [x] Resumable: `--limit N` per run

**Done when:** extraction tests pass on 3–4 saved blog pages (Medium, WordPress, custom); a real run of 200 posts has under 5% failures.

## Phase 6 — LLM classification
- [x] One post per request (articles are long); send title, company, and the first ~6,000 words
- [x] Forced tool call returning: is_de, confidence, primary_problem, problems, post_type, level, stack, scale, key_decisions (3), summary, interview_angle
- [x] Normalize stack names (one alias map, e.g. "Apache Kafka" → "Kafka")
- [x] `--offline` keyword heuristic for demo/tests
- [x] **Eval set:** `tests/fixtures/eval.yaml`, ~30 posts you label by hand; `python -m dataengreads eval` reports is_de and primary_problem accuracy

**Done when:** mocked tests pass; is_de accuracy ≥ 90% on your eval set. Check the cost of 100 posts before the full run.

*Your input:* label the eval set and read 10 summaries. Are they accurate, and in our own words?

## Phase 7 — Ranking
- [x] Score: post_type (case studies and postmortems first), depth (word count, scale stated, number of decisions), tier, confidence, light freshness boost; announcements penalized
- [x] Optional signal: HN discussion. Search the Algolia HN API for the post URL and store points/comments (same idea as DataEngTube's recommendations)

**Done when:** the top 20 look like posts you'd send to a colleague.

## Phase 8 — Static site
- [x] Cards: company, title, date, problem tags, stack chips, scale line, our summary, "Read on <company> blog ↗"
- [x] Expand a card to see key decisions and the interview angle
- [x] Filters: problem, company, industry, stack, post type; search; sort by best, newest, most discussed
- [x] Stack pages: "Kafka in production" lists every post using Kafka
- [x] **Interview mode:** pick a system-design question and see the posts that answer it
- ~~Link to DataEngTube for the same technology~~ (dropped: unrelated project)
- [x] Open Graph tags + 1200×630 preview image; light/dark; mobile

**Done when:** `demo` builds a site that works from `file://` and on phone width.

## Phase 9 — Automation and launch
- [ ] Nightly GitHub Action: collect (feeds only) → fetch → classify → build → deploy Pages
- [ ] Monthly sitemap backfill job (heavier, run less often)
- [ ] README, domain, launch post

**Done when:** a manual workflow run succeeds and the site is live.

## Phase 10 — Ideas for later
- [ ] "Ask the library": answer a design question with citations to posts
- [ ] Company pages: how a company's data stack changed over the years
- [ ] Weekly "best new case studies" digest for the newsletter
- [ ] Shared search across DataEngTube and DataEngReads
