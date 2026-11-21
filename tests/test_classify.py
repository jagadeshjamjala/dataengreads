from types import SimpleNamespace

import pytest
import yaml

from dataengreads.classify import (MAX_WORDS, TOOL_NAME, classify, evaluate, normalize_stack, offline_classify,
                                   tool_schema)
from dataengreads.config import load_taxonomy
from dataengreads.db import connect, upsert

TAX = load_taxonomy()
DE_TEXT = ("We rebuilt our ingestion pipeline on Kafka and Flink. Streaming CDC with Debezium feeds the "
           "lakehouse in Iceberg; compaction and schema evolution kept the warehouse fast. ") * 40
WEB_TEXT = "We redesigned the checkout button and improved our React components for mobile users. " * 60

ANSWER = {
    "is_de": True, "confidence": 0.9, "primary_problem": "realtime", "problems": ["realtime", "cdc_sync"],
    "post_type": "case_study", "level": "advanced", "stack": ["Apache Kafka", "kafka", "Apache Flink"],
    "scale": " 2M events/sec ", "key_decisions": ["a", "b", "c", "d"], "summary": "S.", "interview_angle": "Q?",
}


class FakeAnthropic:
    def __init__(self, answer=ANSWER):
        self.answer, self.calls = answer, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200)
        if self.answer is None:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="no")], usage=usage)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=TOOL_NAME, input=self.answer)],
                               usage=usage)


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    upsert(con, "posts", [
        {"url": "https://a/de", "blog": "Netflix", "title": "Streaming at scale", "text": DE_TEXT, "status": "fetched"},
        {"url": "https://a/web", "blog": "Netflix", "title": "Checkout redesign", "text": WEB_TEXT, "status": "fetched"},
        {"url": "https://a/short", "blog": "Netflix", "title": "Short", "text": None, "status": "too_short"},
    ])
    return con


def test_request_is_forced_tool_call_with_truncated_text(con):
    fake = FakeAnthropic()
    classify(con, TAX, client=fake, model="m")
    req = fake.calls[0]
    assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert req["tools"][0]["strict"] is True
    prompt = req["messages"][0]["content"]
    assert "Company: Netflix" in prompt and "Title:" in prompt
    article = prompt.split("<article>")[1]
    assert len(article.split()) <= MAX_WORDS + 1  # +1 for the closing tag


def test_schema_enums_come_from_taxonomy():
    props = tool_schema(TAX)["input_schema"]["properties"]
    assert props["primary_problem"]["enum"] == [p["id"] for p in TAX["problems"]]
    assert props["post_type"]["enum"] == TAX["post_types"]


def test_row_is_normalized_and_stored(con):
    counts = classify(con, TAX, client=FakeAnthropic(), model="m")
    assert counts == {"classified": 2, "failed": 0, "input_tokens": 2000, "output_tokens": 400}
    stack, problems, decisions, scale, model = con.execute(
        "SELECT stack, problems, key_decisions, scale, model FROM classifications WHERE url = 'https://a/de'").fetchone()
    assert stack == ["Kafka", "Flink"]
    assert problems == ["cdc_sync"]  # primary removed from the extras
    assert decisions == ["a", "b", "c"]
    assert (scale, model) == ("2M events/sec", "m")


def test_rerun_skips_classified_unless_reclassify(con):
    classify(con, TAX, client=FakeAnthropic())
    fake = FakeAnthropic()
    assert classify(con, TAX, client=fake)["classified"] == 0 and not fake.calls
    assert classify(con, TAX, client=fake, reclassify=True)["classified"] == 2


def test_no_tool_call_leaves_post_unclassified(con):
    assert classify(con, TAX, client=FakeAnthropic(answer=None))["failed"] == 2
    assert con.execute("SELECT count(*) FROM classifications").fetchone()[0] == 0


def test_normalize_stack():
    assert normalize_stack(["Apache Kafka", " kafka ", "PySpark", "Foo"]) == ["Kafka", "Spark", "Foo"]


def test_offline_heuristic():
    de = offline_classify({"url": "u", "title": "t", "text": DE_TEXT}, TAX)
    web = offline_classify({"url": "u", "title": "t", "text": WEB_TEXT}, TAX)
    assert de["is_de"] and not web["is_de"]
    assert de["primary_problem"] in ("realtime", "storage_formats", "cdc_sync")
    assert de["model"] == "offline"


def test_offline_rows_are_replaced_by_real_run(con):
    assert classify(con, TAX)["classified"] == 2  # client=None -> heuristic
    assert classify(con, TAX)["classified"] == 0  # offline re-run skips them
    assert classify(con, TAX, client=FakeAnthropic())["classified"] == 2


def test_evaluate(con, tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(yaml.safe_dump({"posts": [
        {"url": "https://a/de", "is_de": True, "primary_problem": "realtime", "reviewed": True},
        {"url": "https://a/web", "is_de": False, "primary_problem": None, "reviewed": False},
        {"url": "https://a/gone", "is_de": True, "primary_problem": "cost", "reviewed": True},
    ]}))
    r = evaluate(con, lambda p: offline_classify(p, TAX) | {"primary_problem": "realtime"}, path)
    assert (r["n"], r["is_de"], r["primary_problem"]) == (2, 1.0, 1.0)
    assert r["missing"] == ["https://a/gone"] and r["unreviewed"] == 1
    r = evaluate(con, lambda p: None, path)  # model never calls the tool
    assert r["is_de"] == 0.0 and len(r["misses"]) == 2
