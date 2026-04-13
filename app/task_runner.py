from __future__ import annotations

"""Flow orchestration layer.

Business values come from config modules, visual geometry comes from
visual_action_specs, and pipeline JSON is used only as atomic action or
fallback shells where explicitly retained.
"""

import ctypes
import ctypes.wintypes
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.bootstrap import bootstrap_app
from app.constants import MAIN_WINDOW_TITLE_CANDIDATES, WINDOW_KEYWORD
from app.controller_manager import (
    capture_once,
    connect_controller,
    create_controller,
    post_double_click,
    post_key_click,
)
from app.cursor_guard import move_cursor_to_window_safe_corner
from app.cv_config import CVFrontHalfConfig, get_default_cv_front_half_config
from app.dto import WindowSession
from app.eis_config import EISFrontHalfConfig, get_default_eis_front_half_config
from app.errors import Chi660eAutoError, WindowNotFoundError
from app.gcd_config import (
    GCDFrontHalfConfig,
    build_gcd_run_values,
    get_default_gcd_front_half_config,
)
from app.replay_manager import append_event, finalize_session
from app.run_control import RunStopRequested, sleep_with_run_control
from app.runtime_context import RuntimeContext
from app.screenshot_manager import save_debug_capture, save_replay_capture
from app.tasker_manager import bind_tasker, create_tasker
from app.template_click import (
    VisualActionMode,
    VisualActionResult,
    click_point,
    compute_full_window_roi,
    run_visual_action,
)
from app.visual_action_specs import get_visual_action_spec
from app.window_linker import find_target_window, list_desktop_windows
from app.window_preset import (
    enforce_window_preset_until_verified,
    verify_window_preset_applied,
)
from app.window_restore import restore_window, window_needs_restore, SW_RESTORE

TECHNIQUE_WINDOW_KEYWORD = "Electrochemical Techniques"
CV_PARAM_WINDOW_KEYWORD = "Cyclic Voltammetry Parameters"
EIS_PARAM_WINDOW_KEYWORD = "A.C. Impedance Parameters"
GCD_PARAM_WINDOW_KEYWORD = "Chronopotentiometry Parameters"
OCP_WINDOW_KEYWORD = "Open Circuit Potential"

WINDOW_WAIT_TIMEOUT_SEC = 6.0
WINDOW_WAIT_INTERVAL_SEC = 0.15
WINDOW_PRESET_VERIFY_RETRIES = 3
MIN_ACTION_GAP_SEC = 1.0
TECHNIQUE_STATE_SETTLE_SEC = 1.0
TECHNIQUE_STATE_RECHECK_ATTEMPTS = 3
CONTROL_MENU_OCP_OFFSET_X = 0
CONTROL_MENU_OCP_OFFSET_Y = 275

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
BM_CLICK = 0x00F5
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

USER32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


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
        def _attempt_connect_current_window():
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

        try:
            if not _restore_window_if_needed(context, window.hwnd, "connect_window_with_preset_verification"):
                last_error = Chi660eAutoError("Window restore failed before preset verification.")
                continue
            return _attempt_connect_current_window()
        except Exception as exc:
            if _looks_like_restoreable_window_error(exc):
                try:
                    if not _restore_window_if_needed(context, window.hwnd, "connect_window_retry"):
                        last_error = Chi660eAutoError("Window restore failed before retry verification.")
                        continue
                    return _attempt_connect_current_window()
                except Exception as retry_exc:
                    exc = retry_exc
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

    if not _restore_window_if_needed(context, session.hwnd, "repair_cached_session"):
        return False

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
    if not _restore_window_if_needed(context, session.hwnd, "session_still_usable"):
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
    if not _restore_window_if_needed(
        context,
        context.linked_window.hwnd,
        "ensure_context_window_ready_for_task",
    ):
        raise Chi660eAutoError("Window restore failed before task verification.")

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


def _enforce_min_action_gap(context: RuntimeContext, action_name: str) -> None:
    last_completed = context.last_action_completed_at
    if last_completed is None:
        context.logger.info("Action gap satisfied: action=%s", action_name)
        return

    elapsed = time.monotonic() - last_completed
    remaining = MIN_ACTION_GAP_SEC - elapsed
    if remaining > 0:
        context.logger.info("Action gap enforced: action=%s sleep=%.3fs", action_name, remaining)
        _sleep_with_stop(context, remaining, f"action_gap:{action_name}")
        return

    context.logger.info("Action gap satisfied: action=%s", action_name)


def _mark_action_completed(context: RuntimeContext) -> None:
    context.last_action_completed_at = time.monotonic()


def _emit_gui_event(context: RuntimeContext, event_type: str, payload: dict[str, Any]) -> None:
    sink = getattr(context, "gui_event_sink", None)
    if sink is None:
        return
    sink(event_type, payload)


def _emit_runtime_message(context: RuntimeContext, message: str) -> None:
    _emit_gui_event(context, "runtime_message", {"message": message})


def _format_gui_error_message(exc: Exception) -> str:
    """压缩异常文本，避免把过长技术细节直接塞进 GUI。"""
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message.splitlines()[0][:120]


def _raise_if_stop_requested(context: RuntimeContext, stage: str) -> None:
    if context.run_control is None:
        return
    context.run_control.raise_if_stop_requested(stage)


def _sleep_with_stop(context: RuntimeContext, total_sec: float, stage: str) -> None:
    sleep_with_run_control(context.run_control, total_sec, stage=stage)


def _prepare_visual_task(
    context: RuntimeContext,
    entry: str,
    relocate_cursor_before_task: bool = True,
) -> dict[str, Any]:
    if not relocate_cursor_before_task:
        result = {
            "success": None,
            "x": None,
            "y": None,
            "hwnd": context.linked_window.hwnd if context.linked_window is not None else None,
            "reason": "post_run_polling",
        }
        context.logger.info(
            "Cursor relocation skipped before task: entry=%s reason=%s",
            entry,
            result["reason"],
        )
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "cursor_relocation_skipped_before_task",
                {
                    "entry": entry,
                    "keyword": context.window_keyword,
                    "title": context.linked_window.title if context.linked_window is not None else None,
                    **result,
                },
            )
        return result

    if context.linked_window is None:
        result = {
            "success": False,
            "x": None,
            "y": None,
            "hwnd": None,
            "reason": "linked_window_missing",
        }
    else:
        result = move_cursor_to_window_safe_corner(
            context.linked_window.hwnd,
            logger=context.logger,
        )

    context.logger.info(
        "Cursor relocated before task: entry=%s success=%s pos=(%s,%s)",
        entry,
        result.get("success"),
        result.get("x"),
        result.get("y"),
    )
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "cursor_relocated_before_task",
            {
                "entry": entry,
                "keyword": context.window_keyword,
                "title": context.linked_window.title if context.linked_window is not None else None,
                **result,
            },
        )
    return result


def _post_task(context: RuntimeContext, entry: str, pipeline_override: dict[str, Any] | None = None) -> Any:
    if context.tasker is None:
        raise Chi660eAutoError("Tasker is not initialized.")

    _raise_if_stop_requested(context, f"task:{entry}")
    _enforce_min_action_gap(context, f"task:{entry}")
    _ensure_context_window_ready_for_task(context)
    _prepare_visual_task(context, entry)
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

    _mark_action_completed(context)
    return detail


def _post_task_expect_window_with_recovery(
    context: RuntimeContext,
    click_entry: str,
    expected_window_keyword: str | list[str],
    replay_name: str,
    max_attempts: int = 2,
    initial_wait_timeout: float = 1.5,
    retry_wait_timeout: float = 2.0,
) -> RuntimeContext:
    attempts = max(1, max_attempts)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        _post_task(context, click_entry)
        wait_timeout = initial_wait_timeout if attempt == 1 else retry_wait_timeout
        try:
            bound_context = _bind_context_to_window(
                context,
                expected_window_keyword,
                replay_name,
                timeout_sec=wait_timeout,
            )
            if attempt > 1:
                context.logger.info(
                    "Task recovery succeeded: entry=%s expected_window=%s attempt=%s",
                    click_entry,
                    expected_window_keyword,
                    attempt,
                )
                if context.replay_record is not None:
                    append_event(
                        context.replay_record,
                        "task_recovery_succeeded",
                        {
                            "entry": click_entry,
                            "expected_window": expected_window_keyword,
                            "attempt": attempt,
                        },
                    )
            return bound_context
        except WindowNotFoundError as exc:
            last_error = exc
            if attempt >= attempts:
                context.logger.error(
                    "Task recovery failed: entry=%s expected_window=%s attempt=%s",
                    click_entry,
                    expected_window_keyword,
                    attempt,
                )
                if context.replay_record is not None:
                    append_event(
                        context.replay_record,
                        "task_recovery_failed",
                        {
                            "entry": click_entry,
                            "expected_window": expected_window_keyword,
                            "attempt": attempt,
                            "error": str(exc),
                        },
                        level="ERROR",
                    )
                raise

            context.logger.warning(
                "Task recovery triggered: entry=%s expected_window=%s attempt=%s",
                click_entry,
                expected_window_keyword,
                attempt + 1,
            )
            if context.replay_record is not None:
                append_event(
                    context.replay_record,
                    "task_recovery_triggered",
                    {
                        "entry": click_entry,
                        "expected_window": expected_window_keyword,
                        "attempt": attempt + 1,
                        "error": str(exc),
                    },
                    level="WARNING",
                )

    if last_error is not None:
        raise last_error
    raise Chi660eAutoError(f"Failed to open expected window after task {click_entry!r}.")


