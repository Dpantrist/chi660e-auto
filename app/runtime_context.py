from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.dto import AppStatus, ReplayRecord, WindowInfo, WindowSession


@dataclass(slots=True)
class RuntimeContext:
    logger: logging.Logger | None = None
    resource: Any = None
    controller: Any = None
    tasker: Any = None
    linked_window: WindowInfo | None = None
    window_keyword: str | None = None
    sessions: dict[str, WindowSession] = field(default_factory=dict)
    active_session_key: str | None = None
    replay_dir: Path | None = None
    status: AppStatus = field(default_factory=AppStatus)
    replay_record: ReplayRecord | None = None
    last_action_completed_at: float | None = None
    run_control: Any = None
    # GUI 事件回调：
    # 仅用于把结构化运行态信息回传给界面，不替代 logger / replay 记录链。
    gui_event_sink: Callable[[str, dict[str, Any]], None] | None = None
