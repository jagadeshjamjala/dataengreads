"""Classify fetched posts with an LLM (one post per request, forced tool call) or an offline keyword heuristic."""
import datetime as dt
import html
import os
import re
from pathlib import Path

import anthropic
import yaml

from dataengreads.db import upsert

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_WORDS = 6000
TOOL_NAME = "classify_post"
EVAL_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eval.yaml"

SYSTEM = """You classify company engineering blog posts for a library of real-world data engineering case studies.
Data engineering means building or running systems that move, store, process, model or govern data:
pipelines, streaming, warehouses, lakehouses, CDC, orchestration, data quality, catalogs, feature stores.
Posts mainly about frontend, mobile, product, hiring, general backend or ML modeling (not its data) are not data engineering.
Write the summary, key decisions and interview angle in your own words; never copy sentences from the post.
Call the classify_post tool exactly once."""

# One map for every spelling we normalize to; keys are lower-case.
STACK_ALIASES = {
    "apache kafka": "Kafka", "kafka": "Kafka", "confluent kafka": "Kafka",
    "apache flink": "Flink", "flink": "Flink",
    "apache spark": "Spark", "spark": "Spark", "pyspark": "Spark", "spark sql": "Spark",
    "apache airflow": "Airflow", "airflow": "Airflow",
    "apache iceberg": "Iceberg", "iceberg": "Iceberg",
    "apache hudi": "Hudi", "hudi": "Hudi",
    "delta lake": "Delta Lake", "delta": "Delta Lake",
    "apache hive": "Hive", "hive": "Hive",
    "apache druid": "Druid", "druid": "Druid",
    "apache pinot": "Pinot", "pinot": "Pinot",
    "apache beam": "Beam", "beam": "Beam",
    "apache cassandra": "Cassandra", "cassandra": "Cassandra",
    "apache hadoop": "Hadoop", "hadoop": "Hadoop", "hdfs": "HDFS",
    "presto": "Presto", "prestodb": "Presto", "trino": "Trino",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "amazon s3": "S3", "aws s3": "S3", "s3": "S3",
    "google bigquery": "BigQuery", "bigquery": "BigQuery",
    "snowflake": "Snowflake",
    "amazon redshift": "Redshift", "redshift": "Redshift",
    "dbt": "dbt", "data build tool": "dbt",
    "debezium": "Debezium",
    "elasticsearch": "Elasticsearch", "elastic search": "Elasticsearch",
    "clickhouse": "ClickHouse",
    "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    "parquet": "Parquet", "apache parquet": "Parquet",
}


def normalize_stack(names: list[str]) -> list[str]:
    out = []
    for name in names:
        clean = STACK_ALIASES.get(name.strip().lower(), name.strip())
        if clean and clean not in out:
            out.append(clean)
    return out


def tool_schema(taxonomy: dict) -> dict:
    problems = [p["id"] for p in taxonomy["problems"]]
    text = {"type": "string"}
    return {
        "name": TOOL_NAME,
        "description": "Record the classification of one engineering blog post.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["is_de", "confidence", "primary_problem", "problems", "post_type", "level",
                         "stack", "scale", "key_decisions", "summary", "interview_angle"],
            "properties": {
                "is_de": {"type": "boolean", "description": "Is this mainly a data engineering post?"},
                "confidence": {"type": "number", "description": "0 to 1, how sure you are about is_de"},
                "primary_problem": {"type": "string", "enum": problems},
                "problems": {"type": "array", "items": {"type": "string", "enum": problems},
                             "description": "Up to 2 more problems besides the primary one"},
                "post_type": {"type": "string", "enum": taxonomy["post_types"]},
                "level": {"type": "string", "enum": taxonomy["levels"]},
                "stack": {"type": "array", "items": text,
                          "description": "Named products, frameworks, databases or languages used, e.g. Kafka, Spark, "
                                         "PostgreSQL. Not techniques, algorithms or generic descriptions "
                                         "(not 'union-find', 'ANN search', 'workflow orchestrator')."},
                "scale": {**text, "description": "One line of numbers if stated, e.g. '4 PB/day'; "
                                                 "else an empty string (never 'unknown' or a placeholder)"},
                "key_decisions": {"type": "array", "items": text, "description": "3 bullets: decision + trade-off"},
                "summary": {**text, "description": "2 sentences"},
                "interview_angle": {**text, "description": "One system-design question this post helps answer"},
            },
        },
    }


