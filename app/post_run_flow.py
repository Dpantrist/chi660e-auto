from __future__ import annotations

"""参数窗口 OK 之后的公共后半圈流程。"""

import time
from pathlib import Path
from typing import Any

from app.constants import MAIN_WINDOW_TITLE_CANDIDATES
from app.controller_manager import capture_once, post_double_click
from app.errors import Chi660eAutoError
from app.replay_manager import append_event
from app.run_control import sleep_with_run_control
from app.runtime_context import RuntimeContext
from app.save_dialog import save_as_txt
from app.task_runner import (
    bind_runtime_context_to_window,
    run_visual_action_click_in_context,
    run_visual_action_once_in_context,
)

RUN_START_CONFIRM_TIMEOUT_SEC = 5.0
RUN_START_CONFIRM_INTERVAL_SEC = 0.5
RUN_FINISH_POLL_SEC = 5.0
RUN_FINISH_CONFIRM_SEC = 1.5


def _append_post_run_event(
    context: RuntimeContext,
    event_name: str,
    detail: dict[str, Any],
    level: str = "INFO",
) -> None:
    if context.replay_record is None:
        return
    append_event(context.replay_record, event_name, detail, level=level)


def _emit_gui_event(context: RuntimeContext, event_type: str, payload: dict[str, Any]) -> None:
    sink = getattr(context, "gui_event_sink", None)
    if sink is None:
        return
    sink(event_type, payload)


def _emit_runtime_message(context: RuntimeContext, message: str) -> None:
    _emit_gui_event(context, "runtime_message", {"message": message})


def _bind_main_window(context: RuntimeContext, capture_name: str) -> RuntimeContext:
    return bind_runtime_context_to_window(context, MAIN_WINDOW_TITLE_CANDIDATES, capture_name)


def _raise_if_stop_requested(context: RuntimeContext, stage: str) -> None:
    if context.run_control is None:
        return
    context.run_control.raise_if_stop_requested(stage)


def _sleep_with_stop(context: RuntimeContext, total_sec: float, stage: str) -> None:
    sleep_with_run_control(context.run_control, total_sec, stage=stage)