def _append_visual_action_event(
    context: RuntimeContext,
    event_name: str,
    spec,
    result: VisualActionResult | None = None,
    extra: dict[str, Any] | None = None,
    level: str = "INFO",
) -> None:
    if context.replay_record is None:
        return

    detail: dict[str, Any] = {
        "name": spec.name,
        "mode": spec.mode.value,
        "template": spec.template,
    }
    if result is not None:
        detail.update(
            {
                "matched": result.matched,
                "clicked": result.clicked,
                "box": result.box,
                "score": result.score,
                "click_point": result.click_point,
                "computed_center": result.computed_center,
                "relative_roi": result.relative_roi,
                "computed_rect": result.computed_rect,
                "image_shape": result.image_shape,
                "actual_roi": result.actual_roi,
                "roi_mode": result.roi_mode,
                "attempt": result.attempt,
                "error": result.error,
            }
        )
    if extra:
        detail.update(extra)

    append_event(context.replay_record, event_name, detail, level=level)


def _is_selected_state_spec(spec) -> bool:
    name_lower = spec.name.lower()
    template_lower = spec.template.lower()
    if spec.state_only:
        return True
    if "unselected" in name_lower or "unselected" in template_lower:
        return False
    return "selected" in name_lower or "_selected" in template_lower


def _enforce_selected_state_spec(spec, *, intended_click: bool) -> None:
    if not _is_selected_state_spec(spec):
        return
    if spec.mode != VisualActionMode.DETECT_ONLY or intended_click:
        raise Chi660eAutoError(f"Selected-state spec must be detect-only: {spec.name}")


def _validate_visual_action_roi_policy(spec, result: VisualActionResult) -> None:
    if not getattr(spec, "require_full_window_roi", False):
        return

    expected_roi = compute_full_window_roi(result.image_shape)
    if result.actual_roi != expected_roi or result.roi_mode != "full_window":
        raise Chi660eAutoError(
            f"Technique spec requires full-window ROI, but cropped ROI was used: {spec.name}"
        )


def _log_visual_action_roi_policy(context: RuntimeContext, spec, result: VisualActionResult) -> None:
    if not getattr(spec, "require_full_window_roi", False):
        return
    context.logger.info(
        "Technique ROI policy: name=%s template=%s score=%.6f box=%s actual_roi=%s image_shape=%s require_full_window_roi=%s",
        spec.name,
        spec.template,
        result.score,
        result.box,
        result.actual_roi,
        result.image_shape,
        spec.require_full_window_roi,
    )


def _run_visual_action_once(
    context: RuntimeContext,
    spec_name: str,
    relocate_cursor_before_task: bool = True,
) -> VisualActionResult:
    spec = get_visual_action_spec(spec_name)
    _raise_if_stop_requested(context, f"visual:{spec.name}")
    _enforce_selected_state_spec(spec, intended_click=False)
    _ensure_context_window_ready_for_task(context)
    if spec.mode != VisualActionMode.DETECT_ONLY:
        _enforce_min_action_gap(context, f"visual:{spec.name}")
    _prepare_visual_task(
        context,
        spec.name,
        relocate_cursor_before_task=relocate_cursor_before_task,
    )

    context.logger.info(
        "Visual action start: name=%s mode=%s template=%s",
        spec.name,
        spec.mode.value,
        spec.template,
    )
    _append_visual_action_event(context, "visual_action_start", spec)

    result = run_visual_action(context.controller, spec, logger=context.logger)
    _validate_visual_action_roi_policy(spec, result)
    _log_visual_action_roi_policy(context, spec, result)

    if spec.mode == VisualActionMode.DETECT_ONLY:
        context.logger.info(
            "Visual action detect-only matched: name=%s matched=%s box=%s score=%.6f actual_roi=%s roi_mode=%s",
            spec.name,
            result.matched,
            result.box,
            result.score,
            result.actual_roi,
            result.roi_mode,
        )
        _append_visual_action_event(context, "visual_action_detect_only", spec, result)
        if result.success:
            context.logger.info("Visual action succeeded: name=%s", spec.name)
            _append_visual_action_event(context, "visual_action_succeeded", spec, result)
        return result

    context.logger.info(
        "Visual action matched: name=%s matched=%s box=%s score=%.6f actual_roi=%s roi_mode=%s",
        spec.name,
        result.matched,
        result.box,
        result.score,
        result.actual_roi,
        result.roi_mode,
    )
    _append_visual_action_event(context, "visual_action_match", spec, result)

    if result.computed_rect is not None:
        context.logger.info(
            "Visual action computed row rect: name=%s rect=%s",
            spec.name,
            result.computed_rect,
        )

    if result.clicked:
        context.logger.info(
            "Visual action clicked: name=%s click_point=%s",
            spec.name,
            result.click_point,
        )
        _append_visual_action_event(context, "visual_action_click", spec, result)
        _mark_action_completed(context)

    if result.success and spec.expected_window_keyword is None:
        context.logger.info("Visual action succeeded: name=%s", spec.name)
        _append_visual_action_event(context, "visual_action_succeeded", spec, result)

    return result


def _run_visual_action_click(
    context: RuntimeContext,
    spec_name: str,
) -> VisualActionResult:
    spec = get_visual_action_spec(spec_name)
    _raise_if_stop_requested(context, f"visual_click:{spec.name}")
    _enforce_selected_state_spec(spec, intended_click=True)
    result = _run_visual_action_once(context, spec_name)
    if not result.success:
        raise Chi660eAutoError(f"Visual action failed: {spec_name}")
    return result


def _locate_visual_action_point(
    context: RuntimeContext,
    spec_name: str,
) -> VisualActionResult:
    spec = get_visual_action_spec(spec_name)
    _raise_if_stop_requested(context, f"visual_locate:{spec.name}")
    _ensure_context_window_ready_for_task(context)
    _prepare_visual_task(context, spec.name)

    context.logger.info(
        "Visual action locate start: name=%s mode=%s template=%s",
        spec.name,
        spec.mode.value,
        spec.template,
    )
    _append_visual_action_event(context, "visual_action_locate_start", spec)

    result = run_visual_action(
        context.controller,
        spec,
        logger=context.logger,
        perform_click=False,
    )
    _validate_visual_action_roi_policy(spec, result)
    _log_visual_action_roi_policy(context, spec, result)

    context.logger.info(
        "Visual action locate matched: name=%s matched=%s box=%s score=%.6f actual_roi=%s roi_mode=%s",
        spec.name,
        result.matched,
        result.box,
        result.score,
        result.actual_roi,
        result.roi_mode,
    )
    _append_visual_action_event(context, "visual_action_locate_match", spec, result)

    if result.computed_rect is not None:
        context.logger.info(
            "Visual action computed row rect: name=%s rect=%s",
            spec.name,
            result.computed_rect,
        )

    if not result.success or result.click_point is None:
        raise Chi660eAutoError(f"Visual action locate failed: {spec_name}")

    context.logger.info(
        "Visual action locate point: name=%s click_point=%s",
        spec.name,
        result.click_point,
    )
    _append_visual_action_event(context, "visual_action_locate_point", spec, result)
    return result


