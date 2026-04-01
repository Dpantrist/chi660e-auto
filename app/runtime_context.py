from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.dto import AppStatus, ReplayRecord, WindowInfo


@dataclass(slots=True)
class RuntimeContext:
    logger: logging.Logger | None = None
    resource: Any = None
    controller: Any = None
    tasker: Any = None
    linked_window: WindowInfo | None = None
    window_keyword: str | None = None
    replay_dir: Path | None = None
    status: AppStatus = field(default_factory=AppStatus)
    replay_record: ReplayRecord | None = None
