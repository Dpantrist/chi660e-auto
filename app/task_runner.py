from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.bootstrap import bootstrap_app
from app.constants import MAIN_WINDOW_TITLE_CANDIDATES, WINDOW_KEYWORD
from app.controller_manager import capture_once, connect_controller, create_controller
from app.cv_config import CVFrontHalfConfig, get_default_cv_front_half_config
from app.dto import WindowSession
from app.errors import Chi660eAutoError, WindowNotFoundError
from app.replay_manager import append_event, finalize_session
from app.runtime_context import RuntimeContext
from app.screenshot_manager import save_debug_capture, save_replay_capture
from app.tasker_manager import bind_tasker, create_tasker
from app.window_linker import find_target_window, list_desktop_windows
from app.window_preset import (
    enforce_window_preset_until_verified,
    verify_window_preset_applied,
)

TECHNIQUE_WINDOW_KEYWORD = "Electrochemical Techniques"
CV_PARAM_WINDOW_KEYWORD = "Cyclic Voltammetry Parameters"

WINDOW_WAIT_TIMEOUT_SEC = 6.0
WINDOW_WAIT_INTERVAL_SEC = 0.15
WINDOW_PRESET_VERIFY_RETRIES = 3


def _canonical_window_keyword(keyword: str | list[str]) -> str:
    if isinstance(keyword, str):
        return keyword
    return keyword[0] if keyword else "window_baseline"


def _save_step_capture(context: RuntimeContext, name: str) -> Path | None:
    if context.controller is None:
        return None

    image = capture_once(context.controller)
    save_debug_capture(image, name)
    if context.replay_record is not None:
        return save_replay_capture(image, context.replay_record.run_dir, name)
    return None


def _log_window_preset_verify(context: RuntimeContext, keyword: str, verify_result: dict) -> None:
    details = verify_result.get("verify_details") or {}
    context.logger.info(
        "Window preset verify: keyword=%s verified=%s client_ok=%s capture_ok=%s dpi_ok=%s style_ok=%s",
        keyword,
        verify_result.get("verified"),
        details.get("client_ok"),
        details.get("capture_ok"),
        details.get("dpi_ok"),
        details.get("style_ok"),
    )
    if verify_result.get("preset_exists") and not verify_result.get("verified"):
        context.logger.warning(
            "Window preset mismatch: keyword=%s expected_client=%s actual_client=%s expected_capture=%s actual_capture=%s expected_dpi=%s actual_dpi=%s",
            keyword,
            details.get("expected_client_rect"),
            details.get("client_rect"),
            details.get("expected_capture_status"),
            details.get("capture_status"),
            details.get("expected_dpi"),
            details.get("dpi"),
        )


def _append_window_preset_event(
    context: RuntimeContext,
    event_name: str,
    keyword: str,
    title: str,
    attempt: int,
    payload: dict,
    level: str = "INFO",
) -> None:
    if context.replay_record is None:
        return
    append_event(
        context.replay_record,
        event_name,
        {
            "keyword": keyword,
            "title": title,
            "attempt": attempt,
            **payload,
        },
        level=level,
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
            _log_window_preset_verify(context, detail.get("keyword", context.window_keyword or ""), detail)


def _register_session(
    context: RuntimeContext,
    keyword: str,
    linked_window,
    controller,
    tasker,
) -> WindowSession:
    session = WindowSession(
        keyword=keyword,
        hwnd=linked_window.hwnd,
        linked_window=linked_window,
        controller=controller,
        tasker=tasker,
    )
    context.sessions[keyword] = session
    return session


def _get_cached_session(context: RuntimeContext, keyword: str) -> WindowSession | None:
    return context.sessions.get(keyword)


def _activate_session(context: RuntimeContext, keyword: str) -> WindowSession:
    session = context.sessions[keyword]
    context.controller = session.controller
    context.tasker = session.tasker
    context.linked_window = session.linked_window
    context.window_keyword = session.keyword
    context.active_session_key = keyword
    context.logger.info("Activating window session: keyword=%s", keyword)
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "window_session_activated",
            {
                "keyword": keyword,
                "title": session.linked_window.title,
            },
        )
    return session