def _run_visual_action_expect_window_with_fallback(
    context: RuntimeContext,
    spec_name: str,
    replay_name: str,
    pipeline_fallback_entry: str,
) -> RuntimeContext:
    # This helper is only for window-opening actions that still retain an
    # explicit pipeline fallback shell. Main flow decisions remain in Python.
    spec = get_visual_action_spec(spec_name)
    _enforce_selected_state_spec(spec, intended_click=True)
    last_error: Exception | None = None

    for attempt in range(1, max(1, spec.max_attempts) + 1):
        result = _run_visual_action_once(context, spec_name)
        result.attempt = attempt

        if not result.success:
            last_error = Chi660eAutoError(f"Visual action failed: {spec_name}")
        else:
            timeout = spec.timeout_sec if attempt == 1 else spec.retry_timeout_sec
            try:
                bound_context = _bind_context_to_window(
                    context,
                    spec.expected_window_keyword or replay_name,
                    replay_name,
                    timeout_sec=timeout,
                )
                context.logger.info(
                    "Visual action succeeded: name=%s expected_window=%s",
                    spec.name,
                    spec.expected_window_keyword,
                )
                _append_visual_action_event(
                    context,
                    "visual_action_succeeded",
                    spec,
                    result,
                    {"expected_window": spec.expected_window_keyword},
                )
                return bound_context
            except WindowNotFoundError as exc:
                last_error = exc

        if attempt < max(1, spec.max_attempts):
            context.logger.warning(
                "Visual action recovery triggered: name=%s attempt=%s",
                spec.name,
                attempt + 1,
            )
            _append_visual_action_event(
                context,
                "visual_action_recovery_triggered",
                spec,
                result,
                {
                    "attempt": attempt + 1,
                    "expected_window": spec.expected_window_keyword,
                    "error": str(last_error) if last_error is not None else None,
                },
                level="WARNING",
            )

    if not spec.allow_pipeline_fallback:
        _emit_runtime_message(context, f"界面操作失败：{spec.name}")
        if last_error is not None:
            raise last_error
        raise Chi660eAutoError(f"Visual action failed: {spec_name}")

    context.logger.warning(
        "Visual action fallback to pipeline: name=%s entry=%s",
        spec.name,
        pipeline_fallback_entry,
    )
    _append_visual_action_event(
        context,
        "visual_action_fallback",
        spec,
        extra={
            "pipeline_entry": pipeline_fallback_entry,
            "expected_window": spec.expected_window_keyword,
            "error": str(last_error) if last_error is not None else None,
        },
        level="WARNING",
    )
    try:
        return _post_task_expect_window_with_recovery(
            context,
            pipeline_fallback_entry,
            spec.expected_window_keyword or replay_name,
            replay_name,
            max_attempts=max(1, spec.max_attempts),
            initial_wait_timeout=spec.timeout_sec,
            retry_wait_timeout=spec.retry_timeout_sec,
        )
    except Exception:
        _emit_runtime_message(context, f"界面操作失败：{spec.name}")
        raise


def _wait_for_window(
    keyword: str | list[str],
    timeout_sec: float | None = None,
    interval_sec: float | None = None,
    context: RuntimeContext | None = None,
):
    timeout = WINDOW_WAIT_TIMEOUT_SEC if timeout_sec is None else timeout_sec
    interval = WINDOW_WAIT_INTERVAL_SEC if interval_sec is None else interval_sec
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        if context is not None:
            _raise_if_stop_requested(context, f"wait_window:{_canonical_window_keyword(keyword)}")
        windows = list_desktop_windows()
        try:
            return find_target_window(keyword, windows=windows)
        except WindowNotFoundError as exc:
            last_error = exc
            if context is not None:
                _sleep_with_stop(context, interval, f"wait_window:{_canonical_window_keyword(keyword)}")
            else:
                time.sleep(interval)

    raise WindowNotFoundError(
        f"Timed out waiting for window with keyword {keyword!r}."
    ) from last_error


def _wait_for_window_close(
    keyword: str,
    timeout_sec: float = WINDOW_WAIT_TIMEOUT_SEC,
    context: RuntimeContext | None = None,
) -> None:
    deadline = time.monotonic() + timeout_sec
    keyword_lower = keyword.lower()

    while time.monotonic() < deadline:
        if context is not None:
            _raise_if_stop_requested(context, f"wait_window_close:{keyword}")
        windows = list_desktop_windows()
        if not any(keyword_lower in window.title.lower() for window in windows):
            return
        if context is not None:
            _sleep_with_stop(context, WINDOW_WAIT_INTERVAL_SEC, f"wait_window_close:{keyword}")
        else:
            time.sleep(WINDOW_WAIT_INTERVAL_SEC)

    raise Chi660eAutoError(f"Timed out waiting for window {keyword!r} to close.")


