from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any


user32 = ctypes.WinDLL("user32", use_last_error=True)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


def _failure(hwnd: int, reason: str, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "x": x,
        "y": y,
        "hwnd": hwnd,
        "reason": reason,
    }


def move_cursor_to_window_safe_corner(
    hwnd: int,
    margin_x: int = 24,
    margin_y: int = 24,
    logger=None,
) -> dict[str, Any]:
    if not hwnd:
        return _failure(hwnd, "invalid_hwnd")

    client_rect = RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client_rect)):
        error_code = ctypes.get_last_error()
        if logger is not None:
            logger.debug("GetClientRect failed for hwnd=%s error=%s", hwnd, error_code)
        return _failure(hwnd, f"get_client_rect_failed:{error_code}")

    width = client_rect.right - client_rect.left
    height = client_rect.bottom - client_rect.top
    if width <= 1 or height <= 1:
        return _failure(hwnd, "client_rect_too_small")

    safe_margin_x = max(1, min(int(margin_x), width - 1))
    safe_margin_y = max(1, min(int(margin_y), height - 1))
    target_point = POINT(width - safe_margin_x, height - safe_margin_y)

    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(target_point)):
        error_code = ctypes.get_last_error()
        if logger is not None:
            logger.debug("ClientToScreen failed for hwnd=%s error=%s", hwnd, error_code)
        return _failure(hwnd, f"client_to_screen_failed:{error_code}")

    if not user32.SetCursorPos(int(target_point.x), int(target_point.y)):
        error_code = ctypes.get_last_error()
        if logger is not None:
            logger.debug("SetCursorPos failed for hwnd=%s error=%s", hwnd, error_code)
        return _failure(hwnd, f"set_cursor_pos_failed:{error_code}", int(target_point.x), int(target_point.y))

    return {
        "success": True,
        "x": int(target_point.x),
        "y": int(target_point.y),
        "hwnd": hwnd,
        "reason": None,
    }