def _session_exists_on_desktop(session: WindowSession) -> bool:
    windows = list_desktop_windows()
    for window in windows:
        if window.hwnd == session.hwnd:
            session.linked_window = window
            session.hwnd = window.hwnd
            return True
    return False


def _connect_window_with_preset_verification(
    context: RuntimeContext,
    matched_windows,
    preset_keyword: str,
    keyword_detail,
):
    last_error: Exception | None = None

    for window in matched_windows:
        try:
            enforce_result = enforce_window_preset_until_verified(
                window.hwnd,
                preset_keyword,
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
                    f"Window preset verification failed for keyword {preset_keyword!r}."
                )

            return window, controller
        except Exception as exc:
            last_error = exc
            if context.replay_record is not None:
                append_event(
                    context.replay_record,
                    "window_connect_retry",
                    {
                        "keyword": keyword_detail,
                        "title": window.title,
                        "hwnd": window.hwnd,
                        "error": str(exc),
                    },
                    level="ERROR",
                )

    if last_error is not None:
        raise last_error
    raise Chi660eAutoError("Failed to connect any matched window.")


def _repair_cached_session(context: RuntimeContext, session: WindowSession) -> bool:
    def _reuse_or_recreate_controller(hwnd: int):
        if session.controller is not None:
            try:
                connect_controller(session.controller)
                return session.controller
            except Exception:
                pass
        return _create_connected_controller(hwnd)

    enforce_result = enforce_window_preset_until_verified(
        session.hwnd,
        session.keyword,
        _reuse_or_recreate_controller,
        logger=context.logger,
        retries=WINDOW_PRESET_VERIFY_RETRIES,
    )
    _record_preset_history(context, session.linked_window.title, enforce_result.get("history") or [])

    if enforce_result.get("preset_exists") and not enforce_result.get("verified"):
        return False

    controller = enforce_result.get("controller")
    if controller is None:
        return False

    rebuilt = controller is not session.controller or session.tasker is None
    session.controller = controller
    if rebuilt:
        tasker = create_tasker()
        bind_tasker(tasker, context.resource, controller)
        session.tasker = tasker
    session.hwnd = session.linked_window.hwnd
    context.logger.info("Window session repaired: keyword=%s rebuilt=%s", session.keyword, rebuilt)
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "window_session_repaired",
            {
                "keyword": session.keyword,
                "title": session.linked_window.title,
                "rebuilt": rebuilt,
                "verified": enforce_result.get("verified"),
            },
        )
    return True


def _session_still_usable(context: RuntimeContext, session: WindowSession) -> bool:
    if session.controller is None or session.tasker is None:
        return False
    if not _session_exists_on_desktop(session):
        return False

    verify_result = verify_window_preset_applied(
        session.hwnd,
        session.controller,
        session.keyword,
        logger=context.logger,
    )
    _log_window_preset_verify(context, session.keyword, verify_result)
    _append_window_preset_event(
        context,
        "window_preset_verify",
        session.keyword,
        session.linked_window.title,
        0,
        verify_result,
    )
    if not verify_result.get("preset_exists") or verify_result.get("verified"):
        return True
    return _repair_cached_session(context, session)