def _normalize_ui_text(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _window_text_by_hwnd(hwnd: int) -> str:
    length = USER32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
    if length > 0:
        buffer = ctypes.create_unicode_buffer(length + 1)
        USER32.GetWindowTextW(ctypes.c_void_p(hwnd), buffer, len(buffer))
        if buffer.value:
            return buffer.value

    message_length = int(USER32.SendMessageW(ctypes.c_void_p(hwnd), WM_GETTEXTLENGTH, 0, 0))
    if message_length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(message_length + 1)
    USER32.SendMessageW(ctypes.c_void_p(hwnd), WM_GETTEXT, len(buffer), buffer)
    return buffer.value


def _class_name_by_hwnd(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(ctypes.c_void_p(hwnd), buffer, len(buffer))
    return buffer.value


def _window_rect_by_hwnd(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    USER32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    return (
        int(rect.left),
        int(rect.top),
        int(rect.right - rect.left),
        int(rect.bottom - rect.top),
    )


def _restore_window_if_needed(context: RuntimeContext, hwnd: int, reason: str) -> bool:
    if not window_needs_restore(hwnd):
        return True

    _emit_runtime_message(context, "检测到窗口最小化，尝试恢复窗口")
    restored = restore_window(hwnd, logger=context.logger, attempts=15)
    if restored:
        _emit_runtime_message(context, "窗口已恢复到前台")
        return True

    context.logger.warning("Window restore failed: hwnd=%s reason=%s", hwnd, reason)
    _emit_runtime_message(context, "窗口恢复失败：窗口仍处于最小化或不可见状态")
    return False


def _looks_like_restoreable_window_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("0x0", "0×0", "0*0", "minimize", "minimized", "iconic", "capture", "rect")
    )


def _enum_child_controls(hwnd: int, *, visible_only: bool = True) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(child_hwnd, _lparam):
        child = int(child_hwnd)
        if visible_only and not USER32.IsWindowVisible(ctypes.c_void_p(child)):
            return True
        children.append(
            {
                "hwnd": child,
                "class_name": _class_name_by_hwnd(child),
                "text": _window_text_by_hwnd(child),
                "rect": _window_rect_by_hwnd(child),
            }
        )
        return True

    USER32.EnumChildWindows(ctypes.c_void_p(hwnd), callback, 0)
    return children


def _find_nearest_control_to_label(
    children: list[dict[str, Any]],
    label_keywords: tuple[str, ...],
    allowed_classes: tuple[str, ...],
) -> dict[str, Any] | None:
    normalized_keywords = tuple(_normalize_ui_text(item) for item in label_keywords)
    labels = [
        child
        for child in children
        if child["text"]
        and any(keyword in _normalize_ui_text(child["text"]) for keyword in normalized_keywords)
    ]
    if not labels:
        return None

    best_candidate: dict[str, Any] | None = None
    best_rank: tuple[float, float, float] | None = None
    for label in labels:
        lx, ly, lw, lh = label["rect"]
        label_right = lx + lw
        label_mid_y = ly + (lh / 2.0)
        for child in children:
            if child["hwnd"] == label["hwnd"] or child["class_name"] not in allowed_classes:
                continue
            cx, cy, cw, ch = child["rect"]
            if cx + cw <= label_right:
                continue
            vertical_delta = abs((cy + (ch / 2.0)) - label_mid_y)
            horizontal_delta = max(0.0, cx - label_right)
            rank = (vertical_delta, horizontal_delta, -float(cw))
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_candidate = child
    return best_candidate


def _click_screen_point(x: int, y: int) -> None:
    USER32.SetCursorPos(int(x), int(y))
    USER32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    USER32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _click_window_center_by_hwnd(hwnd: int) -> None:
    left, top, width, height = _window_rect_by_hwnd(hwnd)
    _click_screen_point(left + max(1, width // 2), top + max(1, height // 2))


def _find_ocp_value_control(ocp_window) -> dict[str, Any]:
    children = _enum_child_controls(ocp_window.hwnd)
    control = _find_nearest_control_to_label(
        children,
        ("Open Circuit Potential",),
        ("Edit", "Static"),
    )
    if control is None:
        raise Chi660eAutoError("Failed to resolve OCP value control from result window.")
    return control


def _read_text_from_hwnd(hwnd: int) -> str:
    return _window_text_by_hwnd(hwnd).strip()


def _format_decimal_value(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _normalize_ocp_for_init_e(raw_text: str) -> str:
    raw_compact = (raw_text or "").strip()
    match = re.search(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw_compact)
    if match is None:
        raise Chi660eAutoError(f"Failed to parse OCP numeric value from text: {raw_text!r}")

    try:
        value = Decimal(match.group(0))
    except (InvalidOperation, ValueError) as exc:
        raise Chi660eAutoError(f"Failed to normalize OCP value: {raw_text!r}") from exc

    if value < 0:
        return "0"
    return _format_decimal_value(value)


def _find_ocp_ok_button(ocp_window) -> dict[str, Any] | None:
    children = _enum_child_controls(ocp_window.hwnd)
    for child in children:
        if child["class_name"] != "Button":
            continue
        if _normalize_ui_text(child["text"]) in {"ok", "纭畾"}:
            return child
    return None


def _click_ocp_dialog_ok(context: RuntimeContext, ocp_window) -> None:
    button = _find_ocp_ok_button(ocp_window)
    if button is None:
        raise Chi660eAutoError("Failed to find OK button in OCP window.")

    _enforce_min_action_gap(context, "ocp_dialog_ok")
    USER32.ShowWindow(ctypes.c_void_p(ocp_window.hwnd), SW_RESTORE)
    USER32.SetForegroundWindow(ctypes.c_void_p(ocp_window.hwnd))
    USER32.SendMessageW(ctypes.c_void_p(button["hwnd"]), BM_CLICK, 0, 0)
    _mark_action_completed(context)
    _sleep_with_stop(context, WINDOW_WAIT_INTERVAL_SEC, "ocp_dialog_ok_settle")

    if any(OCP_WINDOW_KEYWORD.lower() in window.title.lower() for window in list_desktop_windows()):
        _enforce_min_action_gap(context, "ocp_dialog_ok_physical")
        _click_window_center_by_hwnd(button["hwnd"])
        _mark_action_completed(context)

    _wait_for_window_close(OCP_WINDOW_KEYWORD, context=context)
    context.logger.info("OCP dialog OK clicked.")


def _click_open_circuit_potential_from_control_menu(context: RuntimeContext) -> None:
    context.logger.info("Main control menu click start.")
    control_result = _run_visual_action_click(context, "Main_ClickControl")
    context.logger.info("Main control menu click succeeded.")

    anchor_point = control_result.click_point
    if anchor_point is None and control_result.box is not None:
        anchor_point = (
            int(control_result.box[0] + (control_result.box[2] / 2)),
            int(control_result.box[1] + (control_result.box[3] / 2)),
        )
    if anchor_point is None:
        raise Chi660eAutoError("Control menu anchor point is missing.")

    ocp_click_point = (
        int(anchor_point[0] + CONTROL_MENU_OCP_OFFSET_X),
        int(anchor_point[1] + CONTROL_MENU_OCP_OFFSET_Y),
    )
    _enforce_min_action_gap(context, "click:Main_ControlMenu:OpenCircuitPotential")
    click_point(
        context.controller,
        ocp_click_point[0],
        ocp_click_point[1],
        ocp_click_point,
    )
    _mark_action_completed(context)
    context.logger.info(
        "Open Circuit Potential menu fixed-offset click: anchor=%s offset=(%s,%s) point=%s",
        anchor_point,
        CONTROL_MENU_OCP_OFFSET_X,
        CONTROL_MENU_OCP_OFFSET_Y,
        ocp_click_point,
    )
    context.logger.info("Checking OCP result window after control menu action.")


def _read_open_circuit_potential_value(context: RuntimeContext, ocp_window) -> str:
    context.logger.info(
        "OCP window detected: title=%s class=%s",
        ocp_window.title,
        ocp_window.class_name,
    )
    control = _find_ocp_value_control(ocp_window)
    context.logger.info(
        "OCP value control resolved: hwnd=%s class=%s method=label_association",
        control["hwnd"],
        control["class_name"],
    )
    raw_text = _read_text_from_hwnd(control["hwnd"])
    context.logger.info("OCP raw text read: %s", raw_text)
    return raw_text


def _read_and_close_open_circuit_potential(context: RuntimeContext) -> dict[str, str]:
    try:
        context.logger.info("Open circuit potential read start.")
        _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, "ocp_main_initial")
        _click_open_circuit_potential_from_control_menu(context)
        _bind_context_to_window(
            context,
            OCP_WINDOW_KEYWORD,
            "ocp_result_window",
            timeout_sec=WINDOW_WAIT_TIMEOUT_SEC,
            interval_sec=WINDOW_WAIT_INTERVAL_SEC,
        )
        if context.linked_window is None:
            raise Chi660eAutoError("OCP result window was detected but runtime context was not bound.")
        ocp_window = context.linked_window

        raw_text = _read_open_circuit_potential_value(context, ocp_window)
        normalized_value = _normalize_ocp_for_init_e(raw_text)
        context.logger.info("OCP raw: %s", raw_text)
        context.logger.info("OCP normalized for Init E: raw=%s normalized=%s", raw_text, normalized_value)
        _emit_runtime_message(context, f"读取开路电压 {raw_text}")

        _click_ocp_dialog_ok(context, ocp_window)
        _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, "ocp_main_final")
        return {
            "raw_text": raw_text,
            "normalized_value": normalized_value,
        }
    except RunStopRequested:
        raise
    except Exception as exc:
        _emit_runtime_message(context, f"读取开路电压失败：{_format_gui_error_message(exc)}")
        raise


def _bind_context_to_window(
    context: RuntimeContext,
    keyword: str | list[str],
    capture_name: str,
    timeout_sec: float | None = None,
    interval_sec: float | None = None,
) -> RuntimeContext:
    try:
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

        link_result = _wait_for_window(
            keyword,
            timeout_sec=timeout_sec,
            interval_sec=interval_sec,
            context=context,
        )
        if not _restore_window_if_needed(
            context,
            link_result.selected_window.hwnd,
            "bind_context_to_window",
        ):
            raise Chi660eAutoError("Window restore failed before binding.")

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
    except RunStopRequested:
        raise
    except Exception as exc:
        _emit_runtime_message(context, f"连接窗口失败：{_format_gui_error_message(exc)}")
        raise


def _confirm_technique_selected_after_click(context: RuntimeContext) -> bool:
    context.logger.info("Technique state settle wait: seconds=%.1f", TECHNIQUE_STATE_SETTLE_SEC)
    _sleep_with_stop(context, TECHNIQUE_STATE_SETTLE_SEC, "technique_cv_state_settle")

    for attempt in range(1, TECHNIQUE_STATE_RECHECK_ATTEMPTS + 1):
        selected_result = _run_visual_action_once(context, "Techniques_SelectCV_Selected_Check")
        context.logger.info(
            "Technique selected recheck: attempt=%s matched=%s score=%.6f",
            attempt,
            selected_result.matched,
            selected_result.score,
        )
        if selected_result.matched:
            context.logger.info("Technique selection confirmed by selected-check")
            return True

        unselected_result = _run_visual_action_once(context, "Techniques_SelectCV_Unselected_Check")
        context.logger.info(
            "Technique unselected recheck: attempt=%s matched=%s score=%.6f",
            attempt,
            unselected_result.matched,
            unselected_result.score,
        )
        if not unselected_result.matched:
            context.logger.info("Technique selection confirmed by unselected disappearance")
            return True

        if attempt < TECHNIQUE_STATE_RECHECK_ATTEMPTS:
            context.logger.info("Technique state settle wait: seconds=%.1f", TECHNIQUE_STATE_SETTLE_SEC)
            _sleep_with_stop(context, TECHNIQUE_STATE_SETTLE_SEC, "technique_cv_state_recheck")

    return False


def _run_techniques_select_cv_and_confirm(context: RuntimeContext) -> None:
    _emit_runtime_message(context, "选择 CV 方法")
    selected_result = _run_visual_action_once(context, "Techniques_SelectCV_Selected_Check")
    context.logger.info(
        "Technique initial selected check: matched=%s score=%.6f",
        selected_result.matched,
        selected_result.score,
    )
    if selected_result.matched:
        context.logger.info("Technique item already selected: no click needed")
        context.logger.info(
            "Visual action skipped click: name=%s reason=already_selected",
            "Techniques_SelectCV_Unselected_Click",
        )
        _append_visual_action_event(
            context,
            "visual_action_skip_click",
            get_visual_action_spec("Techniques_SelectCV_Unselected_Click"),
            selected_result,
            {"reason": "already_selected"},
        )
    else:
        unselected_result = _run_visual_action_once(context, "Techniques_SelectCV_Unselected_Check")
        context.logger.info(
            "Technique initial unselected check: matched=%s score=%.6f",
            unselected_result.matched,
            unselected_result.score,
        )
        if not unselected_result.matched:
            raise Chi660eAutoError(
                "Technique CV state is ambiguous: neither selected nor unselected matched."
            )

        context.logger.info("Technique item unselected confirmed: click once")
        click_result = _run_visual_action_once(context, "Techniques_SelectCV_Unselected_Click")
        if not click_result.success:
            raise Chi660eAutoError("Technique item click failed for unselected CV item.")

        if not _confirm_technique_selected_after_click(context):
            context.logger.error("Technique selection failed after state recheck attempts")
            raise Chi660eAutoError("Technique selection failed after state recheck attempts.")

    for attempt in range(1, 3):
        ok_result = _run_visual_action_once(context, "Techniques_ClickOK")
        if ok_result.success:
            context.logger.info("Visual action succeeded: name=%s", "Techniques_ClickOK")
            _append_visual_action_event(
                context,
                "visual_action_succeeded",
                get_visual_action_spec("Techniques_ClickOK"),
                ok_result,
            )
            return
        if attempt < 2:
            context.logger.warning("Technique OK click retry: attempt=%s", attempt + 1)

    raise Chi660eAutoError("Technique OK click failed.")


def _confirm_technique_eis_selected_after_click(context: RuntimeContext) -> bool:
    context.logger.info("Technique state settle wait: seconds=%.1f", TECHNIQUE_STATE_SETTLE_SEC)
    _sleep_with_stop(context, TECHNIQUE_STATE_SETTLE_SEC, "technique_eis_state_settle")

    for attempt in range(1, TECHNIQUE_STATE_RECHECK_ATTEMPTS + 1):
        selected_result = _run_visual_action_once(context, "Techniques_SelectEIS_Selected_Check")
        context.logger.info(
            "Technique EIS selected recheck: attempt=%s matched=%s score=%.6f",
            attempt,
            selected_result.matched,
            selected_result.score,
        )
        if selected_result.matched:
            context.logger.info("Technique selection confirmed by selected-check")
            return True

        unselected_result = _run_visual_action_once(context, "Techniques_SelectEIS_Unselected_Check")
        context.logger.info(
            "Technique EIS unselected recheck: attempt=%s matched=%s score=%.6f",
            attempt,
            unselected_result.matched,
            unselected_result.score,
        )
        if not unselected_result.matched:
            context.logger.info("Technique selection confirmed by unselected disappearance")
            return True

        if attempt < TECHNIQUE_STATE_RECHECK_ATTEMPTS:
            context.logger.info("Technique state settle wait: seconds=%.1f", TECHNIQUE_STATE_SETTLE_SEC)
            _sleep_with_stop(context, TECHNIQUE_STATE_SETTLE_SEC, "technique_eis_state_recheck")

    return False


def _run_techniques_select_eis_and_confirm(context: RuntimeContext) -> None:
    _emit_runtime_message(context, "选择 EIS 方法")
    selected_result = _run_visual_action_once(context, "Techniques_SelectEIS_Selected_Check")
    context.logger.info(
        "Technique initial EIS selected check: matched=%s score=%.6f",
        selected_result.matched,
        selected_result.score,
    )
    if selected_result.matched:
        context.logger.info("EIS item already selected: no click needed")
        context.logger.info(
            "Visual action skipped click: name=%s reason=already_selected",
            "Techniques_SelectEIS_Unselected_Click",
        )
        _append_visual_action_event(
            context,
            "visual_action_skip_click",
            get_visual_action_spec("Techniques_SelectEIS_Unselected_Click"),
            selected_result,
            {"reason": "already_selected"},
        )
    else:
        unselected_result = _run_visual_action_once(context, "Techniques_SelectEIS_Unselected_Check")
        context.logger.info(
            "Technique initial EIS unselected check: matched=%s score=%.6f",
            unselected_result.matched,
            unselected_result.score,
        )
        if not unselected_result.matched:
            raise Chi660eAutoError(
                "Technique EIS state is ambiguous: neither selected nor unselected matched."
            )

        click_result = _run_visual_action_once(context, "Techniques_SelectEIS_Unselected_Click")
        if not click_result.success:
            raise Chi660eAutoError("Technique item click failed for unselected EIS item.")
        context.logger.info("EIS item selected via unselected click")

        if not _confirm_technique_eis_selected_after_click(context):
            context.logger.error("Technique selection failed after state recheck attempts")
            raise Chi660eAutoError("Technique selection failed after state recheck attempts.")

    for attempt in range(1, 3):
        ok_result = _run_visual_action_once(context, "Techniques_ClickOK")
        if ok_result.success:
            context.logger.info("Visual action succeeded: name=%s", "Techniques_ClickOK")
            _append_visual_action_event(
                context,
                "visual_action_succeeded",
                get_visual_action_spec("Techniques_ClickOK"),
                ok_result,
            )
            return
        if attempt < 2:
            context.logger.warning("Technique OK click retry: attempt=%s", attempt + 1)

    raise Chi660eAutoError("Technique OK click failed.")


def _confirm_technique_gcd_selected_after_click(context: RuntimeContext) -> bool:
    context.logger.info("Technique state settle wait: seconds=%.1f", TECHNIQUE_STATE_SETTLE_SEC)
    _sleep_with_stop(context, TECHNIQUE_STATE_SETTLE_SEC, "technique_gcd_state_settle")

    for attempt in range(1, TECHNIQUE_STATE_RECHECK_ATTEMPTS + 1):
        selected_result = _run_visual_action_once(context, "Techniques_SelectGCD_Selected_Check")
        context.logger.info(
            "Technique GCD selected recheck: attempt=%s matched=%s score=%.6f",
            attempt,
            selected_result.matched,
            selected_result.score,
        )
        if selected_result.matched:
            context.logger.info("Technique selection confirmed by selected-check")
            return True

        unselected_result = _run_visual_action_once(context, "Techniques_SelectGCD_Unselected_Check")
        context.logger.info(
            "Technique GCD unselected recheck: attempt=%s matched=%s score=%.6f",
            attempt,
            unselected_result.matched,
            unselected_result.score,
        )
        if not unselected_result.matched:
            context.logger.info("Technique selection confirmed by unselected disappearance")
            return True

        if attempt < TECHNIQUE_STATE_RECHECK_ATTEMPTS:
            context.logger.info("Technique state settle wait: seconds=%.1f", TECHNIQUE_STATE_SETTLE_SEC)
            _sleep_with_stop(context, TECHNIQUE_STATE_SETTLE_SEC, "technique_gcd_state_recheck")

    return False


def _run_techniques_select_gcd_and_confirm(context: RuntimeContext) -> None:
    _emit_runtime_message(context, "选择 GCD 方法")
    selected_result = _run_visual_action_once(context, "Techniques_SelectGCD_Selected_Check")
    context.logger.info(
        "Technique initial GCD selected check: matched=%s score=%.6f",
        selected_result.matched,
        selected_result.score,
    )
    if selected_result.matched:
        context.logger.info("GCD item already selected: no click needed")
        context.logger.info(
            "Visual action skipped click: name=%s reason=already_selected",
            "Techniques_SelectGCD_Unselected_Click",
        )
        _append_visual_action_event(
            context,
            "visual_action_skip_click",
            get_visual_action_spec("Techniques_SelectGCD_Unselected_Click"),
            selected_result,
            {"reason": "already_selected"},
        )
    else:
        unselected_result = _run_visual_action_once(context, "Techniques_SelectGCD_Unselected_Check")
        context.logger.info(
            "Technique initial GCD unselected check: matched=%s score=%.6f",
            unselected_result.matched,
            unselected_result.score,
        )
        if not unselected_result.matched:
            raise Chi660eAutoError(
                "Technique GCD state is ambiguous: neither selected nor unselected matched."
            )

        click_result = _run_visual_action_once(context, "Techniques_SelectGCD_Unselected_Click")
        if not click_result.success:
            raise Chi660eAutoError("Technique item click failed for unselected GCD item.")
        context.logger.info("GCD item selected via unselected click")

        if not _confirm_technique_gcd_selected_after_click(context):
            context.logger.error("Technique selection failed after state recheck attempts")
            raise Chi660eAutoError("Technique selection failed after state recheck attempts.")

    for attempt in range(1, 3):
        ok_result = _run_visual_action_once(context, "Techniques_ClickOK")
        if ok_result.success:
            context.logger.info("Visual action succeeded: name=%s", "Techniques_ClickOK")
            _append_visual_action_event(
                context,
                "visual_action_succeeded",
                get_visual_action_spec("Techniques_ClickOK"),
                ok_result,
            )
            return
        if attempt < 2:
            context.logger.warning("Technique OK click retry: attempt=%s", attempt + 1)

    raise Chi660eAutoError("Technique OK click failed.")


def _bind_next_window_or_open_from_main(
    context: RuntimeContext,
    next_window_keyword: str | list[str],
    direct_replay_name: str,
    main_rebind_replay_name: str,
    main_click_spec_name: str,
    main_click_replay_name: str,
    pipeline_fallback_entry: str | None = None,
    direct_wait_timeout: float = 2.0,
    direct_wait_interval: float = 0.2,
) -> RuntimeContext:
    next_window_label = _canonical_window_keyword(next_window_keyword)
    context.logger.info(
        "Checking direct next window after technique confirm: keyword=%s",
        next_window_label,
    )

    fallback_reason = "direct_not_found"
    try:
        _wait_for_window(
            next_window_keyword,
            timeout_sec=direct_wait_timeout,
            interval_sec=direct_wait_interval,
            context=context,
        )
        context.logger.info(
            "Direct next window detected after technique confirm: keyword=%s",
            next_window_label,
        )
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "next_window_direct_detected",
                {
                    "keyword": next_window_label,
                    "timeout_sec": direct_wait_timeout,
                    "interval_sec": direct_wait_interval,
                },
            )

        try:
            bound_context = _bind_context_to_window(
                context,
                next_window_keyword,
                direct_replay_name,
                timeout_sec=direct_wait_timeout,
                interval_sec=direct_wait_interval,
            )
            context.logger.info(
                "Direct next window bind succeeded: keyword=%s",
                next_window_label,
            )
            if context.replay_record is not None:
                append_event(
                    context.replay_record,
                    "next_window_direct_bound",
                    {
                        "keyword": next_window_label,
                        "source": "direct_popup",
                    },
                )
            _emit_runtime_message(context, f"打开 {next_window_label}")
            return bound_context
        except Exception as exc:
            fallback_reason = "direct_bind_failed"
            context.logger.warning(
                "Direct next window detected but bind failed, fallback to main path: keyword=%s",
                next_window_label,
            )
            if context.replay_record is not None:
                append_event(
                    context.replay_record,
                    "next_window_direct_bind_failed",
                    {
                        "keyword": next_window_label,
                        "error": str(exc),
                    },
                    level="WARNING",
                )
    except WindowNotFoundError as exc:
        context.logger.info(
            "Direct next window not found after technique confirm, fallback to main path: keyword=%s",
            next_window_label,
        )
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "next_window_fallback_main",
                {
                    "keyword": next_window_label,
                    "click_spec": main_click_spec_name,
                    "reason": fallback_reason,
                    "error": str(exc),
                },
                level="INFO",
            )

    context.logger.info(
        "Main path used for next window: click_spec=%s keyword=%s",
        main_click_spec_name,
        next_window_label,
    )
    if context.replay_record is not None and fallback_reason != "direct_not_found":
        append_event(
            context.replay_record,
            "next_window_fallback_main",
            {
                "keyword": next_window_label,
                "click_spec": main_click_spec_name,
                "reason": fallback_reason,
            },
            level="INFO",
        )

    fallback_entry = pipeline_fallback_entry or main_click_spec_name

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
        _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, main_rebind_replay_name)
    else:
        try:
            return _run_visual_action_expect_window_with_fallback(
                context,
                main_click_spec_name,
                main_click_replay_name,
                fallback_entry,
            )
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
            _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, main_rebind_replay_name)

    bound_context = _run_visual_action_expect_window_with_fallback(
        context,
        main_click_spec_name,
        main_click_replay_name,
        fallback_entry,
    )
    _emit_runtime_message(context, f"打开 {next_window_label}")
    return bound_context


