from __future__ import annotations

import time
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
from app.errors import Chi660eAutoError, WindowNotFoundError
from app.replay_manager import append_event, finalize_session
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

TECHNIQUE_WINDOW_KEYWORD = "Electrochemical Techniques"
CV_PARAM_WINDOW_KEYWORD = "Cyclic Voltammetry Parameters"

WINDOW_WAIT_TIMEOUT_SEC = 6.0
WINDOW_WAIT_INTERVAL_SEC = 0.15
WINDOW_PRESET_VERIFY_RETRIES = 3
MIN_ACTION_GAP_SEC = 1.0
TECHNIQUE_STATE_SETTLE_SEC = 1.0
TECHNIQUE_STATE_RECHECK_ATTEMPTS = 3
CV_SENSITIVITY_OPTION_OFFSET_1E_MINUS3 = (0, 108)


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


def _enforce_min_action_gap(context: RuntimeContext, action_name: str) -> None:
    last_completed = context.last_action_completed_at
    if last_completed is None:
        context.logger.info("Action gap satisfied: action=%s", action_name)
        return

    elapsed = time.monotonic() - last_completed
    remaining = MIN_ACTION_GAP_SEC - elapsed
    if remaining > 0:
        context.logger.info("Action gap enforced: action=%s sleep=%.3fs", action_name, remaining)
        time.sleep(remaining)
        return

    context.logger.info("Action gap satisfied: action=%s", action_name)


def _mark_action_completed(context: RuntimeContext) -> None:
    context.last_action_completed_at = time.monotonic()


def _prepare_visual_task(context: RuntimeContext, entry: str) -> dict[str, Any]:
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


def _run_visual_action_once(context: RuntimeContext, spec_name: str) -> VisualActionResult:
    spec = get_visual_action_spec(spec_name)
    _enforce_selected_state_spec(spec, intended_click=False)
    _ensure_context_window_ready_for_task(context)
    if spec.mode != VisualActionMode.DETECT_ONLY:
        _enforce_min_action_gap(context, f"visual:{spec.name}")
    _prepare_visual_task(context, spec.name)

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
    return _post_task_expect_window_with_recovery(
        context,
        pipeline_fallback_entry,
        spec.expected_window_keyword or replay_name,
        replay_name,
        max_attempts=max(1, spec.max_attempts),
        initial_wait_timeout=spec.timeout_sec,
        retry_wait_timeout=spec.retry_timeout_sec,
    )


def _wait_for_window(
    keyword: str | list[str],
    timeout_sec: float | None = None,
    interval_sec: float | None = None,
):
    timeout = WINDOW_WAIT_TIMEOUT_SEC if timeout_sec is None else timeout_sec
    interval = WINDOW_WAIT_INTERVAL_SEC if interval_sec is None else interval_sec
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        windows = list_desktop_windows()
        try:
            return find_target_window(keyword, windows=windows)
        except WindowNotFoundError as exc:
            last_error = exc
            time.sleep(interval)

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
    timeout_sec: float | None = None,
    interval_sec: float | None = None,
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

    link_result = _wait_for_window(keyword, timeout_sec=timeout_sec, interval_sec=interval_sec)
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


def _confirm_technique_selected_after_click(context: RuntimeContext) -> bool:
    context.logger.info("Technique state settle wait: seconds=%.1f", TECHNIQUE_STATE_SETTLE_SEC)
    time.sleep(TECHNIQUE_STATE_SETTLE_SEC)

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
            time.sleep(TECHNIQUE_STATE_SETTLE_SEC)

    return False


def _run_techniques_select_cv_and_confirm(context: RuntimeContext) -> None:
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


def _bind_next_window_or_open_from_main(
    context: RuntimeContext,
    next_window_keyword: str | list[str],
    direct_replay_name: str,
    main_rebind_replay_name: str,
    main_click_spec_name: str,
    main_click_replay_name: str,
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
                main_click_spec_name,
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

    return _run_visual_action_expect_window_with_fallback(
        context,
        main_click_spec_name,
        main_click_replay_name,
        main_click_spec_name,
    )


def _build_node_override(node_name: str, **fields: Any) -> dict[str, dict[str, Any]]:
    return {
        node_name: fields,
    }


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


def _run_cv_input_field(
    context: RuntimeContext,
    focus_name: str,
    apply_entry: str,
    input_text: str,
) -> None:
    focus_result = _locate_visual_action_point(context, focus_name)
    if focus_result.click_point is None:
        raise Chi660eAutoError(f"Visual focus click point is missing for {focus_name}.")

    context.logger.info("Input focus located: name=%s click_point=%s", focus_name, focus_result.click_point)
    _double_click_focused_input(context, focus_name, focus_result.click_point)
    _delete_immediately_after_double_click(context, focus_name)
    _post_task(
        context,
        apply_entry,
        _build_node_override(apply_entry, input_text=input_text, next=[]),
    )


def _select_cv_sensitivity_dropdown_value(
    context: RuntimeContext,
    sensitivity_value: str,
) -> None:
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

    if sensitivity_value != "1.e-003":
        raise NotImplementedError(
            "Only sensitivity=1.e-003 is supported in current test step."
        )

    option_click_point = (
        int(dropdown_click_point[0] + CV_SENSITIVITY_OPTION_OFFSET_1E_MINUS3[0]),
        int(dropdown_click_point[1] + CV_SENSITIVITY_OPTION_OFFSET_1E_MINUS3[1]),
    )

    _enforce_min_action_gap(context, "click:CV_SensitivityOption_1.e-003")
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
                "offset": CV_SENSITIVITY_OPTION_OFFSET_1E_MINUS3,
            },
        )


def _run_cv_front_half_visual_form_once(
    context: RuntimeContext,
    config: CVFrontHalfConfig,
) -> None:
    _run_cv_input_field(
        context,
        "CV_FocusHighPotential",
        "CV_InputHighPotential_Apply",
        config.high_potential,
    )
    _run_cv_input_field(
        context,
        "CV_FocusScanRate",
        "CV_InputScanRate_Apply",
        config.scan_rate,
    )
    _run_cv_input_field(
        context,
        "CV_FocusSweepSegments",
        "CV_InputSweepSegments_Apply",
        config.sweep_segments,
    )
    _select_cv_sensitivity_dropdown_value(context, config.sensitivity)

    ok_result = _run_visual_action_click(context, "CV_ClickOK")
    _append_visual_action_event(
        context,
        "visual_action_succeeded",
        get_visual_action_spec("CV_ClickOK"),
        ok_result,
    )
    _wait_for_window_close(CV_PARAM_WINDOW_KEYWORD)


def _run_cv_front_half_visual_form(
    context: RuntimeContext,
    config: CVFrontHalfConfig,
) -> None:
    _run_cv_front_half_visual_form_once(context, config)


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

        _run_visual_action_expect_window_with_fallback(
            context,
            "Main_ClickTechnique",
            "cv_front_half_techniques_window",
            "Main_ClickTechnique",
        )
        _run_techniques_select_cv_and_confirm(context)
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