def _ensure_context_window_ready_for_task(context: RuntimeContext) -> None:
    if context.linked_window is None or context.window_keyword is None:
        return
    if context.controller is None:
        raise Chi660eAutoError("Controller is not initialized.")

    verify_result = verify_window_preset_applied(
        context.linked_window.hwnd,
        context.controller,
        context.window_keyword,
        logger=context.logger,
    )
    _log_window_preset_verify(context, context.window_keyword, verify_result)
    _append_window_preset_event(
        context,
        "window_preset_verify",
        context.window_keyword,
        context.linked_window.title,
        0,
        verify_result,
    )
    if not verify_result.get("preset_exists") or verify_result.get("verified"):
        return

    _append_window_preset_event(
        context,
        "window_preset_mismatch",
        context.window_keyword,
        context.linked_window.title,
        0,
        {
            "preset_path": verify_result.get("preset_path"),
            "verify_details": verify_result.get("verify_details"),
            "stage": "pre_task",
        },
        level="ERROR",
    )

    session = _get_cached_session(context, context.window_keyword)
    if session is None:
        session = _register_session(
            context,
            context.window_keyword,
            context.linked_window,
            context.controller,
            context.tasker,
        )

    if not _repair_cached_session(context, session):
        raise Chi660eAutoError(
            f"Window preset verification failed before task for {context.window_keyword!r}."
        )

    _activate_session(context, session.keyword)


def _task_succeeded(job: Any, detail: Any) -> bool:
    job_succeeded = getattr(job, "succeeded", None)
    if job_succeeded is False:
        return False

    detail_status = getattr(detail, "status", None)
    detail_succeeded = getattr(detail_status, "succeeded", None)
    if detail_succeeded is False:
        return False

    return True


def _post_task(context: RuntimeContext, entry: str, pipeline_override: dict[str, Any] | None = None) -> Any:
    if context.tasker is None:
        raise Chi660eAutoError("Tasker is not initialized.")

    _ensure_context_window_ready_for_task(context)
    context.logger.info("Posting task: %s", entry)
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "task_post",
            {"entry": entry, "pipeline_override_keys": sorted((pipeline_override or {}).keys())},
        )

    job = context.tasker.post_task(entry, pipeline_override or {}).wait()
    detail = job.get() if hasattr(job, "get") else None
    if not _task_succeeded(job, detail):
        raise Chi660eAutoError(f"Task execution failed: {entry}")

    return detail


def _wait_for_window(keyword: str | list[str], timeout_sec: float = WINDOW_WAIT_TIMEOUT_SEC):
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        windows = list_desktop_windows()
        try:
            return find_target_window(keyword, windows=windows)
        except WindowNotFoundError as exc:
            last_error = exc
            time.sleep(WINDOW_WAIT_INTERVAL_SEC)

    raise WindowNotFoundError(
        f"Timed out waiting for window with keyword {keyword!r}."
    ) from last_error


def _wait_for_window_close(keyword: str, timeout_sec: float = WINDOW_WAIT_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout_sec
    keyword_lower = keyword.lower()

    while time.monotonic() < deadline:
        windows = list_desktop_windows()
        if not any(keyword_lower in window.title.lower() for window in windows):
            return
        time.sleep(WINDOW_WAIT_INTERVAL_SEC)

    raise Chi660eAutoError(f"Timed out waiting for window {keyword!r} to close.")


def _bind_context_to_window(
    context: RuntimeContext,
    keyword: str | list[str],
    capture_name: str,
) -> RuntimeContext:
    preset_keyword = _canonical_window_keyword(keyword)
    cached_session = _get_cached_session(context, preset_keyword)
    if cached_session is not None:
        if _session_still_usable(context, cached_session):
            context.logger.info("Reusing cached window session: keyword=%s", preset_keyword)
            _activate_session(context, preset_keyword)
            if context.replay_record is not None:
                append_event(
                    context.replay_record,
                    "window_session_reused",
                    {
                        "keyword": preset_keyword,
                        "title": cached_session.linked_window.title,
                    },
                )
            _save_step_capture(context, capture_name)
            return context
        context.logger.info("Cached session invalid, fallback to rebind: keyword=%s", preset_keyword)
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "window_session_fallback_rebind",
                {
                    "keyword": preset_keyword,
                    "title": cached_session.linked_window.title,
                },
            )

    link_result = _wait_for_window(keyword)
    context.logger.info("Binding runtime context to window: %s", link_result.selected_window.title)
    selected_window, controller = _connect_window_with_preset_verification(
        context,
        link_result.matched_windows,
        preset_keyword,
        keyword,
    )
    context.controller = controller

    tasker = create_tasker()
    bind_tasker(tasker, context.resource, controller)
    _register_session(context, preset_keyword, selected_window, controller, tasker)
    _activate_session(context, preset_keyword)

    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "window_connected",
            {
                "keyword": keyword,
                "title": selected_window.title,
                "hwnd": selected_window.hwnd,
            },
        )

    _save_step_capture(context, capture_name)
    return context