def _build_node_override(node_name: str, **fields: Any) -> dict[str, dict[str, Any]]:
    return {
        node_name: fields,
    }


def _build_input_apply_override(apply_entry: str, input_text: str) -> dict[str, dict[str, Any]]:
    # Pipeline Apply nodes are atomic InputText shells only. Business values are
    # injected from config here and are not sourced from pipeline JSON.
    return _build_node_override(apply_entry, input_text=input_text)


def _get_dropdown_option_offset(spec_name: str, option_value: str) -> tuple[int, int]:
    spec = get_visual_action_spec(spec_name)
    offsets = spec.dropdown_option_offsets or {}
    try:
        return offsets[option_value]
    except KeyError as exc:
        raise NotImplementedError(
            f"Dropdown option {option_value!r} is not declared for visual action {spec_name!r}."
        ) from exc


def _double_click_focused_input(
    context: RuntimeContext,
    name: str,
    click_point: tuple[int, int],
) -> dict[str, Any]:
    _enforce_min_action_gap(context, f"double_click:{name}")
    context.logger.info("Input double-click start: name=%s point=%s", name, click_point)
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "input_double_click_start",
            {
                "name": name,
                "click_point": click_point,
            },
        )

    try:
        result = post_double_click(context.controller, click_point[0], click_point[1])
    except Exception as exc:
        context.logger.warning(
            "Input double-click failed: name=%s point=%s error=%s",
            name,
            click_point,
            exc,
        )
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "input_double_click_failed",
                {
                    "name": name,
                    "click_point": click_point,
                    "error": str(exc),
                },
                level="ERROR",
            )
        raise

    context.logger.info("Input double-click succeeded: name=%s point=%s", name, click_point)
    _mark_action_completed(context)
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "input_double_click_succeeded",
            {
                "name": name,
                "click_point": click_point,
                "interval_sec": result.get("interval_sec"),
                "success": result.get("success"),
            },
        )
    return result