def _problem_guide(taxonomy: dict) -> str:
    return "\n".join(f"- {p['id']}: {p['label']} ({p['hint']})" for p in taxonomy["problems"])


def _scale(value: str) -> str:
    """The model sometimes writes a placeholder (even HTML-escaped, '&lt;UNKNOWN&gt;') instead of leaving it empty."""
    clean = html.unescape(value).strip()
    placeholder = re.sub(r"[^a-z/ ]", "", clean.lower()).strip()
    return "" if placeholder in ("", "unknown", "none", "n/a", "na", "not stated", "not specified") else clean


LEAKED_MARKUP = re.compile(r"</?(parameter|invoke|summary|interview_angle)\b")


def _row(url: str, out: dict, model: str) -> dict:
    primary = out["primary_problem"]
    return {
        "url": url,
        "is_de": bool(out["is_de"]),
        "confidence": max(0.0, min(1.0, float(out["confidence"]))),
        "primary_problem": primary,
        "problems": [p for p in dict.fromkeys(out["problems"]) if p != primary][:2],
        "post_type": out["post_type"],
        "level": out["level"],
        "stack": normalize_stack(out["stack"]),
        "scale": _scale(out["scale"]),
        "key_decisions": out["key_decisions"][:3],
        "summary": out["summary"].strip(),
        "interview_angle": out["interview_angle"].strip(),
        "model": model,
        "classified_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
    }


