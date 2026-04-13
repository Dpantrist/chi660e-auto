from __future__ import annotations

import ctypes

from app.dto import LinkResult, WindowInfo
from app.errors import Chi660eAutoError, WindowNotFoundError


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


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


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if hwnd <= 0 or not hasattr(ctypes, "windll"):
        return None

    rect = RECT()
    ok = ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    if not ok:
        return None
    return (
        int(rect.left),
        int(rect.top),
        int(rect.right - rect.left),
        int(rect.bottom - rect.top),
    )


def _is_window_minimized(hwnd: int) -> bool:
    if hwnd <= 0 or not hasattr(ctypes, "windll"):
        return False
    try:
        return bool(ctypes.windll.user32.IsIconic(ctypes.c_void_p(hwnd)))
    except Exception:
        return False


def _is_window_usable(window: WindowInfo) -> bool:
    if not window.visible or not window.enabled or window.rect is None:
        return False
    _, _, width, height = window.rect
    return width > 0 and height > 0 and not _is_window_minimized(window.hwnd)


def _window_area(window: WindowInfo) -> int:
    if window.rect is None:
        return -1
    _, _, width, height = window.rect
    return int(width) * int(height)


def _select_best_window(matches: list[WindowInfo]) -> WindowInfo:
    usable_matches = [window for window in matches if _is_window_usable(window)]
    if usable_matches:
        usable_matches.sort(key=_window_area, reverse=True)
        return usable_matches[0]
    return matches[0]


def build_window_info(win) -> WindowInfo:
    title = getattr(win, "window_name", None) or getattr(win, "title", None) or ""
    class_name = getattr(win, "class_name", None) or ""
    hwnd = _to_int(getattr(win, "hwnd", None) or getattr(win, "hWnd", None), 0) or 0
    pid = _to_int(getattr(win, "process_id", None) or getattr(win, "pid", None))
    visible = bool(getattr(win, "is_visible", True))
    enabled = bool(getattr(win, "is_enabled", True))
    rect = _normalize_rect(getattr(win, "rect", None))
    if rect is None and hwnd:
        rect = _get_window_rect(hwnd)

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


def _normalize_keywords(keyword: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(keyword, str):
        return [keyword]
    return [item for item in keyword if item]


def find_target_window(
    keyword: str | list[str] | tuple[str, ...],
    windows: list[WindowInfo] | None = None,
) -> LinkResult:
    keywords = _normalize_keywords(keyword)
    keyword_lowers = [item.lower() for item in keywords]
    if windows is None:
        windows = list_desktop_windows()
    if not windows:
        raise WindowNotFoundError("No desktop windows were detected by MaaFramework Toolkit.")

    title_matches = [
        window
        for window in windows
        if any(item in window.title.lower() for item in keyword_lowers)
    ]
    if title_matches:
        title_matches.sort(key=lambda window: (_is_window_usable(window), _window_area(window)), reverse=True)
        return LinkResult(
            selected_window=_select_best_window(title_matches),
            matched_windows=title_matches,
            match_field="title",
        )

    class_matches = [
        window
        for window in windows
        if any(item in window.class_name.lower() for item in keyword_lowers)
    ]
    if class_matches:
        class_matches.sort(key=lambda window: (_is_window_usable(window), _window_area(window)), reverse=True)
        return LinkResult(
            selected_window=_select_best_window(class_matches),
            matched_windows=class_matches,
            match_field="class_name",
        )

    raise WindowNotFoundError("Target window not found for configured title candidates.")