def _delete_immediately_after_double_click(context: RuntimeContext, name: str) -> dict[str, Any]:
    keycode = 46
    _enforce_min_action_gap(context, f"delete:{name}")
    context.logger.info("Input immediate delete start: name=%s keycode=%s", name, keycode)
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "input_delete_immediate_start",
            {
                "name": name,
                "keycode": keycode,
            },
        )

    try:
        result = post_key_click(context.controller, keycode)
    except Exception as exc:
        context.logger.warning(
            "Input immediate delete failed: name=%s keycode=%s error=%s",
            name,
            keycode,
            exc,
        )
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "input_delete_immediate_failed",
                {
                    "name": name,
                    "keycode": keycode,
                    "error": str(exc),
                },
                level="ERROR",
            )
        raise

    context.logger.info("Input immediate delete succeeded: name=%s keycode=%s", name, keycode)
    _mark_action_completed(context)
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "input_delete_immediate_succeeded",
            {
                "name": name,
                "keycode": result.get("keycode"),
                "success": result.get("success"),
            },
        )
    return result


def _run_text_input_field(
    context: RuntimeContext,
    focus_name: str,
    apply_entry: str,
    input_text: str,
    cleanup_passes: int = 1,
) -> None:
    focus_result = _locate_visual_action_point(context, focus_name)
    if focus_result.click_point is None:
        raise Chi660eAutoError(f"Visual focus click point is missing for {focus_name}.")

    context.logger.info("Input focus located: name=%s click_point=%s", focus_name, focus_result.click_point)
    # 仅在指定字段上增加额外清理轮次，默认仍保持现有单轮清理。
    for _ in range(max(1, cleanup_passes)):
        _double_click_focused_input(context, focus_name, focus_result.click_point)
        _delete_immediately_after_double_click(context, focus_name)
    _post_task(
        context,
        apply_entry,
        _build_input_apply_override(apply_entry, input_text),
    )


def _run_cv_input_field(
    context: RuntimeContext,
    focus_name: str,
    apply_entry: str,
    input_text: str,
) -> None:
    _run_text_input_field(context, focus_name, apply_entry, input_text)


def _select_cv_sensitivity_dropdown_value(
    context: RuntimeContext,
    sensitivity_value: str,
) -> None:
    # Flow decides when to select sensitivity. Geometry and dropdown offsets are
    # declared in visual_action_specs, and the business value comes from config.
    focus_result = _run_visual_action_click(context, "CV_FocusSensitivity")
    if focus_result.click_point is None:
        raise Chi660eAutoError("Sensitivity dropdown click point is missing.")

    dropdown_click_point = focus_result.click_point
    context.logger.info(
        "CV sensitivity dropdown opened: click_point=%s",
        dropdown_click_point,
    )
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "cv_sensitivity_dropdown_opened",
            {
                "click_point": dropdown_click_point,
            },
        )

    option_offset = _get_dropdown_option_offset("CV_FocusSensitivity", sensitivity_value)
    option_click_point = (
        int(dropdown_click_point[0] + option_offset[0]),
        int(dropdown_click_point[1] + option_offset[1]),
    )

    _enforce_min_action_gap(context, f"click:CV_SensitivityOption:{sensitivity_value}")
    click_point(
        context.controller,
        option_click_point[0],
        option_click_point[1],
        option_click_point,
    )
    _mark_action_completed(context)
    context.logger.info(
        "CV sensitivity option click: value=%s click_point=%s",
        sensitivity_value,
        option_click_point,
    )
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "cv_sensitivity_option_click",
            {
                "value": sensitivity_value,
                "click_point": option_click_point,
                "offset": option_offset,
            },
        )