def _double_click_main_window_center(context: RuntimeContext) -> dict[str, Any]:
    if context.controller is None:
        raise Chi660eAutoError("Controller is not initialized for center double-click.")

    image = capture_once(context.controller)
    if image is None or getattr(image, "shape", None) is None:
        raise Chi660eAutoError("Main window capture is unavailable for center double-click.")

    height, width = image.shape[:2]
    point = (int(width // 2), int(height // 2))
    context.logger.info("Post-run center double-click start: point=%s", point)
    result = post_double_click(context.controller, point[0], point[1])
    context.logger.info("Post-run center double-click succeeded: point=%s", point)
    _append_post_run_event(
        context,
        "post_run_center_double_click_succeeded",
        {
            "point": point,
            "interval_sec": result.get("interval_sec"),
        },
    )
    return {
        "success": True,
        "point": point,
        "capture_shape": (height, width),
        "interval_sec": result.get("interval_sec"),
    }


def ensure_main_idle_before_run(context: RuntimeContext) -> dict[str, Any]:
    _emit_runtime_message(context, "确认主界面空闲")
    try:
        _raise_if_stop_requested(context, "post_run_idle_before_bind")
        _bind_main_window(context, "post_run_main_idle")

        disabled_first = run_visual_action_once_in_context(context, "Main_CheckPauseDisabled")
        usable_first = run_visual_action_once_in_context(context, "Main_CheckPauseUsable")
        context.logger.info(
            "Main idle precheck: disabled=%s score=%.6f usable=%s score=%.6f",
            disabled_first.matched,
            disabled_first.score,
            usable_first.matched,
            usable_first.score,
        )

        if usable_first.matched:
            raise Chi660eAutoError("当前已有正在运行的任务，请取消后重试。")
        if not disabled_first.matched:
            raise Chi660eAutoError("主窗口状态不明确，无法确认是否空闲。")

        _sleep_with_stop(context, RUN_START_CONFIRM_INTERVAL_SEC, "post_run_idle_stable_confirm")
        disabled_second = run_visual_action_once_in_context(context, "Main_CheckPauseDisabled")
        usable_second = run_visual_action_once_in_context(context, "Main_CheckPauseUsable")
        context.logger.info(
            "Main idle stable confirm: disabled=%s score=%.6f usable=%s score=%.6f",
            disabled_second.matched,
            disabled_second.score,
            usable_second.matched,
            usable_second.score,
        )

        if disabled_second.matched and not usable_second.matched:
            result = {
                "idle": True,
                "disabled_score": disabled_second.score,
                "usable_score": usable_second.score,
            }
            _append_post_run_event(context, "main_idle_confirmed", result)
            return result

        raise Chi660eAutoError("主窗口空闲状态稳定确认失败。")
    except Exception as exc:
        if context.run_control is not None and context.run_control.is_stop_requested():
            raise
        _emit_runtime_message(context, f"主界面空闲检查失败：{exc}")
        raise


def start_run_and_confirm(context: RuntimeContext) -> dict[str, Any]:
    _emit_runtime_message(context, "点击 Run 按钮")
    try:
        _raise_if_stop_requested(context, "post_run_before_run")
        _bind_main_window(context, "post_run_main_before_run")
        run_result = run_visual_action_click_in_context(context, "Main_ClickRun")
        _append_post_run_event(
            context,
            "run_clicked",
            {
                "click_point": run_result.click_point,
                "score": run_result.score,
            },
        )

        deadline = time.monotonic() + RUN_START_CONFIRM_TIMEOUT_SEC
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            pause_usable = run_visual_action_once_in_context(context, "Main_CheckPauseUsable")
            context.logger.info(
                "Run start confirm poll: attempt=%s matched=%s score=%.6f",
                attempt,
                pause_usable.matched,
                pause_usable.score,
            )
            if pause_usable.matched:
                result = {
                    "started": True,
                    "attempt": attempt,
                    "score": pause_usable.score,
                }
                _emit_runtime_message(context, "测试任务开始")
                _append_post_run_event(context, "run_started_confirmed", result)
                return result
            _sleep_with_stop(context, RUN_START_CONFIRM_INTERVAL_SEC, "post_run_run_start_confirm")

        _emit_runtime_message(context, "测试启动确认失败：5 秒内未确认任务启动")
        raise Chi660eAutoError("点击 Run 后 5 秒内未确认任务已启动。")
    except Exception:
        if context.run_control is not None and context.run_control.is_stop_requested():
            raise
        raise


def wait_until_run_finished(
    context: RuntimeContext,
    max_wait_sec: float | None = None,
) -> dict[str, Any]:
    start_monotonic = time.monotonic()
    poll_count = 0

    while True:
        _raise_if_stop_requested(context, "post_run_run_finish_poll")
        if max_wait_sec is not None and time.monotonic() - start_monotonic > max_wait_sec:
            _emit_runtime_message(context, "运行等待超时")
            raise Chi660eAutoError("运行等待超时。")

        _sleep_with_stop(context, RUN_FINISH_POLL_SEC, "post_run_run_finish_poll_sleep")
        poll_count += 1

        try:
            pause_usable = run_visual_action_once_in_context(
                context,
                "Main_CheckPauseUsable",
                relocate_cursor_before_task=False,
            )
            context.logger.info(
                "Run finish poll: count=%s running=%s score=%.6f",
                poll_count,
                pause_usable.matched,
                pause_usable.score,
            )
            if pause_usable.matched:
                _append_post_run_event(
                    context,
                    "run_poll_running",
                    {"poll_count": poll_count, "score": pause_usable.score},
                )
                continue

            pause_disabled = run_visual_action_once_in_context(
                context,
                "Main_CheckPauseDisabled",
                relocate_cursor_before_task=False,
            )
            context.logger.info(
                "Run finish disabled check: count=%s matched=%s score=%.6f",
                poll_count,
                pause_disabled.matched,
                pause_disabled.score,
            )
            if not pause_disabled.matched:
                _append_post_run_event(
                    context,
                    "run_poll_ambiguous",
                    {"poll_count": poll_count},
                    level="WARNING",
                )
                continue

            _sleep_with_stop(context, RUN_FINISH_CONFIRM_SEC, "post_run_run_finish_confirm")
            pause_disabled_confirm = run_visual_action_once_in_context(
                context,
                "Main_CheckPauseDisabled",
                relocate_cursor_before_task=False,
            )
            if pause_disabled_confirm.matched:
                result = {
                    "finished": True,
                    "poll_count": poll_count,
                    "score": pause_disabled_confirm.score,
                }
                context.logger.info(
                    "Run finish confirmed: count=%s score=%.6f",
                    poll_count,
                    pause_disabled_confirm.score,
                )
                _emit_runtime_message(context, "检测到运行结束")
                _append_post_run_event(context, "run_finished_confirmed", result)
                return result
        except Exception as exc:
            if context.run_control is not None and context.run_control.is_stop_requested():
                raise
            context.logger.warning("Run finish poll rebind: error=%s", exc)
            _append_post_run_event(
                context,
                "run_poll_rebind",
                {"poll_count": poll_count, "error": str(exc)},
                level="WARNING",
            )
            _bind_main_window(context, "post_run_main_rebind")


def save_result_via_save_as(
    context: RuntimeContext,
    save_directory: str | Path,
    file_name: str,
) -> dict[str, Any]:
    _emit_runtime_message(context, "点击 Save As 按钮")
    _raise_if_stop_requested(context, "post_run_before_save")
    context.logger.info(
        "Save target requested: directory=%s filename=%s",
        save_directory,
        file_name,
    )
    _bind_main_window(context, "post_run_main_before_save")
    save_click = run_visual_action_click_in_context(context, "Main_ClickSaveAs")
    context.logger.info(
        "Save As triggered from main window: click_point=%s",
        save_click.click_point,
    )
    _append_post_run_event(
        context,
        "save_as_triggered",
        {
            "click_point": save_click.click_point,
            "score": save_click.score,
        },
    )

    try:
        save_result = save_as_txt(save_directory, file_name, logger=context.logger)
    except Exception as exc:
        _emit_runtime_message(context, f"保存失败：{exc}")
        raise
    file_path = Path(save_result["file_path"])
    context.logger.info(
        "Save As dialog bound: title=%s class=%s hwnd=%s",
        save_result["dialog"].title,
        save_result["dialog"].class_name,
        save_result["dialog"].hwnd,
    )
    context.logger.info(
        "Save As completed: directory=%s filename=%s type_method=%s",
        file_path.parent,
        file_path.name,
        save_result["type_result"]["method"],
    )
    _append_post_run_event(
        context,
        "save_as_completed",
        {
            "file_path": file_path,
            "directory_method": save_result["directory_result"]["method"],
            "type_method": save_result["type_result"]["method"],
            "filename_method": save_result["filename_result"]["method"],
            "confirm_method": save_result["confirm_result"]["method"],
        },
    )
    _emit_runtime_message(context, f"保存结果 {file_path.name}")

    _bind_main_window(context, "post_run_main_after_save")
    return {
        "success": True,
        "file_path": file_path,
        "dialog": save_result["dialog"],
        "directory_result": save_result["directory_result"],
        "type_result": save_result["type_result"],
        "filename_result": save_result["filename_result"],
        "confirm_result": save_result["confirm_result"],
    }


def run_post_run_public_flow(
    context: RuntimeContext,
    save_directory: str | Path,
    file_name: str,
    max_wait_sec: float | None = None,
    double_click_main_center_after_run: bool = False,
    double_click_main_center_delay_sec: float = 0.0,
) -> dict[str, Any]:
    idle_before = ensure_main_idle_before_run(context)
    run_start = start_run_and_confirm(context)
    center_double_click_result: dict[str, Any] | None = None
    if double_click_main_center_after_run:
        context.logger.info(
            "Post-run center double-click scheduled: delay_sec=%.1f",
            double_click_main_center_delay_sec,
        )
        _append_post_run_event(
            context,
            "post_run_center_double_click_scheduled",
            {"delay_sec": float(double_click_main_center_delay_sec)},
        )
        _sleep_with_stop(
            context,
            max(0.0, float(double_click_main_center_delay_sec)),
            "post_run_center_double_click_delay",
        )
        try:
            center_double_click_result = _double_click_main_window_center(context)
        except Exception as exc:
            context.logger.error("Post-run center double-click failed: %s", exc)
            _append_post_run_event(
                context,
                "post_run_center_double_click_failed",
                {"error": str(exc)},
                level="ERROR",
            )
            raise
    run_finish = wait_until_run_finished(context, max_wait_sec=max_wait_sec)
    save_result = save_result_via_save_as(context, save_directory, file_name)
    idle_after = ensure_main_idle_before_run(context)
    return {
        "success": True,
        "idle_before": idle_before,
        "run_start": run_start,
        "center_double_click": center_double_click_result,
        "run_finish": run_finish,
        "save": save_result,
        "idle_after": idle_after,
    }
