from __future__ import annotations

import ctypes
import json
import re
import time
from pathlib import Path

from app.paths import BASE_DIR, WINDOW_BASELINE_DIR

_MISSING_PRESET_WARNED: set[str] = set()


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def sanitize_baseline_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return "window_baseline"
    raw = re.sub(r"\s+", "_", raw)
    raw = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    raw = re.sub(r"_+", "_", raw)
    raw = raw.strip("_")
    return raw or "window_baseline"


def resolve_restore_baseline_profile_path(window_keyword: str | None = None) -> Path:
    keyword = window_keyword or "window_baseline"
    return WINDOW_BASELINE_DIR / f"{sanitize_baseline_name(keyword)}.json"


def load_window_baseline_profile(window_keyword: str | None = None) -> dict | None:
    path = resolve_restore_baseline_profile_path(window_keyword)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return None


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
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


def get_client_rect(hwnd: int) -> tuple[int, int] | None:
    rect = RECT()
    ok = ctypes.windll.user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    if not ok:
        return None
    return int(rect.right - rect.left), int(rect.bottom - rect.top)


def get_window_style(hwnd: int) -> int | None:
    try:
        getter = getattr(ctypes.windll.user32, "GetWindowLongPtrW", None)
        if getter is None:
            getter = ctypes.windll.user32.GetWindowLongW
        return int(getter(ctypes.c_void_p(hwnd), -16))
    except Exception:
        return None


def get_window_exstyle(hwnd: int) -> int | None:
    try:
        getter = getattr(ctypes.windll.user32, "GetWindowLongPtrW", None)
        if getter is None:
            getter = ctypes.windll.user32.GetWindowLongW
        return int(getter(ctypes.c_void_p(hwnd), -20))
    except Exception:
        return None


def get_window_dpi(hwnd: int) -> int | None:
    try:
        getter = getattr(ctypes.windll.user32, "GetDpiForWindow", None)
        if getter is None:
            return None
        return int(getter(ctypes.c_void_p(hwnd)))
    except Exception:
        return None


def is_window_maximized(hwnd: int) -> bool:
    return bool(ctypes.windll.user32.IsZoomed(ctypes.c_void_p(hwnd)))


def is_window_minimized(hwnd: int) -> bool:
    return bool(ctypes.windll.user32.IsIconic(ctypes.c_void_p(hwnd)))


def is_window_visible(hwnd: int) -> bool:
    return bool(ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(hwnd)))


def get_monitor_work_area(hwnd: int) -> tuple[int, int, int, int] | None:
    try:
        monitor = ctypes.windll.user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)
        if not monitor:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        ok = ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info))
        if not ok:
            return None
        return (
            int(info.rcWork.left),
            int(info.rcWork.top),
            int(info.rcWork.right - info.rcWork.left),
            int(info.rcWork.bottom - info.rcWork.top),
        )
    except Exception:
        return None


def restore_window_rect(hwnd: int, rect: tuple[int, int, int, int]) -> bool:
    left, top, width, height = rect
    ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 9)
    moved = ctypes.windll.user32.MoveWindow(
        ctypes.c_void_p(hwnd), int(left), int(top), int(width), int(height), True
    )
    if not moved:
        return False
    try:
        ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    except Exception:
        pass
    return True


def baseline_client_size(profile: dict) -> tuple[int, int] | None:
    rect = (profile or {}).get("client_rect")
    if not isinstance(rect, dict):
        return None
    try:
        return int(rect["width"]), int(rect["height"])
    except Exception:
        return None


def baseline_capture_status(profile: dict) -> dict | None:
    capture = (profile or {}).get("capture")
    if not isinstance(capture, dict):
        return None

    result: dict[str, list[int] | str | None] = {}
    image_shape = capture.get("image_shape")
    resolution = capture.get("resolution")
    screencap_method = capture.get("screencap_method")

    if isinstance(image_shape, (list, tuple)) and len(image_shape) >= 2:
        try:
            result["image_shape"] = [int(item) for item in image_shape]
        except Exception:
            result["image_shape"] = None

    if isinstance(resolution, (list, tuple)) and len(resolution) >= 2:
        try:
            result["resolution"] = [int(item) for item in resolution]
        except Exception:
            result["resolution"] = None

    if screencap_method is not None:
        result["screencap_method"] = str(screencap_method)

    return result or None


