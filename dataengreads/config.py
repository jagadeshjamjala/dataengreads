"""Load and validate config/blogs.yaml and config/taxonomy.yaml."""
import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
TIERS = (1, 2)


class ConfigError(ValueError):
    pass


def _read(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ConfigError(f"{Path(path).name}: expected a mapping at top level")
    return data


def _require(item, fields: tuple[str, ...], where: str) -> None:
    if not isinstance(item, dict):
        raise ConfigError(f"{where}: expected a mapping, got {item!r}")
    for field in fields:
        if item.get(field) in (None, ""):
            raise ConfigError(f"{where}: missing '{field}'")


def _check_defaults(defaults, name: str) -> None:
    _require(defaults, ("min_words", "backfill_since"), f"{name}: defaults")
    min_words = defaults["min_words"]
    if not isinstance(min_words, int) or isinstance(min_words, bool) or min_words <= 0:
        raise ConfigError(f"{name}: defaults.min_words must be a positive integer, got {min_words!r}")
    # YAML parses 2016-01-01 as a date; a quoted or malformed value stays a string.
    if not isinstance(defaults["backfill_since"], datetime.date):
        raise ConfigError(f"{name}: defaults.backfill_since must be a YYYY-MM-DD date")


def _check_blog(blog, where: str, seen: set) -> None:
    _require(blog, ("company", "url", "industry", "tier"), where)
    for field in ("url", "feed", "sitemap"):  # feed and sitemap are optional overrides
        if field in blog:
            url = urlparse(str(blog[field]))
            if url.scheme not in ("http", "https") or not url.netloc:
                raise ConfigError(f"{where}: {field} must be http(s), got {blog[field]!r}")
    delay = blog.get("delay", 1)
    if not isinstance(delay, (int, float)) or isinstance(delay, bool) or delay < 1:
        raise ConfigError(f"{where}: delay must be a number of seconds >= 1, got {delay!r}")
    if "post_prefix" in blog and not str(blog["post_prefix"]).startswith("/"):
        raise ConfigError(f"{where}: post_prefix must start with '/', got {blog['post_prefix']!r}")
    if blog["tier"] not in TIERS:
        raise ConfigError(f"{where}: tier must be one of {TIERS}, got {blog['tier']!r}")
    for key in (blog["company"].lower(), blog["url"].rstrip("/").lower()):
        if key in seen:
            raise ConfigError(f"{where}: duplicate company or url '{key}'")
        seen.add(key)


def load_blogs(path: Path = CONFIG_DIR / "blogs.yaml") -> dict:
    data = _read(path)
    name = Path(path).name
    defaults = data.get("defaults") or {}
    _check_defaults(defaults, name)
    blogs, seen = [], set()
    for i, blog in enumerate(data.get("blogs") or []):
        _check_blog(blog, f"{name}: blogs[{i}]", seen)
        blogs.append({**defaults, **blog})
    if not blogs:
        raise ConfigError(f"{name}: no blogs defined")
    return {"defaults": defaults, "blogs": blogs}


def load_taxonomy(path: Path = CONFIG_DIR / "taxonomy.yaml") -> dict:
    data = _read(path)
    name = Path(path).name
    seen = set()
    for i, problem in enumerate(data.get("problems") or []):
        where = f"{name}: problems[{i}]"
        _require(problem, ("id", "label", "hint"), where)
        if problem["id"] in seen:
            raise ConfigError(f"{where}: duplicate problem id '{problem['id']}'")
        seen.add(problem["id"])
    if not seen:
        raise ConfigError(f"{name}: no problems defined")
    for key in ("post_types", "levels", "extract"):
        values = data.get(key)
        if not isinstance(values, list) or not values:
            raise ConfigError(f"{name}: '{key}' must be a non-empty list")
    for i, question in enumerate(data.get("interview_questions") or []):
        where = f"{name}: interview_questions[{i}]"
        _require(question, ("q", "problems"), where)
        if unknown := set(question["problems"]) - seen:
            raise ConfigError(f"{where}: unknown problem id(s) {sorted(unknown)}")
    return data
