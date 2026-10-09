import pytest

from backend.services.evaluation_service import BenchmarkRecord
from scripts.run_evaluation import load_dataset


def test_formal_evaluation_rejects_balanced_but_unreviewed_dataset(tmp_path):
    dataset = tmp_path / "benchmark.jsonl"
    record = BenchmarkRecord(
        query_id="draft1",
        query="推荐科幻片",
        query_type="测试",
        query_group="fuzzy",
        annotation_status="draft_needs_human_review",
    )
    dataset.write_text(record.model_dump_json() + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="正式实验门禁"):
        load_dataset(dataset, allow_draft=False)
    assert load_dataset(dataset, allow_draft=True)[0].query_id == "draft1"