def baseline_window_style(profile: dict) -> tuple[int | None, int | None]:
    style = (profile or {}).get("window_style")
    if not isinstance(style, dict):
        return None, None
    return style.get("style_int"), style.get("exstyle_int")


def baseline_window_dpi(profile: dict) -> int | None:
    dpi = (profile or {}).get("window_dpi")
    try:
        return None if dpi is None else int(dpi)
    except Exception:
        return None


def _baseline_rect_tuple(profile: dict, key: str) -> tuple[int, int, int, int] | None:
    rect = (profile or {}).get(key)
    if not isinstance(rect, dict):
        return None
    try:
        return (
            int(rect["left"]),
            int(rect["top"]),
            int(rect["width"]),
            int(rect["height"]),
        )
    except Exception:
        return None


def _get_menu_presence(hwnd: int) -> bool:
    try:
        return bool(ctypes.windll.user32.GetMenu(ctypes.c_void_p(hwnd)))
    except Exception:
        return False


def _safe_cached_image(controller):
    try:
        return getattr(controller, "cached_image", None)
    except Exception:
        return None


def get_capture_status(controller) -> dict | None:
    if controller is None:
        return None

    image = None
    try:
        job = controller.post_screencap().wait()
        if getattr(job, "succeeded", False):
            image = _safe_cached_image(controller)
            if image is None and hasattr(job, "get"):
                try:
                    image = job.get()
                except Exception:
                    image = None
    except Exception:
        image = _safe_cached_image(controller)

    try:
        resolution = getattr(controller, "resolution", None)
    except Exception:
        resolution = None

    result: dict[str, list[int] | None] = {}
    image_shape = getattr(image, "shape", None) if image is not None else None
    if isinstance(image_shape, (list, tuple)) and len(image_shape) >= 2:
        try:
            result["image_shape"] = [int(item) for item in image_shape]
        except Exception:
            result["image_shape"] = None

    if isinstance(resolution, (list, tuple)) and len(resolution) >= 2:
        try:
            result["resolution"] = [int(item) for item in resolution]
        except Exception:
            result["resolution"] = None

    return result or None


def client_matches_baseline(hwnd: int, baseline: dict, tolerance: int = 2) -> bool:
    expected_client = baseline_client_size(baseline)
    if expected_client is None:
        return True

    actual_client = get_client_rect(hwnd)
    if actual_client is None:
        return False

    return all(abs(int(actual) - int(expected)) <= tolerance for actual, expected in zip(actual_client, expected_client))


def style_matches_baseline(hwnd: int, baseline: dict) -> bool:
    expected_style, expected_exstyle = baseline_window_style(baseline)
    actual_style = get_window_style(hwnd)
    actual_exstyle = get_window_exstyle(hwnd)

    if expected_style is not None and actual_style != expected_style:
        return False
    if expected_exstyle is not None and actual_exstyle != expected_exstyle:
        return False
    return True


def dpi_matches_baseline(hwnd: int, baseline: dict) -> bool:
    expected_dpi = baseline_window_dpi(baseline)
    if expected_dpi is None:
        return True

    actual_dpi = get_window_dpi(hwnd)
    if actual_dpi is None:
        return False
    return int(actual_dpi) == int(expected_dpi)


def capture_matches_baseline(current_capture: dict | None, baseline: dict) -> bool:
    expected_capture = baseline_capture_status(baseline)
    if not expected_capture:
        return True
    if not current_capture:
        return False

    expected_shape = expected_capture.get("image_shape")
    expected_resolution = expected_capture.get("resolution")
    actual_shape = current_capture.get("image_shape")
    actual_resolution = current_capture.get("resolution")

    if expected_shape is not None and actual_shape != expected_shape:
        return False
    if expected_resolution is not None and actual_resolution != expected_resolution:
        return False
    return True