def _select_cv_initial_scan_polarity_dropdown_value(
    context: RuntimeContext,
    polarity_value: str,
) -> None:
    focus_result = _run_visual_action_click(context, "CV_FocusInitialScanPolarity")
    if focus_result.click_point is None:
        raise Chi660eAutoError("Initial Scan Polarity dropdown click point is missing.")

    dropdown_click_point = focus_result.click_point
    context.logger.info(
        "CV initial scan polarity dropdown opened: click_point=%s",
        dropdown_click_point,
    )
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "cv_initial_scan_polarity_dropdown_opened",
            {
                "click_point": dropdown_click_point,
            },
        )

    option_offset = _get_dropdown_option_offset("CV_FocusInitialScanPolarity", polarity_value)
    option_click_point = (
        int(dropdown_click_point[0] + option_offset[0]),
        int(dropdown_click_point[1] + option_offset[1]),
    )

    _enforce_min_action_gap(context, f"click:CV_InitialScanPolarityOption:{polarity_value}")
    click_point(
        context.controller,
        option_click_point[0],
        option_click_point[1],
        option_click_point,
    )
    _mark_action_completed(context)
    context.logger.info(
        "CV initial scan polarity option click: value=%s click_point=%s",
        polarity_value,
        option_click_point,
    )
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "cv_initial_scan_polarity_option_click",
            {
                "value": polarity_value,
                "click_point": option_click_point,
                "offset": option_offset,
            },
        )


def _run_cv_front_half_visual_form_once(
    context: RuntimeContext,
    config: CVFrontHalfConfig,
) -> None:
    _emit_runtime_message(context, f"输入 High E (V) {config.high_potential}")
    _run_cv_input_field(
        context,
        "CV_FocusHighPotential",
        "CV_InputHighPotential_Apply",
        config.high_potential,
    )
    _emit_runtime_message(context, "设置 Initial Scan Polarity Positive")
    _select_cv_initial_scan_polarity_dropdown_value(context, "Positive")
    _emit_runtime_message(context, f"输入 Scan Rate (V/s) {config.scan_rate}")
    _run_cv_input_field(
        context,
        "CV_FocusScanRate",
        "CV_InputScanRate_Apply",
        config.scan_rate,
    )
    _emit_runtime_message(context, f"输入 Sweep Segments {config.sweep_segments}")
    _run_cv_input_field(
        context,
        "CV_FocusSweepSegments",
        "CV_InputSweepSegments_Apply",
        config.sweep_segments,
    )
    _select_cv_sensitivity_dropdown_value(context, config.sensitivity)

    _emit_runtime_message(context, "点击参数窗口 OK")
    ok_result = _run_visual_action_click(context, "CV_ClickOK")
    _append_visual_action_event(
        context,
        "visual_action_succeeded",
        get_visual_action_spec("CV_ClickOK"),
        ok_result,
    )
    _wait_for_window_close(CV_PARAM_WINDOW_KEYWORD, context=context)


def _run_cv_front_half_visual_form(
    context: RuntimeContext,
    config: CVFrontHalfConfig,
) -> None:
    _run_cv_front_half_visual_form_once(context, config)


def _run_eis_front_half_visual_form_once(
    context: RuntimeContext,
    config: EISFrontHalfConfig,
) -> None:
    context.logger.info("EIS init E input start/value=%s", config.init_potential_v)
    _emit_runtime_message(context, f"输入 Init E (V) {config.init_potential_v}")
    _run_text_input_field(
        context,
        "EIS_FocusInitPotential",
        "EIS_InputInitPotential_Apply",
        config.init_potential_v,
    )
    context.logger.info("EIS high frequency input start/value=%s", config.high_frequency_hz)
    _emit_runtime_message(context, f"输入 High Frequency (Hz) {config.high_frequency_hz}")
    _run_text_input_field(
        context,
        "EIS_FocusHighFrequency",
        "EIS_InputHighFrequency_Apply",
        config.high_frequency_hz,
        cleanup_passes=2,
    )
    context.logger.info("EIS low frequency input start/value=%s", config.low_frequency_hz)
    _emit_runtime_message(context, f"输入 Low Frequency (Hz) {config.low_frequency_hz}")
    _run_text_input_field(
        context,
        "EIS_FocusLowFrequency",
        "EIS_InputLowFrequency_Apply",
        config.low_frequency_hz,
    )

    _emit_runtime_message(context, "点击参数窗口 OK")
    ok_result = _run_visual_action_click(context, "EIS_ClickOK")
    _append_visual_action_event(
        context,
        "visual_action_succeeded",
        get_visual_action_spec("EIS_ClickOK"),
        ok_result,
    )
    _wait_for_window_close(EIS_PARAM_WINDOW_KEYWORD, context=context)


def _run_eis_front_half_visual_form(
    context: RuntimeContext,
    config: EISFrontHalfConfig,
) -> None:
    _run_eis_front_half_visual_form_once(context, config)


def _run_gcd_front_half_visual_form_once(
    context: RuntimeContext,
    run_values: dict[str, str],
) -> None:
    context.logger.info(
        "GCD cathodic current input start/value=%s",
        run_values["cathodic_current_a_text"],
    )
    _emit_runtime_message(context, f"输入 Cathodic Current (A) {run_values['cathodic_current_a_text']}")
    _run_text_input_field(
        context,
        "GCD_FocusCathodicCurrent",
        "GCD_InputCathodicCurrent_Apply",
        run_values["cathodic_current_a_text"],
    )
    context.logger.info(
        "GCD anodic current input start/value=%s",
        run_values["anodic_current_a_text"],
    )
    _emit_runtime_message(context, f"输入 Anodic Current (A) {run_values['anodic_current_a_text']}")
    _run_text_input_field(
        context,
        "GCD_FocusAnodicCurrent",
        "GCD_InputAnodicCurrent_Apply",
        run_values["anodic_current_a_text"],
    )
    context.logger.info(
        "GCD high E limit input start/value=%s",
        run_values["high_e_limit_v_text"],
    )
    _emit_runtime_message(context, f"输入 High E limit (V) {run_values['high_e_limit_v_text']}")
    _run_text_input_field(
        context,
        "GCD_FocusHighELimit",
        "GCD_InputHighELimit_Apply",
        run_values["high_e_limit_v_text"],
    )
    context.logger.info(
        "GCD low E limit input start/value=%s",
        run_values["low_e_limit_v_text"],
    )
    _emit_runtime_message(context, f"输入 Low E limit (V) {run_values['low_e_limit_v_text']}")
    _run_text_input_field(
        context,
        "GCD_FocusLowELimit",
        "GCD_InputLowELimit_Apply",
        run_values["low_e_limit_v_text"],
    )
    context.logger.info(
        "GCD data storage interval input start/value=%s",
        run_values["data_storage_interval_text"],
    )
    _emit_runtime_message(context, f"输入 Data Storage Intvl (sec) {run_values['data_storage_interval_text']}")
    _run_text_input_field(
        context,
        "GCD_FocusDataStorageIntvl",
        "GCD_InputDataStorageIntvl_Apply",
        run_values["data_storage_interval_text"],
    )
    context.logger.info(
        "GCD number of segments input start/value=%s",
        run_values["number_of_segments_text"],
    )
    _emit_runtime_message(context, f"输入 Number of Segments {run_values['number_of_segments_text']}")
    _run_text_input_field(
        context,
        "GCD_FocusNumberOfSegments",
        "GCD_InputNumberOfSegments_Apply",
        run_values["number_of_segments_text"],
    )

    _emit_runtime_message(context, "点击参数窗口 OK")
    ok_result = _run_visual_action_click(context, "GCD_ClickOK")
    _append_visual_action_event(
        context,
        "visual_action_succeeded",
        get_visual_action_spec("GCD_ClickOK"),
        ok_result,
    )
    _wait_for_window_close(GCD_PARAM_WINDOW_KEYWORD, context=context)


def _run_gcd_front_half_visual_form(
    context: RuntimeContext,
    run_values: dict[str, str],
) -> None:
    _run_gcd_front_half_visual_form_once(context, run_values)


def bind_runtime_context_to_window(
    context: RuntimeContext,
    keyword: str | list[str],
    capture_name: str,
    timeout_sec: float | None = None,
    interval_sec: float | None = None,
) -> RuntimeContext:
    return _bind_context_to_window(
        context,
        keyword,
        capture_name,
        timeout_sec=timeout_sec,
        interval_sec=interval_sec,
    )


def run_visual_action_once_in_context(
    context: RuntimeContext,
    spec_name: str,
    relocate_cursor_before_task: bool = True,
) -> VisualActionResult:
    return _run_visual_action_once(
        context,
        spec_name,
        relocate_cursor_before_task=relocate_cursor_before_task,
    )


def run_visual_action_click_in_context(context: RuntimeContext, spec_name: str) -> VisualActionResult:
    return _run_visual_action_click(context, spec_name)


