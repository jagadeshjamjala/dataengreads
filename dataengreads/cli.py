"""Command-line entry point: python -m dataengreads <command>."""
import argparse

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dataengreads", description="Data engineering case studies from company blogs.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    args = parser.parse_args(argv)
    print(f"{args.command}: not implemented yet")
    return 0
