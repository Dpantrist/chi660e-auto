from __future__ import annotations

from app.dto import LinkResult, WindowInfo
from app.errors import Chi660eAutoError, WindowNotFoundError


def _import_toolkit():
    try:
        from maa.toolkit import Toolkit
    except ImportError as exc:
        raise Chi660eAutoError(
            "Failed to import maa.toolkit. Install the MaaFramework Python binding "
            "for this machine before running the project."
        ) from exc
    return Toolkit


def _to_int(value, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_rect(value) -> tuple[int, int, int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
        except (TypeError, ValueError):
            return None

    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    w = getattr(value, "w", None)
    h = getattr(value, "h", None)
    if None not in (x, y, w, h):
        return (int(x), int(y), int(w), int(h))

    return None


def build_window_info(win) -> WindowInfo:
    title = getattr(win, "window_name", None) or getattr(win, "title", None) or ""
    class_name = getattr(win, "class_name", None) or ""
    hwnd = _to_int(getattr(win, "hwnd", None) or getattr(win, "hWnd", None), 0) or 0
    pid = _to_int(getattr(win, "process_id", None) or getattr(win, "pid", None))
    visible = bool(getattr(win, "is_visible", True))
    enabled = bool(getattr(win, "is_enabled", True))
    rect = _normalize_rect(getattr(win, "rect", None))

    return WindowInfo(
        hwnd=hwnd,
        title=title,
        class_name=class_name,
        visible=visible,
        enabled=enabled,
        pid=pid,
        rect=rect,
    )


def list_desktop_windows() -> list[WindowInfo]:
    toolkit = _import_toolkit()
    windows = toolkit.find_desktop_windows() or []
    return [build_window_info(win) for win in windows]


def find_target_window(keyword: str, windows: list[WindowInfo] | None = None) -> LinkResult:
    keyword_lower = keyword.lower()
    if windows is None:
        windows = list_desktop_windows()
    if not windows:
        raise WindowNotFoundError("No desktop windows were detected by MaaFramework Toolkit.")

    title_matches = [
        window
        for window in windows
        if keyword_lower in window.title.lower()
    ]
    if title_matches:
        return LinkResult(
            selected_window=title_matches[0],
            matched_windows=title_matches,
            match_field="title",
        )

    class_matches = [
        window
        for window in windows
        if keyword_lower in window.class_name.lower()
    ]
    if class_matches:
        return LinkResult(
            selected_window=class_matches[0],
            matched_windows=class_matches,
            match_field="class_name",
        )

    candidate_lines = [
        f"hwnd={window.hwnd} title={window.title!r} class={window.class_name!r}"
        for window in windows
    ]
    candidate_text = " | ".join(candidate_lines) if candidate_lines else "none"
    raise WindowNotFoundError(
        f"Target window not found for keyword {keyword!r}. Candidates: {candidate_text}"
    )
