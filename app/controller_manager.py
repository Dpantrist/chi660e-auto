from __future__ import annotations

import ctypes
import os
from pathlib import Path

from app.constants import DEFAULT_INPUT_METHOD, DEFAULT_SCREENCAP_METHOD
from app.errors import ControllerInitError
from app.paths import BASE_DIR


def _candidate_bin_dirs() -> list[Path]:
    candidates: list[Path] = []
    env_dir = os.environ.get("MAA_BIN_DIR")
    if env_dir:
        candidates.append(Path(env_dir))

    candidates.extend(
        [
            BASE_DIR / "maa_bin",
            BASE_DIR.parent / "maa_bin",
            BASE_DIR.parent / "MAA-win-x86_64-v5.9.2" / "bin",
            BASE_DIR.parent.parent / "MAA-win-x86_64-v5.9.2" / "bin",
        ]
    )

    for parent in (BASE_DIR.parent, BASE_DIR.parent.parent):
        if parent.exists():
            candidates.extend(sorted(parent.glob("MAA-win-*/bin")))

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_candidates.append(candidate)
    return unique_candidates


def _resolve_bin_dir() -> Path | None:
    for candidate in _candidate_bin_dirs():
        if candidate.exists():
            return candidate
    return None


def _import_controller_types():
    try:
        from maa.controller import (
            MaaWin32InputMethodEnum,
            MaaWin32ScreencapMethodEnum,
            Win32Controller,
        )
    except ImportError as exc:
        searched = ", ".join(str(path) for path in _candidate_bin_dirs())
        raise ControllerInitError(
            "Failed to import maa.controller. Ensure the MaaFramework Python binding "
            f"and runtime DLLs are installed. Searched runtime candidates: {searched}"
        ) from exc

    return Win32Controller, MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum


def _resolve_enum(enum_cls, member_name: str):
    try:
        return getattr(enum_cls, member_name)
    except AttributeError as exc:
        raise ControllerInitError(
            f"Required MaaFramework enum member {enum_cls.__name__}.{member_name} is missing."
        ) from exc


def setup_windows_runtime() -> Path | None:
    if os.name != "nt":
        raise ControllerInitError("Win32Controller is only supported on Windows.")

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except AttributeError:
        pass

    bin_dir = _resolve_bin_dir()
    if bin_dir is None:
        return None

    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(bin_dir))

    current_path = os.environ.get("PATH", "")
    if str(bin_dir) not in current_path.split(os.pathsep):
        os.environ["PATH"] = str(bin_dir) + os.pathsep + current_path

    return bin_dir


def create_controller(hwnd: int):
    setup_windows_runtime()
    win32_controller, input_enum, screencap_enum = _import_controller_types()

    controller_kwargs = {
        "screencap_method": _resolve_enum(screencap_enum, DEFAULT_SCREENCAP_METHOD),
        "mouse_method": _resolve_enum(input_enum, DEFAULT_INPUT_METHOD),
        "keyboard_method": _resolve_enum(input_enum, DEFAULT_INPUT_METHOD),
    }

    try:
        return win32_controller(hwnd=hwnd, **controller_kwargs)
    except TypeError:
        try:
            return win32_controller(hWnd=hwnd, **controller_kwargs)
        except Exception as exc:
            raise ControllerInitError(f"Failed to create Win32Controller for hwnd={hwnd}.") from exc
    except Exception as exc:
        raise ControllerInitError(f"Failed to create Win32Controller for hwnd={hwnd}.") from exc


def connect_controller(controller):
    try:
        job = controller.post_connection().wait()
    except Exception as exc:
        raise ControllerInitError("Controller post_connection() failed.") from exc

    if not getattr(job, "succeeded", False):
        raise ControllerInitError("Controller connection failed.")
    return job


def capture_once(controller):
    try:
        job = controller.post_screencap().wait()
    except Exception as exc:
        raise ControllerInitError("Controller post_screencap() failed.") from exc

    if not getattr(job, "succeeded", False):
        raise ControllerInitError("Initial screencap validation failed.")

    image = getattr(controller, "cached_image", None)
    if image is not None:
        return image

    if hasattr(job, "get"):
        try:
            image = job.get()
        except Exception:
            image = None

    if image is None:
        raise ControllerInitError("Screencap succeeded but no image was returned.")

    return image
