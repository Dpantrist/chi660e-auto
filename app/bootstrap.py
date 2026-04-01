from __future__ import annotations

import traceback
from dataclasses import asdict

from app.constants import (
    DEBUG_ERROR_CAPTURE_NAME,
    DEBUG_STARTUP_CAPTURE_NAME,
    MAIN_WINDOW_TITLE_CANDIDATES,
    WINDOW_KEYWORD,
)
from app.controller_manager import capture_once, connect_controller, create_controller
from app.controller_manager import get_cached_image_safe
from app.dto import AppStatus, WindowInfo
from app.errors import Chi660eAutoError
from app.logging_utils import get_logger, init_logging
from app.paths import BASE_DIR, LOG_FILE, MAA_LOG_FILE, RESOURCE_DIR, ensure_project_dirs
from app.replay_manager import append_event, create_replay_session, finalize_session
from app.resource_loader import create_resource, load_resource_bundle
from app.runtime_context import RuntimeContext
from app.screenshot_manager import save_debug_capture, save_replay_capture
from app.tasker_manager import bind_tasker, create_tasker
from app.window_linker import find_target_window, list_desktop_windows
from app.window_preset import (
    enforce_window_preset_until_verified,
)

WINDOW_PRESET_VERIFY_RETRIES = 3


def _import_toolkit():
    try:
        from maa.toolkit import Toolkit
    except ImportError as exc:
        raise Chi660eAutoError(
            "Failed to import maa.toolkit. Install the MaaFramework Python binding "
            "for this machine before running the project."
        ) from exc
    return Toolkit


def _window_to_dict(window: WindowInfo) -> dict:
    return asdict(window)


def _initialize_framework_debug(logger) -> None:
    try:
        if MAA_LOG_FILE.exists():
            MAA_LOG_FILE.unlink()
    except Exception:
        logger.debug("Failed to clear previous maa.log.", exc_info=True)

    toolkit = _import_toolkit()
    toolkit.init_option(str(BASE_DIR))
    logger.info("MaaFramework debug option root initialized: %s", BASE_DIR)


def _log_desktop_windows(logger, windows: list[WindowInfo]) -> None:
    logger.info("Desktop windows detected: %s", len(windows))
    for index, window in enumerate(windows, start=1):
        logger.debug(
            "Candidate[%s] hwnd=%s title=%r class=%r visible=%s enabled=%s pid=%s rect=%s",
            index,
            window.hwnd,
            window.title,
            window.class_name,
            window.visible,
            window.enabled,
            window.pid,
            window.rect,
        )


def _capture_failure_scene(context: RuntimeContext) -> None:
    if context.controller is None or context.replay_record is None:
        return

    image = None
    try:
        image = capture_once(context.controller)
    except Exception:
        try:
            image = get_cached_image_safe(context.controller)
        except Exception:
            return

    if image is None:
        return

    save_debug_capture(image, DEBUG_ERROR_CAPTURE_NAME)
    save_replay_capture(image, context.replay_record.run_dir, DEBUG_ERROR_CAPTURE_NAME)


def _log_window_preset_verify(logger, keyword: str, verify_result: dict) -> None:
    details = verify_result.get("verify_details") or {}
    logger.info(
        "Window preset verify: keyword=%s verified=%s client_ok=%s capture_ok=%s dpi_ok=%s style_ok=%s",
        keyword,
        verify_result.get("verified"),
        details.get("client_ok"),
        details.get("capture_ok"),
        details.get("dpi_ok"),
        details.get("style_ok"),
    )
    if verify_result.get("preset_exists") and not verify_result.get("verified"):
        logger.warning(
            "Window preset mismatch: keyword=%s expected_client=%s actual_client=%s expected_capture=%s actual_capture=%s expected_dpi=%s actual_dpi=%s",
            keyword,
            details.get("expected_client_rect"),
            details.get("client_rect"),
            details.get("expected_capture_status"),
            details.get("capture_status"),
            details.get("expected_dpi"),
            details.get("dpi"),
        )


def _create_connected_controller(hwnd: int):
    controller = create_controller(hwnd)
    connect_controller(controller)
    return controller


def _record_preset_history(context: RuntimeContext, title: str, history: list[dict]) -> None:
    for item in history:
        event_name = item.get("event")
        detail = dict(item.get("detail") or {})
        detail.setdefault("title", title)
        detail.setdefault("attempt", item.get("attempt"))
        level = item.get("level", "INFO")
        if context.replay_record is not None:
            append_event(context.replay_record, event_name, detail, level=level)
        if event_name == "window_preset_verify":
            _log_window_preset_verify(context.logger, detail.get("keyword", WINDOW_KEYWORD), detail)


