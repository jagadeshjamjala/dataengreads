"""Command-line entry point: python -m dataengreads <command>."""
import argparse

from dataengreads.config import load_blogs
from dataengreads.db import connect
from dataengreads.discover import discover
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


def run_discover(args: argparse.Namespace) -> int:
    con, client = connect(), PoliteClient()
    blogs = load_blogs()["blogs"]
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


HANDLERS = {"discover": run_discover}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dataengreads", description="Data engineering case studies from company blogs.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text)
        if name == "discover":
            cmd.add_argument("--refresh", action="store_true", help="re-discover blogs that already have a feed or sitemap")
    args = parser.parse_args(argv)
    if args.command not in HANDLERS:
        print(f"{args.command}: not implemented yet")
        return 0
    return HANDLERS[args.command](args)
