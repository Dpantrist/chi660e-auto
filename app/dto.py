from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    visible: bool = True
    enabled: bool = True
    pid: int | None = None
    rect: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class LinkResult:
    selected_window: WindowInfo
    matched_windows: list[WindowInfo]
    match_field: str


@dataclass(slots=True)
class RuntimeFolders:
    base_dir: Path
    resource_dir: Path
    pipeline_dir: Path
    image_dir: Path
    log_dir: Path
    debug_dir: Path
    debug_latest_dir: Path
    replay_dir: Path
    config_dir: Path


@dataclass(slots=True)
class ReplayRecord:
    run_id: str
    run_dir: Path
    session_file: Path
    event_file: Path
    screenshot_dir: Path
    started_at: str
    status: str = "running"
    ended_at: str | None = None
    error: str | None = None


@dataclass(slots=True)
class WindowSession:
    keyword: str
    hwnd: int
    linked_window: WindowInfo
    controller: object
    tasker: object


@dataclass(slots=True)
class AppStatus:
    stage: str = "created"
    ready: bool = False
    message: str = "Application initialized."
    last_error: str | None = None
