import hashlib
import json
import pytest

from scripts.assist_benchmark_annotations import validate_judgments
from scripts import assist_benchmark_annotations as assistant
from backend.services.evaluation_service import BenchmarkRecord


def packet():
    return {"query_id": "q1", "movies": [{"tmdb_id": 7, "overview": "机器人学会照顾动物，与小鹅建立亲情。"}]}


def judgment(**changes):
    return {"query_id": "q1", "judgments": [{"tmdb_id": 7, "grade": 2,
        "reason": "机器人与动物的亲情符合主题", "excerpt": "机器人学会照顾动物", "unknowns": [], **changes}]}


@pytest.mark.parametrize("changes", [{"grade": True}, {"grade": 3}, {"tmdb_id": 9}, {"reason": ""}])
def test_invalid_machine_labels_rejected(changes):
    with pytest.raises(ValueError):
        validate_judgments(packet(), judgment(**changes))


def test_missing_candidate_rejected():
    with pytest.raises(ValueError):
        validate_judgments(packet(), {"query_id": "q1", "judgments": []})


def test_unmatched_synopsis_evidence_stays_unannotated():
    result = validate_judgments(packet(), judgment(excerpt="从未出现在简介中的情节"))
    assert result[0]["grade"] is None
    assert result[0]["unknowns"]
    assert result[0]["excerpt"] == ""
    assert result[0]["rejected_excerpt"] == "从未出现在简介中的情节"


def test_unknown_does_not_become_grade_zero():
    result = validate_judgments(packet(), judgment(grade=None, excerpt=""))
    assert result[0]["grade"] is None


def test_valid_draft_evidence_preserved():
    result = validate_judgments(packet(), judgment())
    assert result[0]["grade"] == 2


def test_missing_synopsis_does_not_assert_full_match():
    source = packet()
    source["movies"][0]["overview"] = "暂无中文简介。"
    result = validate_judgments(source, judgment(excerpt=""))
    assert result[0]["grade"] == 1 and result[0]["unknowns"]


def test_uncertain_essential_requirement_caps_machine_grade():
    result = validate_judgments(packet(), judgment(unknowns=["实际节奏未知"]))
    assert result[0]["grade"] == 1


def test_apply_only_drafts_preserves_verified_record_and_query_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant, 'ROOT', tmp_path)
    dataset = tmp_path / 'experiments/data/query_benchmark.jsonl'
    monkeypatch.setattr(assistant, 'DATASET', dataset)
    dataset.parent.mkdir(parents=True)
    snapshots = tmp_path / 'experiments/snapshots-vector-v2'
    snapshots.mkdir()
    output = tmp_path / 'assisted'
    (output / 'labels').mkdir(parents=True)
    records = []
    for qid, mid, status in [('q001', 7, 'verified'), ('q002', 8, 'draft_needs_human_review')]:
        record = BenchmarkRecord(query_id=qid, query='温暖的机器人故事', query_type='主题', query_group='mood_subjective',
                                 expected_intent={'themes':['亲情']}, hard_constraints={'genres':['科幻']},
                                 graded_relevance={str(mid):2} if status=='verified' else {},
                                 relevant_tmdb_ids=[mid] if status=='verified' else [],
                                 annotation_status=status, annotation_note='用户已有的说明')
        payload = {'user_query':record.query,'recommendations':[{'tmdb_id':mid,'title':'影片','overview':'机器人学会照顾动物'}]}
        raw = json.dumps(payload,ensure_ascii=False).encode()
        path = snapshots / f'{qid}-saved.json'
        path.write_bytes(raw)
        identity = {'annotation_snapshot_id':f'snapshots-vector-v2/{path.name}',
                    'annotation_snapshot_sha256':hashlib.sha256(raw).hexdigest()}
        if status=='verified':
            record=record.model_copy(update=identity)
        records.append(record)
        draft={'query_id':qid,'judgments':[{'tmdb_id':mid,'grade':1,'reason':'部分相关','excerpt':'机器人学会照顾动物','unknowns':['年龄适宜性待核对']}],
               'annotation_source':'machine_assisted','human_review_confirmed':False,'annotation_status':'draft_needs_human_review',
               'reported_model':'test-judge',**identity}
        (output/'labels'/f'{qid}.json').write_text(json.dumps(draft,ensure_ascii=False))
    dataset.write_text(''.join(r.model_dump_json(exclude_none=True)+'\n' for r in records))
    before = dataset.read_bytes()
    assistant.apply_drafts(output)
    after = [json.loads(line) for line in dataset.read_text().splitlines()]
    assert after[0] == records[0].model_dump(exclude_none=True)
    assert after[1]['graded_relevance'] == {'8':1}
    assert after[1]['relevant_tmdb_ids'] == [8]
    assert after[1]['annotation_status']=='draft_needs_human_review'
    assert after[1]['expected_intent']==records[1].expected_intent
    assert after[1]['hard_constraints']==records[1].hard_constraints
    assert after[1]['annotation_snapshot_id']=='snapshots-vector-v2/q002-saved.json'
    assert '[机器辅助初标，未人工复核]' in after[1]['annotation_note']
    assert next(output.glob('benchmark-before-assisted-*.jsonl')).read_bytes()==before
    final_bytes = dataset.read_bytes()
    assistant.apply_drafts(output)
    assert dataset.read_bytes() == final_bytes
    dataset.write_bytes(final_bytes + b'\n')
    with pytest.raises(ValueError, match='数据集已变化'):
        assistant.apply_drafts(output)


def test_apply_does_not_change_version_during_capture(tmp_path):
    progress={'completed':1,'authorized_missing_count':2}
    (tmp_path/'capture-progress.json').write_text(json.dumps(progress))
    with pytest.raises(ValueError,match='采集尚未结束'):
        assistant.apply_drafts(tmp_path)
