from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.constants import REPLAY_EVENT_FILE_NAME, REPLAY_SESSION_FILE_NAME
from app.dto import ReplayRecord
from app.paths import REPLAY_DIR, ensure_project_dirs


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _make_json_safe(value: Any):
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _make_json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return value


def _write_session(record: ReplayRecord) -> None:
    session_payload = _make_json_safe(record)
    record.session_file.write_text(
        json.dumps(session_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_replay_session() -> ReplayRecord:
    ensure_project_dirs()
    run_id = _run_id()
    run_dir = REPLAY_DIR / run_id
    sequence = 1
    while run_dir.exists():
        run_dir = REPLAY_DIR / f"{run_id}_{sequence:02d}"
        sequence += 1

    screenshot_dir = run_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    record = ReplayRecord(
        run_id=run_dir.name,
        run_dir=run_dir,
        session_file=run_dir / REPLAY_SESSION_FILE_NAME,
        event_file=run_dir / REPLAY_EVENT_FILE_NAME,
        screenshot_dir=screenshot_dir,
        started_at=_timestamp(),
    )
    record.event_file.touch()
    _write_session(record)
    return record


def append_event(
    record: ReplayRecord,
    event_type: str,
    detail: dict[str, Any] | None = None,
    level: str = "INFO",
) -> None:
    payload = {
        "timestamp": _timestamp(),
        "level": level,
        "event": event_type,
        "detail": _make_json_safe(detail or {}),
    }
    with record.event_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def finalize_session(record: ReplayRecord, status: str = "completed", error: str | None = None) -> None:
    record.status = status
    record.ended_at = _timestamp()
    record.error = error
    _write_session(record)