def _build_cv_override(config: CVFrontHalfConfig) -> dict[str, dict[str, str]]:
    return {
        "CV_InputHighPotential_Apply": {"input_text": config.high_potential},
        "CV_InputScanRate_Apply": {"input_text": config.scan_rate},
        "CV_InputSweepSegments_Apply": {"input_text": config.sweep_segments},
    }


def run_cv_front_half(config: CVFrontHalfConfig | None = None) -> RuntimeContext:
    context = bootstrap_app()
    config = config or get_default_cv_front_half_config()

    try:
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "cv_front_half_start",
                {
                    "high_potential": config.high_potential,
                    "scan_rate": config.scan_rate,
                    "sweep_segments": config.sweep_segments,
                },
            )

        _post_task(context, "Main_ClickTechnique")
        _save_step_capture(context, "cv_front_half_main_after_technique")

        _bind_context_to_window(context, TECHNIQUE_WINDOW_KEYWORD, "cv_front_half_techniques_window")
        _post_task(context, "Techniques_SelectCVAndConfirm")

        main_session = _get_cached_session(context, WINDOW_KEYWORD)
        fast_switched = False
        if main_session is not None and _session_still_usable(context, main_session):
            _activate_session(context, WINDOW_KEYWORD)
            context.logger.info("Fast switch to cached main session succeeded.")
            if context.replay_record is not None:
                append_event(
                    context.replay_record,
                    "window_session_reused",
                    {
                        "keyword": WINDOW_KEYWORD,
                        "title": main_session.linked_window.title,
                        "stage": "main_fast_switch",
                    },
                )
            fast_switched = True
        if not fast_switched:
            _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, "cv_front_half_main_rebound")
            _post_task(context, "Main_ClickParameters")
        else:
            try:
                _post_task(context, "Main_ClickParameters")
            except Exception:
                context.logger.info("Cached main session post failed, fallback to rebind.")
                if context.replay_record is not None:
                    append_event(
                        context.replay_record,
                        "window_session_fallback_rebind",
                        {
                            "keyword": WINDOW_KEYWORD,
                            "stage": "main_post_task_retry",
                        },
                    )
                _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, "cv_front_half_main_rebound")
                _post_task(context, "Main_ClickParameters")

        _save_step_capture(context, "cv_front_half_main_after_parameters")
        _bind_context_to_window(context, CV_PARAM_WINDOW_KEYWORD, "cv_front_half_cv_window_initial")

        _post_task(context, "CV_FrontHalf_FillAndConfirm", _build_cv_override(config))
        _wait_for_window_close(CV_PARAM_WINDOW_KEYWORD)
        _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, "cv_front_half_main_final")

        if context.replay_record is not None:
            append_event(context.replay_record, "cv_front_half_ready", {"window": MAIN_WINDOW_TITLE_CANDIDATES})
            finalize_session(context.replay_record, status="completed")

        context.logger.info("CV front-half flow completed.")
        return context
    except Exception as exc:
        context.logger.exception("CV front-half flow failed.")
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "error",
                {"message": str(exc), "stage": "cv_front_half"},
                level="ERROR",
            )
            finalize_session(context.replay_record, status="error", error=str(exc))
        try:
            _save_step_capture(context, "cv_front_half_error")
        except Exception:
            context.logger.exception("Failed to save runner error capture.")
        raise