def classify_post(client, model: str, post: dict, taxonomy: dict) -> tuple[dict | None, object]:
    """(classifications row or None if the model didn't call the tool, usage)."""
    text = " ".join(post["text"].split()[:MAX_WORDS])
    prompt = (f"Problem ids:\n{_problem_guide(taxonomy)}\n\n"
              f"Company: {post['blog']}\nTitle: {post['title']}\n\n<article>\n{text}\n</article>")
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM,
        tools=[tool_schema(taxonomy)],
        # Forced tool calls work on Haiku 5.5; Opus 5.5 / Sonnet 5.5 / Fable reject them (400).
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": prompt}],
    )
    call = next((b for b in resp.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
    # ~0.5% of answers leak tool-call markup into a text field; treat as failed so the next run retries it.
    if call is None or LEAKED_MARKUP.search(str(call.input)):
        return None, resp.usage
    return _row(post["url"], call.input, model), resp.usage


# Offline heuristic: enough data words means DE; the problem whose keywords appear most wins.
DE_WORDS = ("pipeline", "kafka", "spark", "flink", "warehouse", "lakehouse", "etl", "streaming", "airflow",
            "data lake", "cdc", "schema", "batch", "ingestion", "iceberg", "parquet", "data quality", "dbt")
PROBLEM_WORDS = {
    "scale_ingestion": ("ingestion", "events per", "throughput"),
    "realtime": ("real-time", "realtime", "streaming", "flink", "low latency"),
    "cdc_sync": ("cdc", "change data capture", "debezium", "replication"),
    "late_data": ("late data", "watermark", "backfill", "out-of-order", "reprocess"),
    "cost": ("cost", "savings", "cheaper", "finops"),
    "performance": ("query performance", "latency", "index", "faster queries", "caching"),
    "quality": ("data quality", "incident", "outage", "postmortem", "data contract"),
    "lineage_catalog": ("lineage", "catalog", "metadata", "discovery"),
    "platform": ("platform", "self-serve", "orchestration", "airflow"),
    "migration": ("migration", "migrate", "legacy", "re-platform"),
    "modeling": ("data model", "metrics", "dimension", "experimentation"),
    "governance_privacy": ("privacy", "gdpr", "pii", "compliance", "access control"),
    "ml_data": ("feature store", "training data", "embedding", "machine learning"),
    "storage_formats": ("iceberg", "delta lake", "hudi", "parquet", "compaction", "table format"),
}


def offline_classify(post: dict, taxonomy: dict) -> dict:
    text = f"{post['title']} {post['text']}".lower()
    count = lambda words: sum(len(re.findall(re.escape(w), text)) for w in words)  # noqa: E731
    de_hits = count(DE_WORDS)
    ids = [p["id"] for p in taxonomy["problems"]]
    ranked = sorted(ids, key=lambda i: -count(PROBLEM_WORDS.get(i, ())))
    return {
        "url": post["url"],
        "is_de": de_hits >= 5,
        "confidence": 0.3,  # low on purpose: never mistaken for an LLM answer
        "primary_problem": ranked[0],
        "problems": ranked[1:3],
        "post_type": "case_study",
        "level": taxonomy["levels"][0],
        "stack": normalize_stack([w for w in ("kafka", "spark", "flink", "airflow", "iceberg", "dbt") if w in text]),
        "scale": "",
        "key_decisions": [],
        "summary": post["title"] or "",
        "interview_angle": "",
        "model": "offline",
        "classified_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
    }


TO_CLASSIFY = """
SELECT p.url, p.blog, p.title, p.text FROM posts p
LEFT JOIN classifications c USING (url)
WHERE p.status = 'fetched' AND (c.url IS NULL OR (c.model = 'offline' AND ?) OR ?)  -- real runs replace heuristic rows
ORDER BY p.published_at DESC NULLS LAST, p.url
LIMIT ?
"""


def classify(con, taxonomy: dict, client=None, model: str | None = None, limit: int = 100,
             reclassify: bool = False) -> dict:
    """Classify up to `limit` posts, saving each as it's done. client=None runs the offline heuristic."""
    model = model or os.environ.get("DATAENGREADS_MODEL", DEFAULT_MODEL)
    counts = {"classified": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0}
    for url, blog, title, text in con.execute(TO_CLASSIFY, [client is not None, reclassify, limit]).fetchall():
        post = {"url": url, "blog": blog, "title": title, "text": text}
        if client is None:
            row = offline_classify(post, taxonomy)
        else:
            try:
                row, usage = classify_post(client, model, post, taxonomy)
            except (anthropic.APIConnectionError, anthropic.InternalServerError):  # timeouts included
                counts["failed"] += 1  # SDK already retried; skip this post, the next run picks it up
                continue
            counts["input_tokens"] += usage.input_tokens
            counts["output_tokens"] += usage.output_tokens
        if row is None:
            counts["failed"] += 1
            continue
        upsert(con, "classifications", [row])
        counts["classified"] += 1
    return counts


def evaluate(con, classify_fn, path: Path = EVAL_PATH) -> dict:
    """is_de and primary_problem accuracy on hand-labelled posts. primary_problem only counts DE posts."""
    labels = yaml.safe_load(Path(path).read_text())["posts"]
    found = {r[0]: r for r in con.execute(
        "SELECT url, blog, title, text FROM posts WHERE list_contains(?, url) AND text IS NOT NULL",
        [[e["url"] for e in labels]]).fetchall()}
    de_ok = prob_ok = prob_n = 0
    misses, missing = [], []
    for e in labels:
        if e["url"] not in found:
            missing.append(e["url"])
            continue
        url, blog, title, text = found[e["url"]]
        got = classify_fn({"url": url, "blog": blog, "title": title, "text": text}) or \
            {"is_de": None, "primary_problem": None}  # no tool call counts as a miss
        de_ok += got["is_de"] == e["is_de"]
        if e["is_de"]:
            prob_n += 1
            prob_ok += got["primary_problem"] == e["primary_problem"]
        if got["is_de"] != e["is_de"] or (e["is_de"] and got["primary_problem"] != e["primary_problem"]):
            misses.append({"url": url, "want": (e["is_de"], e.get("primary_problem")),
                           "got": (got["is_de"], got["primary_problem"])})
    n = len(labels) - len(missing)
    return {"n": n, "is_de": de_ok / n if n else 0.0, "primary_problem": prob_ok / prob_n if prob_n else 0.0,
            "misses": misses, "missing": missing, "unreviewed": sum(not e.get("reviewed") for e in labels)}
