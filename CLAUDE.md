# DataEngReads

A searchable library of real-world data engineering case studies from company engineering
blogs (Netflix, Uber, Airbnb, Stripe, ...). Sibling of DataEngTube: same pattern, different source.

Collect posts from engineering blogs → extract article text → an LLM keeps only data engineering
posts and tags each one by the problem solved, the stack, scale and key trade-offs → rank →
publish a static site that refreshes nightly.

The build plan is in `docs/PLAN.md`. Work one phase at a time, in order, and tick its boxes
when its "Done when" criteria pass.

## Stack (do not change without asking)
- Python 3.12, modules in `dataengreads/`, CLI `python -m dataengreads <command>`
- DuckDB at `data/dataengreads.duckdb`
- `requests`, `feedparser` (RSS/Atom), `trafilatura` (article text extraction), `pyyaml`, `anthropic`
- Static site: one `index.html` (vanilla JS, no framework) + generated `data.js`; GitHub Pages
- `pytest`; GitHub Actions for the nightly refresh

## Architecture
```
config/blogs.yaml ─► discover (find RSS/sitemap) ─► collect (feeds + sitemap backfill)
     ─► fetch (article HTML → clean text) ─► classify (LLM) ─► rank ─► build ─► site/
```
Tables: `blogs`, `posts` (url, title, published, raw text), `classifications` (LLM output).
Raw facts and LLM output are separate so posts can be re-classified without re-fetching.

## Rules
- **Copyright:** never publish article text. The site shows title, company, date, our own
  LLM-written summary and tags, and links to the original post. Stored text is for classification only.
- **Be a polite crawler:** honour robots.txt, at most 1 request/second per domain, a descriptive
  User-Agent with the repo URL, cache fetched HTML, never re-fetch a post that's already stored.
- **Never call real websites or APIs in tests.** Use saved HTML/XML fixtures in `tests/fixtures/`
  and a mocked Anthropic client.
- Every step is idempotent and resumable.
- Secrets only from env (`ANTHROPIC_API_KEY`); model from `DATAENGREADS_MODEL`, default
  `claude-haiku-5-5`; forced tool call for structured output.
- Send at most the first ~6,000 words of an article to the LLM.
- Escape all scraped text in the site.
- Run `pytest -q` after each change; don't mark a phase done with failing tests.

## Commands
- `pip install -r requirements.txt`
- `pytest -q`
- `python -m dataengreads demo` — offline run on fixtures
- `python -m dataengreads all` — real run