def wait_for_window_close_by_keyword(keyword: str, timeout_sec: float = WINDOW_WAIT_TIMEOUT_SEC) -> None:
    _wait_for_window_close(keyword, timeout_sec=timeout_sec)


def run_open_circuit_potential_on_context(context: RuntimeContext) -> dict[str, str]:
    return _read_and_close_open_circuit_potential(context)


def run_open_circuit_potential() -> dict[str, str]:
    context = bootstrap_app()

    try:
        result = run_open_circuit_potential_on_context(context)
        print(f"OCP raw: {result['raw_text']}")
        print(f"OCP normalized for Init E: {result['normalized_value']}")
        if context.replay_record is not None:
            finalize_session(context.replay_record, status="completed")
        return result
    except Exception as exc:
        context.logger.exception("Open circuit potential flow failed.")
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "error",
                {"message": str(exc), "stage": "open_circuit_potential"},
                level="ERROR",
            )
            finalize_session(context.replay_record, status="error", error=str(exc))
        raise


def run_cv_front_half_on_context(
    context: RuntimeContext,
    config: CVFrontHalfConfig,
) -> RuntimeContext:
    _raise_if_stop_requested(context, "cv_front_half_start")
    # 前半圈主路径仍由流程层负责：窗口切换、步骤顺序、fallback 决策都只在这里。
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "cv_front_half_start",
            {
                "high_potential": config.high_potential,
                "scan_rate": config.scan_rate,
                "sweep_segments": config.sweep_segments,
                "sensitivity": config.sensitivity,
            },
        )

    _raise_if_stop_requested(context, "cv_front_half_before_technique")
    _emit_runtime_message(context, "点击 Techniques 按钮")
    _run_visual_action_expect_window_with_fallback(
        context,
        "Main_ClickTechnique",
        "cv_front_half_techniques_window",
        "Main_ClickTechnique",
    )
    _run_techniques_select_cv_and_confirm(context)
    _raise_if_stop_requested(context, "cv_front_half_before_parameters")
    _bind_next_window_or_open_from_main(
        context,
        next_window_keyword=CV_PARAM_WINDOW_KEYWORD,
        direct_replay_name="cv_front_half_cv_window_direct",
        main_rebind_replay_name="cv_front_half_main_rebound",
        main_click_spec_name="Main_ClickParameters",
        main_click_replay_name="cv_front_half_cv_window_initial",
        direct_wait_timeout=2.0,
        direct_wait_interval=0.2,
    )

    _run_cv_front_half_visual_form(context, config)
    _raise_if_stop_requested(context, "cv_front_half_after_parameters")
    _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, "cv_front_half_main_final")

    if context.replay_record is not None:
        append_event(context.replay_record, "cv_front_half_ready", {"window": MAIN_WINDOW_TITLE_CANDIDATES})

    context.logger.info("CV front-half flow completed.")
    return context


def run_eis_front_half_on_context(
    context: RuntimeContext,
    config: EISFrontHalfConfig,
) -> RuntimeContext:
    _raise_if_stop_requested(context, "eis_front_half_before_ocp")
    ocp_result = _read_and_close_open_circuit_potential(context)
    config.init_potential_v = ocp_result["normalized_value"]

    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "eis_front_half_start",
            {
                "init_potential_v": config.init_potential_v,
                "high_frequency_hz": config.high_frequency_hz,
                "low_frequency_hz": config.low_frequency_hz,
            },
        )

    _raise_if_stop_requested(context, "eis_front_half_before_technique")
    _emit_runtime_message(context, "点击 Techniques 按钮")
    _run_visual_action_expect_window_with_fallback(
        context,
        "Main_ClickTechnique",
        "eis_front_half_techniques_window",
        "Main_ClickTechnique",
    )
    _run_techniques_select_eis_and_confirm(context)
    _raise_if_stop_requested(context, "eis_front_half_before_parameters")
    _bind_next_window_or_open_from_main(
        context,
        next_window_keyword=EIS_PARAM_WINDOW_KEYWORD,
        direct_replay_name="eis_front_half_param_window_direct",
        main_rebind_replay_name="eis_front_half_main_rebound",
        main_click_spec_name="Main_ClickParametersEIS",
        main_click_replay_name="eis_front_half_param_window_initial",
        pipeline_fallback_entry="Main_ClickParameters",
        direct_wait_timeout=2.0,
        direct_wait_interval=0.2,
    )

    _run_eis_front_half_visual_form(context, config)
    _raise_if_stop_requested(context, "eis_front_half_after_parameters")
    _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, "eis_front_half_main_final")

    if context.replay_record is not None:
        append_event(context.replay_record, "eis_front_half_ready", {"window": MAIN_WINDOW_TITLE_CANDIDATES})

    context.logger.info("EIS front-half flow completed.")
    return context


def run_gcd_front_half_on_context(
    context: RuntimeContext,
    run_values: dict[str, str],
) -> RuntimeContext:
    _raise_if_stop_requested(context, "gcd_front_half_start")
    if context.replay_record is not None:
        append_event(
            context.replay_record,
            "gcd_front_half_start",
            {
                "cathodic_current_a_text": run_values["cathodic_current_a_text"],
                "anodic_current_a_text": run_values["anodic_current_a_text"],
                "high_e_limit_v_text": run_values["high_e_limit_v_text"],
                "data_storage_interval_text": run_values["data_storage_interval_text"],
                "number_of_segments_text": run_values["number_of_segments_text"],
                "density_label_text": run_values["density_label_text"],
            },
        )

    _raise_if_stop_requested(context, "gcd_front_half_before_technique")
    _emit_runtime_message(context, "点击 Techniques 按钮")
    _run_visual_action_expect_window_with_fallback(
        context,
        "Main_ClickTechnique",
        "gcd_front_half_techniques_window",
        "Main_ClickTechnique",
    )
    _run_techniques_select_gcd_and_confirm(context)
    _raise_if_stop_requested(context, "gcd_front_half_before_parameters")
    _bind_next_window_or_open_from_main(
        context,
        next_window_keyword=GCD_PARAM_WINDOW_KEYWORD,
        direct_replay_name="gcd_front_half_param_window_direct",
        main_rebind_replay_name="gcd_front_half_main_rebound",
        main_click_spec_name="Main_ClickParametersGCD",
        main_click_replay_name="gcd_front_half_param_window_initial",
        pipeline_fallback_entry="Main_ClickParameters",
        direct_wait_timeout=2.0,
        direct_wait_interval=0.2,
    )

    _run_gcd_front_half_visual_form(context, run_values)
    _raise_if_stop_requested(context, "gcd_front_half_after_parameters")
    _bind_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, "gcd_front_half_main_final")

    if context.replay_record is not None:
        append_event(context.replay_record, "gcd_front_half_ready", {"window": MAIN_WINDOW_TITLE_CANDIDATES})

    context.logger.info("GCD front-half flow completed.")
    return context


def run_cv_front_half(config: CVFrontHalfConfig | None = None) -> RuntimeContext:
    context = bootstrap_app()
    config = config or get_default_cv_front_half_config()

    try:
        result = run_cv_front_half_on_context(context, config)
        if context.replay_record is not None:
            finalize_session(context.replay_record, status="completed")
        return result
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


def run_eis_front_half(config: EISFrontHalfConfig | None = None) -> RuntimeContext:
    context = bootstrap_app()
    config = config or get_default_eis_front_half_config()

    try:
        result = run_eis_front_half_on_context(context, config)
        if context.replay_record is not None:
            finalize_session(context.replay_record, status="completed")
        return result
    except Exception as exc:
        context.logger.exception("EIS front-half flow failed.")
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "error",
                {"message": str(exc), "stage": "eis_front_half"},
                level="ERROR",
            )
            finalize_session(context.replay_record, status="error", error=str(exc))
        try:
            _save_step_capture(context, "eis_front_half_error")
        except Exception:
            context.logger.exception("Failed to save runner error capture.")
        raise


def run_gcd_front_half(config: GCDFrontHalfConfig | None = None) -> RuntimeContext:
    context = bootstrap_app()
    config = config or get_default_gcd_front_half_config()

    try:
        density = float(config.current_density_ma_cm2_list[0])
        run_values = build_gcd_run_values(config, density)
        result = run_gcd_front_half_on_context(context, run_values)
        if context.replay_record is not None:
            finalize_session(context.replay_record, status="completed")
        return result
    except Exception as exc:
        context.logger.exception("GCD front-half flow failed.")
        if context.replay_record is not None:
            append_event(
                context.replay_record,
                "error",
                {"message": str(exc), "stage": "gcd_front_half"},
                level="ERROR",
            )
            finalize_session(context.replay_record, status="error", error=str(exc))
        try:
            _save_step_capture(context, "gcd_front_half_error")
        except Exception:
            context.logger.exception("Failed to save runner error capture.")
        raise