def window_matches_baseline(hwnd: int, controller, baseline: dict) -> tuple[bool, dict]:
    expected_client = baseline_client_size(baseline)
    expected_style, expected_exstyle = baseline_window_style(baseline)
    expected_dpi = baseline_window_dpi(baseline)
    expected_capture = baseline_capture_status(baseline)

    actual_rect = get_window_rect(hwnd)
    actual_client = get_client_rect(hwnd)
    actual_style = get_window_style(hwnd)
    actual_exstyle = get_window_exstyle(hwnd)
    actual_dpi = get_window_dpi(hwnd)
    actual_capture = get_capture_status(controller)

    client_ok = client_matches_baseline(hwnd, baseline)
    style_ok = style_matches_baseline(hwnd, baseline)
    dpi_ok = dpi_matches_baseline(hwnd, baseline)
    capture_ok = capture_matches_baseline(actual_capture, baseline)

    details = {
        "rect": actual_rect,
        "expected_rect": _baseline_rect_tuple(baseline, "window_rect"),
        "client_rect": actual_client,
        "expected_client_rect": expected_client,
        "dpi": actual_dpi,
        "expected_dpi": expected_dpi,
        "style": actual_style,
        "expected_style": expected_style,
        "exstyle": actual_exstyle,
        "expected_exstyle": expected_exstyle,
        "capture_status": actual_capture,
        "expected_capture_status": expected_capture,
        "style_ok": style_ok,
        "dpi_ok": dpi_ok,
        "client_ok": client_ok,
        "capture_ok": capture_ok,
    }

    all_ok = client_ok and style_ok and dpi_ok and capture_ok
    return all_ok, details


