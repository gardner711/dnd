"""In-memory session store for pre-auth integration testing."""
from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

from app.models import SessionContext

_sessions: dict[str, SessionContext] = {}
_lock = RLock()


def create_session(user_id: UUID, campaign_id: UUID, character_id: UUID, map_hint_id: UUID | None) -> SessionContext:
    now = datetime.now(UTC)
    session = SessionContext(
        session_id=uuid4(),
        user_id=user_id,
        campaign_id=campaign_id,
        character_id=character_id,
        map_hint_id=map_hint_id,
        started_at=now,
        updated_at=now,
    )
    with _lock:
        _sessions[str(session.session_id)] = session
    return session


def get_session(session_id: UUID) -> SessionContext | None:
    with _lock:
        return _sessions.get(str(session_id))


def update_session_map_hint(session_id: UUID, map_hint_id: UUID | None) -> SessionContext | None:
    with _lock:
        current = _sessions.get(str(session_id))
        if current is None:
            return None
        updated = current.model_copy(update={"map_hint_id": map_hint_id, "updated_at": datetime.now(UTC)})
        _sessions[str(session_id)] = updated
        return updated


def delete_session(session_id: UUID) -> bool:
    with _lock:
        return _sessions.pop(str(session_id), None) is not None


def session_count() -> int:
    with _lock:
        return len(_sessions)


def clear_sessions() -> None:
    with _lock:
        _sessions.clear()