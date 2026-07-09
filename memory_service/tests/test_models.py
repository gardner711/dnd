import pytest
from uuid import uuid4
from pydantic import ValidationError
from app.models import MemoryIn, RecallResult, SubjectType


def test_memory_in_valid():
    m = MemoryIn(
        campaign_id=uuid4(), subject_type=SubjectType.NPC,
        subject_id=uuid4(), content="Innkeeper became hostile",
    )
    assert m.importance == 3
    assert m.source_event_ids == []


def test_memory_in_importance_bounds():
    with pytest.raises(ValidationError):
        MemoryIn(campaign_id=uuid4(), subject_type=SubjectType.NPC,
                 subject_id=uuid4(), content="x", importance=6)
    with pytest.raises(ValidationError):
        MemoryIn(campaign_id=uuid4(), subject_type=SubjectType.NPC,
                 subject_id=uuid4(), content="x", importance=0)


def test_memory_in_empty_content_fails():
    with pytest.raises(ValidationError):
        MemoryIn(campaign_id=uuid4(), subject_type=SubjectType.NPC,
                 subject_id=uuid4(), content="")


def test_memory_in_content_too_long_fails():
    with pytest.raises(ValidationError):
        MemoryIn(campaign_id=uuid4(), subject_type=SubjectType.NPC,
                 subject_id=uuid4(), content="x" * 2001)


def test_recall_result_defaults():
    r = RecallResult(memories=[], query="dragons", top_k=5)
    assert r.memories == []


def test_all_subject_types_valid():
    for st in SubjectType:
        m = MemoryIn(campaign_id=uuid4(), subject_type=st,
                     subject_id=uuid4(), content="test content")
        assert m.subject_type == st


def test_memory_update_both_fields():
    from app.models import MemoryUpdate
    u = MemoryUpdate(importance=5, content="Updated content")
    assert u.importance == 5
    assert u.content == "Updated content"


def test_memory_update_empty_is_valid():
    from app.models import MemoryUpdate
    u = MemoryUpdate()
    assert u.importance is None
    assert u.content is None


def test_memory_update_importance_bounds():
    from app.models import MemoryUpdate
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MemoryUpdate(importance=0)
    with pytest.raises(ValidationError):
        MemoryUpdate(importance=6)