def compute_target_window_rect_from_baseline(hwnd: int, baseline: dict) -> tuple[int, int, int, int] | None:
    client_size = baseline_client_size(baseline)
    if client_size is None:
        return None

    baseline_window_rect = _baseline_rect_tuple(baseline, "window_rect")

    if baseline_window_rect is not None:
        left = int(baseline_window_rect[0])
        top = int(baseline_window_rect[1])
        target_width = int(baseline_window_rect[2])
        target_height = int(baseline_window_rect[3])
    else:
        client_w, client_h = client_size
        current_style = get_window_style(hwnd)
        current_exstyle = get_window_exstyle(hwnd)
        baseline_style, baseline_exstyle = baseline_window_style(baseline)
        style = current_style if current_style is not None else baseline_style
        exstyle = current_exstyle if current_exstyle is not None else baseline_exstyle
        current_dpi = get_window_dpi(hwnd)
        fallback_dpi = baseline_window_dpi(baseline)
        dpi = current_dpi if current_dpi is not None else fallback_dpi
        target_width = None
        target_height = None

        if style is not None and exstyle is not None and dpi is not None:
            try:
                rect = RECT()
                rect.left = 0
                rect.top = 0
                rect.right = int(client_w)
                rect.bottom = int(client_h)
                adjust = getattr(ctypes.windll.user32, "AdjustWindowRectExForDpi", None)
                if adjust is not None:
                    ok = adjust(
                        ctypes.byref(rect),
                        int(style),
                        bool(_get_menu_presence(hwnd)),
                        int(exstyle),
                        int(dpi),
                    )
                    if ok:
                        target_width = int(rect.right - rect.left)
                        target_height = int(rect.bottom - rect.top)
            except Exception:
                target_width = None
                target_height = None

        if target_width is None or target_height is None:
            return None

        work_area = get_monitor_work_area(hwnd)
        if work_area is None:
            left, top = 100, 100
        else:
            work_left, work_top, work_w, work_h = work_area
            left = work_left + max(0, (work_w - target_width) // 2)
            top = work_top + max(0, (work_h - target_height) // 2)

    work_area = get_monitor_work_area(hwnd)
    if work_area is not None:
        work_left, work_top, work_w, work_h = work_area
        if (
            left < work_left
            or top < work_top
            or left + target_width > work_left + work_w
            or top + target_height > work_top + work_h
        ):
            left = work_left + max(0, (work_w - target_width) // 2)
            top = work_top + max(0, (work_h - target_height) // 2)

    return int(left), int(top), int(target_width), int(target_height)


def enforce_window_preset_until_verified(
    hwnd: int,
    window_keyword: str,
    controller_factory,
    logger=None,
    retries: int = 3,
    wait_sec: float = 0.3,
) -> dict:
    path = resolve_restore_baseline_profile_path(window_keyword)
    result = {
        "preset_exists": path.exists(),
        "preset_attempted": False,
        "preset_applied": False,
        "verified": False,
        "verify_details": None,
        "attempts": 0,
        "preset_path": str(path),
        "controller": None,
        "history": [],
    }

    if not path.exists():
        return result

    baseline = load_window_baseline_profile(window_keyword)
    if baseline is None:
        if logger is not None:
            logger.warning("Failed to load window preset from: %s", path)
        return result

    retries = max(1, int(retries))
    controller = None

    for attempt in range(1, retries + 1):
        result["attempts"] = attempt
        result["history"].append(
            {
                "event": "window_preset_verify",
                "attempt": attempt,
                "level": "INFO",
                "detail": {
                    "keyword": window_keyword,
                    "preset_path": str(path),
                },
            }
        )

        controller = controller_factory(hwnd)
        verify_result = verify_window_preset_applied(hwnd, controller, window_keyword, logger=logger)
        result["verify_details"] = verify_result.get("verify_details")
        result["verified"] = bool(verify_result.get("verified"))
        result["controller"] = controller
        result["history"][-1]["detail"].update(verify_result)
        if result["verified"]:
            return result

        result["history"].append(
            {
                "event": "window_preset_mismatch",
                "attempt": attempt,
                "level": "ERROR",
                "detail": {
                    "keyword": window_keyword,
                    "preset_path": str(path),
                    "verify_details": verify_result.get("verify_details"),
                },
            }
        )

        if attempt >= retries:
            break

        apply_result = apply_window_preset_if_available(hwnd, window_keyword, logger=logger)
        result["preset_attempted"] = result["preset_attempted"] or apply_result.get("preset_attempted", False)
        result["preset_applied"] = result["preset_applied"] or apply_result.get("preset_applied", False)
        result["history"].append(
            {
                "event": "window_preset",
                "attempt": attempt,
                "level": "INFO",
                "detail": {
                    "keyword": window_keyword,
                    **apply_result,
                },
            }
        )
        result["history"].append(
            {
                "event": "window_preset_retry",
                "attempt": attempt + 1,
                "level": "INFO",
                "detail": {
                    "keyword": window_keyword,
                    "preset_path": str(path),
                },
            }
        )
        time.sleep(wait_sec)

    return result


def _format_terminal_path(path: Path) -> str:
    try:
        return str(path.relative_to(BASE_DIR)).replace("\\", "/")
    except Exception:
        return str(path)


def _warn_missing_preset_once(path: Path, logger=None) -> None:
    key = str(path)
    if key in _MISSING_PRESET_WARNED:
        return
    _MISSING_PRESET_WARNED.add(key)
    message = f"[WARN] Missing window preset: {_format_terminal_path(path)}"
    print(message)
    if logger is not None:
        logger.warning("Missing window preset file: %s", path)


def apply_window_preset_if_available(hwnd: int, window_keyword: str, logger=None) -> dict:
    path = resolve_restore_baseline_profile_path(window_keyword)
    result = {
        "preset_exists": path.exists(),
        "preset_attempted": False,
        "preset_applied": False,
        "verified": False,
        "verify_details": None,
        "preset_path": str(path),
    }
    if not path.exists():
        _warn_missing_preset_once(path, logger=logger)
        return result

    baseline = load_window_baseline_profile(window_keyword)
    if baseline is None:
        if logger is not None:
            logger.warning("Failed to load window preset from: %s", path)
        return result

    result["preset_attempted"] = True
    try:
        if is_window_minimized(hwnd) or is_window_maximized(hwnd):
            ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 9)

        target_rect = compute_target_window_rect_from_baseline(hwnd, baseline)
        if target_rect is None:
            if logger is not None:
                logger.warning("Failed to compute target rect from window preset: %s", path)
            return result

        if not restore_window_rect(hwnd, target_rect):
            if logger is not None:
                logger.warning("Failed to restore window rect using preset: %s", path)
            return result

        time.sleep(0.2)
        result["preset_applied"] = True
        return result
    except Exception:
        if logger is not None:
            logger.exception("Failed to apply window preset: %s", path)
        return result


def verify_window_preset_applied(hwnd: int, controller, window_keyword: str, logger=None) -> dict:
    path = resolve_restore_baseline_profile_path(window_keyword)
    result = {
        "preset_exists": path.exists(),
        "preset_attempted": False,
        "preset_applied": False,
        "verified": False,
        "verify_details": None,
        "preset_path": str(path),
    }
    if not path.exists():
        return result

    baseline = load_window_baseline_profile(window_keyword)
    if baseline is None:
        if logger is not None:
            logger.warning("Failed to load window preset for verification: %s", path)
        return result

    verified, details = window_matches_baseline(hwnd, controller, baseline)
    result["verified"] = verified
    result["verify_details"] = details
    return result
