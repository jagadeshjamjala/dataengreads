"""Command-line entry point: python -m dataengreads <command>."""
import argparse
import os
from urllib.parse import urlparse

from dataengreads.classify import DEFAULT_MODEL, classify, classify_post, evaluate, offline_classify
from dataengreads.collect import collect
from dataengreads.config import load_blogs, load_taxonomy
from dataengreads.db import connect
from dataengreads.discover import discover
from dataengreads.fetch import fetch
from dataengreads.http import PoliteClient

COMMANDS = {
    "discover": "Find each blog's RSS/Atom feed and sitemap",
    "collect": "Collect post URLs from feeds and sitemaps",
    "fetch": "Fetch posts and extract article text",
    "classify": "Classify and tag posts with an LLM",
    "build": "Build the static site",
    "all": "Run the full pipeline",
    "demo": "Offline run on fixtures, no keys needed",
    "eval": "Measure classifier accuracy on tests/fixtures/eval.yaml",
}


def _client(blogs: list[dict]) -> PoliteClient:
    client = PoliteClient()
    client.delays = {urlparse(b["url"]).netloc: b["delay"] for b in blogs if "delay" in b}
    return client


def run_discover(args: argparse.Namespace) -> int:
    blogs = load_blogs()["blogs"]
    con, client = connect(), _client(blogs)
    missing = discover(con, client, blogs, refresh=args.refresh)
    rows = con.execute(
        "SELECT company, feed_url, sitemap_url FROM blogs WHERE list_contains(?, company) ORDER BY company",
        [[b["company"] for b in blogs]],
    ).fetchall()
    for company, feed, sitemap in rows:
        print(f"  {company:<14} feed: {feed or '-'}   sitemap: {sitemap or '-'}")
    for blog in missing:
        print(f"  NOT FOUND {blog['company']} {blog['url']}  (add `feed:` or `sitemap:` in blogs.yaml)")
    print(f"{len(blogs) - len(missing)}/{len(blogs)} blogs have a feed or sitemap, {client.requests} requests made")
    return 0


def run_collect(args: argparse.Namespace) -> int:
    blogs = load_blogs()["blogs"]
    con, client = connect(), _client(blogs)
    counts = collect(con, client, blogs, feeds_only=args.feeds_only)
    for company, new in counts.items():
        print(f"  {company:<14} {'not discovered: run discover first' if new is None else f'{new} new'}")
    total = con.execute("SELECT count(*) FROM posts").fetchone()[0]
    print(f"{sum(n or 0 for n in counts.values())} new URLs, {total} posts stored, {client.requests} requests made")
    return 0


def run_fetch(args: argparse.Namespace) -> int:
    blogs = load_blogs()["blogs"]
    con, client = connect(), _client(blogs)
    counts = fetch(con, client, blogs, limit=args.limit, retry_failed=args.retry_failed)
    done = sum(counts.values())
    failed = f"{counts['failed'] / done:.0%}" if done else "0%"
    left = con.execute("SELECT count(*) FROM posts WHERE status = 'new'").fetchone()[0]
    print(f"fetched {counts['fetched']}, too short {counts['too_short']}, failed {counts['failed']} ({failed}); "
          f"{left} new posts left, {client.requests} requests made")
    return 0


HAIKU_PRICE = (0.10, 0.50)  # $ per 1M input / output tokens, claude-haiku-5-5 (prompts <= 100K tokens)


def _anthropic():
    import anthropic  # only real runs need the SDK and a key
    return anthropic.Anthropic()


def run_classify(args: argparse.Namespace) -> int:
    con = connect()
    counts = classify(con, load_taxonomy(), client=None if args.offline else _anthropic(),
                      limit=args.limit, reclassify=args.reclassify)
    cost = (counts["input_tokens"] * HAIKU_PRICE[0] + counts["output_tokens"] * HAIKU_PRICE[1]) / 1e6
    left = con.execute("SELECT count(*) FROM posts p LEFT JOIN classifications c USING (url) "
                       "WHERE p.status = 'fetched' AND c.url IS NULL").fetchone()[0]
    print(f"classified {counts['classified']}, failed {counts['failed']}; {left} fetched posts left")
    if not args.offline:
        print(f"tokens: {counts['input_tokens']:,} in, {counts['output_tokens']:,} out "
              f"(~${cost:.4f} at Haiku 5.5 rates)")
    return 0


def run_eval(args: argparse.Namespace) -> int:
    taxonomy = load_taxonomy()
    if args.offline:
        fn = lambda post: offline_classify(post, taxonomy)  # noqa: E731
    else:
        client, model = _anthropic(), os.environ.get("DATAENGREADS_MODEL", DEFAULT_MODEL)
        fn = lambda post: classify_post(client, model, post, taxonomy)[0]  # noqa: E731
    r = evaluate(connect(), fn)
    for m in r["misses"]:
        print(f"  MISS {m['url']}  want {m['want']}  got {m['got']}")
    for url in r["missing"]:
        print(f"  NOT IN DB {url}  (fetch it first)")
    if r["unreviewed"]:
        print(f"  WARNING: {r['unreviewed']} eval labels still have reviewed: false")
    print(f"{r['n']} posts: is_de {r['is_de']:.0%}, primary_problem {r['primary_problem']:.0%} (on DE posts)")
    return 0


HANDLERS = {"discover": run_discover, "collect": run_collect, "fetch": run_fetch,
            "classify": run_classify, "eval": run_eval}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dataengreads", description="Data engineering case studies from company blogs.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text)
        if name == "discover":
            cmd.add_argument("--refresh", action="store_true", help="re-discover blogs that already have a feed or sitemap")
        if name == "collect":
            cmd.add_argument("--feeds-only", action="store_true", help="skip the sitemap backfill (nightly runs)")
        if name == "fetch":
            cmd.add_argument("--limit", type=int, default=1000, help="posts to fetch this run (default 1000)")
            cmd.add_argument("--retry-failed", action="store_true", help="also retry posts that failed before")
        if name in ("classify", "eval"):
            cmd.add_argument("--offline", action="store_true", help="keyword heuristic, no API key needed")
        if name == "classify":
            cmd.add_argument("--limit", type=int, default=100, help="posts to classify this run (default 100)")
            cmd.add_argument("--reclassify", action="store_true", help="also redo posts already classified")
    args = parser.parse_args(argv)
    if args.command not in HANDLERS:
        print(f"{args.command}: not implemented yet")
        return 0
    return HANDLERS[args.command](args)