def _connect_main_window_candidates(context: RuntimeContext, link_result):
    last_error: Exception | None = None

    for window in link_result.matched_windows:
        try:
            enforce_result = enforce_window_preset_until_verified(
                window.hwnd,
                WINDOW_KEYWORD,
                _create_connected_controller,
                logger=context.logger,
                retries=WINDOW_PRESET_VERIFY_RETRIES,
            )
            _record_preset_history(context, window.title, enforce_result.get("history") or [])

            controller = enforce_result.get("controller")
            if controller is None:
                controller = _create_connected_controller(window.hwnd)

            if enforce_result.get("preset_exists") and not enforce_result.get("verified"):
                raise Chi660eAutoError(
                    f"Window preset verification failed for keyword {WINDOW_KEYWORD!r}."
                )

            context.controller = controller
            context.linked_window = window
            context.window_keyword = WINDOW_KEYWORD
            return window, controller
        except Exception as exc:
            last_error = exc
            if context.replay_record is not None:
                append_event(
                    context.replay_record,
                    "window_connect_retry",
                    {
                        "match_field": link_result.match_field,
                        "title": window.title,
                        "hwnd": window.hwnd,
                        "error": str(exc),
                    },
                    level="ERROR",
                )

    if last_error is not None:
        raise last_error
    raise Chi660eAutoError("Failed to connect any matched main window.")


def bootstrap_app() -> RuntimeContext:
    ensure_project_dirs()
    init_logging()
    logger = get_logger("bootstrap")
    replay_record = create_replay_session()

    context = RuntimeContext(
        logger=logger,
        replay_dir=replay_record.run_dir,
        replay_record=replay_record,
        status=AppStatus(stage="starting", ready=False, message="Startup initialization in progress."),
    )

    logger.info("Project log file: %s", LOG_FILE)
    logger.info("Replay session directory: %s", replay_record.run_dir)
    append_event(
        replay_record,
        "app_start",
        {
            "base_dir": str(BASE_DIR),
            "window_keyword": WINDOW_KEYWORD,
            "main_window_title_candidates": MAIN_WINDOW_TITLE_CANDIDATES,
            "resource_dir": str(RESOURCE_DIR),
        },
    )

    try:
        _initialize_framework_debug(logger)

        desktop_windows = list_desktop_windows()
        _log_desktop_windows(logger, desktop_windows)

        link_result = find_target_window(MAIN_WINDOW_TITLE_CANDIDATES, windows=desktop_windows)
        context.linked_window = link_result.selected_window
        context.status.stage = "window_search"
        append_event(
            replay_record,
            "window_search",
            {
                "keyword": WINDOW_KEYWORD,
                "title_candidates": MAIN_WINDOW_TITLE_CANDIDATES,
                "match_field": link_result.match_field,
                "matched_windows": [_window_to_dict(window) for window in link_result.matched_windows],
                "selected_window": _window_to_dict(link_result.selected_window),
            },
        )

        selected_window, controller = _connect_main_window_candidates(context, link_result)
        image = capture_once(controller)
        context.status.stage = "window_connected"
        append_event(
            replay_record,
            "window_connected",
            _window_to_dict(selected_window),
        )

        save_debug_capture(image, DEBUG_STARTUP_CAPTURE_NAME)
        save_replay_capture(image, replay_record.run_dir, DEBUG_STARTUP_CAPTURE_NAME)

        resource = create_resource()
        context.resource = resource
        bundle_default_pipeline = load_resource_bundle(resource, RESOURCE_DIR)
        context.status.stage = "resource_loaded"
        append_event(
            replay_record,
            "resource_loaded",
            {
                "resource_dir": str(RESOURCE_DIR),
                "bundle_default_pipeline": str(bundle_default_pipeline),
            },
        )

        tasker = create_tasker()
        context.tasker = tasker
        bind_tasker(tasker, resource, controller)
        context.status.stage = "tasker_bound"
        append_event(replay_record, "tasker_bound", {"tasker_inited": bool(getattr(tasker, "inited", False))})

        # TODO: integrate the pipeline business layer after real nodes are authored.
        # TODO: expose this runtime context to the future GUI layer.
        # TODO: move startup knobs into a dedicated parameter configuration layer.
        # TODO: add a task orchestration layer once entry tasks exist.
        context.status = AppStatus(
            stage="ready",
            ready=True,
            message="controller connected, resource loaded, tasker bound.",
        )
        append_event(
            replay_record,
            "startup_ready",
            {"message": context.status.message},
        )
        finalize_session(replay_record, status="ready")
        logger.info("Startup finished successfully.")
        return context
    except Exception as exc:
        context.status = AppStatus(
            stage="error",
            ready=False,
            message="Startup failed.",
            last_error=f"{type(exc).__name__}: {exc}",
        )
        logger.exception("Bootstrap failed.")
        append_event(
            replay_record,
            "error",
            {
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
            level="ERROR",
        )
        try:
            _capture_failure_scene(context)
        except Exception:
            logger.exception("Failed to save failure capture.")
        finalize_session(
            replay_record,
            status="error",
            error=context.status.last_error,
        )
        raise


def main() -> None:
    try:
        bootstrap_app()
    except Exception:
        print("[ERROR] Bootstrap failed. See logs/app.log and debug/maa.log.")
        raise SystemExit(1)

    print("READY: controller connected, resource loaded, tasker bound.")
