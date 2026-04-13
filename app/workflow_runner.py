from __future__ import annotations

"""任务段执行器。

这里消费已排序的任务段计划；业务值来自 config/segment params，前后半流程由
既有流程层模块负责。
"""

import time
from pathlib import Path
from typing import Any

from app.bootstrap import bootstrap_app
from app.errors import Chi660eAutoError
from app.post_run_flow import run_post_run_public_flow
from app.run_control import RunControl, RunStopRequested, sleep_with_run_control
from app.replay_manager import append_event, finalize_session
from app.runtime_context import RuntimeContext
from app.task_runner import (
    run_cv_front_half_on_context,
    run_eis_front_half_on_context,
    run_gcd_front_half_on_context,
)
from app.workflow_segments import (
    WorkflowSegment,
    WorkflowSegmentType,
    build_cv_front_half_config_for_segment,
    build_eis_front_half_config_for_segment,
    build_gcd_run_values_for_segment,
    build_default_runnable_segment_plan,
    build_output_filename_for_segment,
    segment_block_reason,
    segment_is_rest,
    segment_is_runnable,
    sort_enabled_segments,
)


def _append_workflow_event(
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


def _raise_if_stop_requested(context: RuntimeContext, stage: str) -> None:
    run_control = getattr(context, "run_control", None)
    if run_control is None:
        return
    run_control.raise_if_stop_requested(stage)


def _run_rest_segment(context: RuntimeContext, segment: WorkflowSegment, runtime_key: str) -> None:
    duration_sec = int(segment.params["duration_sec"])
    context.logger.info("Rest segment start: name=%s duration=%ss", segment.display_name, duration_sec)
    _append_workflow_event(
        context,
        "workflow_segment_rest_start",
        {"segment_id": segment.segment_id, "display_name": segment.display_name, "duration_sec": duration_sec},
    )
    _emit_gui_event(
        context,
        "rest_start",
        {
            "runtime_key": runtime_key,
            "segment_id": segment.segment_id,
            "display_name": segment.display_name,
            "duration_sec": duration_sec,
        },
    )

    remaining_sec = max(0, duration_sec)
    _emit_gui_event(
        context,
        "rest_tick",
        {
            "runtime_key": runtime_key,
            "segment_id": segment.segment_id,
            "display_name": segment.display_name,
            "remaining_sec": remaining_sec,
        },
    )

    deadline = time.monotonic() + remaining_sec
    last_remaining = remaining_sec
    while remaining_sec > 0:
        _raise_if_stop_requested(context, f"rest:{segment.display_name}")
        sleep_with_run_control(
            context.run_control,
            min(1.0, max(0.0, deadline - time.monotonic())),
            stage=f"rest:{segment.display_name}",
        )
        remaining_sec = max(0, int(round(deadline - time.monotonic())))
        if remaining_sec != last_remaining:
            _emit_gui_event(
                context,
                "rest_tick",
                {
                    "runtime_key": runtime_key,
                    "segment_id": segment.segment_id,
                    "display_name": segment.display_name,
                    "remaining_sec": remaining_sec,
                },
            )
            last_remaining = remaining_sec

    _append_workflow_event(
        context,
        "workflow_segment_rest_completed",
        {"segment_id": segment.segment_id, "display_name": segment.display_name},
    )
    _emit_gui_event(
        context,
        "rest_completed",
        {
            "runtime_key": runtime_key,
            "segment_id": segment.segment_id,
            "display_name": segment.display_name,
        },
    )


def validate_workflow_segments(segments: list[WorkflowSegment]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    enabled_segments = sort_enabled_segments(segments)
    if not enabled_segments:
        return [
            {
                "segment_id": "",
                "segment_type": "workflow",
                "display_name": "workflow",
                "order": 0,
                "reason": "No enabled runnable segments were provided.",
            }
        ]

    for segment in enabled_segments:
        reason = segment_block_reason(segment)
        if reason is None:
            continue
        issues.append(
            {
                "segment_id": segment.segment_id,
                "segment_type": segment.segment_type.value,
                "display_name": segment.display_name,
                "order": segment.order,
                "reason": reason,
            }
        )
    return issues


def _raise_workflow_validation_error(issues: list[dict[str, Any]]) -> None:
    if not issues:
        return
    summary = "; ".join(
        f"{item['display_name']}({item['segment_type']}): {item['reason']}"
        for item in issues
    )
    raise Chi660eAutoError(f"Workflow contains non-runnable segments: {summary}")


def _run_segment(
    context: RuntimeContext,
    segment: WorkflowSegment,
    save_directory: str | Path,
    runtime_key: str,
) -> None:
    _raise_if_stop_requested(context, f"segment_start:{segment.display_name}")
    if not segment_is_runnable(segment):
        raise Chi660eAutoError(
            f"Segment is modeled but not runnable yet: {segment.display_name} ({segment.segment_type.value})"
        )

    if segment.segment_type in {WorkflowSegmentType.ACTIVATION_CV, WorkflowSegmentType.CV_SERIES_ITEM}:
        cv_config = build_cv_front_half_config_for_segment(segment)
        output_name = build_output_filename_for_segment(segment)
        if output_name is None:
            raise RuntimeError(f"Missing output filename for segment {segment.segment_id!r}.")

        run_cv_front_half_on_context(context, cv_config)
        _raise_if_stop_requested(context, f"segment_after_front_half:{segment.display_name}")
        post_run_result = run_post_run_public_flow(
            context,
            save_directory=save_directory,
            file_name=output_name,
        )
        _append_workflow_event(
            context,
            "workflow_segment_saved",
            {
                "segment_id": segment.segment_id,
                "display_name": segment.display_name,
                "file_path": post_run_result["save"]["file_path"],
                "run_poll_count": post_run_result["run_finish"]["poll_count"],
            },
        )
        return

    if segment.segment_type in {
        WorkflowSegmentType.EIS_AFTER_ACTIVATION,
        WorkflowSegmentType.EIS_AFTER_CV,
        WorkflowSegmentType.EIS_AFTER_GCD,
    }:
        eis_config = build_eis_front_half_config_for_segment(segment)
        output_name = build_output_filename_for_segment(segment)
        if output_name is None:
            raise RuntimeError(f"Missing output filename for segment {segment.segment_id!r}.")

        run_eis_front_half_on_context(context, eis_config)
        _raise_if_stop_requested(context, f"segment_after_front_half:{segment.display_name}")
        post_run_result = run_post_run_public_flow(
            context,
            save_directory=save_directory,
            file_name=output_name,
            double_click_main_center_after_run=True,
            double_click_main_center_delay_sec=10.0,
        )
        _append_workflow_event(
            context,
            "workflow_segment_saved",
            {
                "segment_id": segment.segment_id,
                "display_name": segment.display_name,
                "file_path": post_run_result["save"]["file_path"],
                "run_poll_count": post_run_result["run_finish"]["poll_count"],
            },
        )
        return

    if segment.segment_type == WorkflowSegmentType.GCD_SERIES_ITEM:
        gcd_run_values = build_gcd_run_values_for_segment(segment)
        output_name = build_output_filename_for_segment(segment)
        if output_name is None:
            raise RuntimeError(f"Missing output filename for segment {segment.segment_id!r}.")

        context.logger.info(
            "Workflow GCD run values: density=%s cathodic=%s anodic=%s high_e=%s",
            gcd_run_values["density_label_text"],
            gcd_run_values["cathodic_current_a_text"],
            gcd_run_values["anodic_current_a_text"],
            gcd_run_values["high_e_limit_v_text"],
        )

        run_gcd_front_half_on_context(context, gcd_run_values)
        _raise_if_stop_requested(context, f"segment_after_front_half:{segment.display_name}")
        post_run_result = run_post_run_public_flow(
            context,
            save_directory=save_directory,
            file_name=output_name,
        )
        _append_workflow_event(
            context,
            "workflow_segment_saved",
            {
                "segment_id": segment.segment_id,
                "display_name": segment.display_name,
                "density": gcd_run_values["density_label_text"],
                "file_path": post_run_result["save"]["file_path"],
                "run_poll_count": post_run_result["run_finish"]["poll_count"],
            },
        )
        return

    if segment_is_rest(segment):
        _run_rest_segment(context, segment, runtime_key)
        return

    raise NotImplementedError(
        f"Segment type {segment.segment_type.value!r} is declared but its front-half flow is not implemented yet."
    )


def run_workflow_segments(
    segments: list[WorkflowSegment],
    save_directory: str | Path,
    context: RuntimeContext | None = None,
    run_control: RunControl | None = None,
) -> RuntimeContext:
    ordered_segments = sort_enabled_segments(segments)
    issues = validate_workflow_segments(ordered_segments)
    _raise_workflow_validation_error(issues)

    runtime_context = context or bootstrap_app()
    runtime_context.run_control = run_control

    try:
        current_segment: WorkflowSegment | None = None
        current_runtime_key = ""
        for index, segment in enumerate(ordered_segments, start=1):
            current_segment = segment
            current_runtime_key = f"{index}:{segment.segment_id}"
            _raise_if_stop_requested(runtime_context, f"workflow_before_segment:{segment.display_name}")
            output_name = build_output_filename_for_segment(segment)
            runtime_context.logger.info(
                "Workflow segment start: index=%s/%s type=%s name=%s output=%s density=%s",
                index,
                len(ordered_segments),
                segment.segment_type.value,
                segment.display_name,
                output_name,
                segment.params.get("current_density_ma_cm2"),
            )
            _append_workflow_event(
                runtime_context,
                "workflow_segment_start",
                {
                    "index": index,
                    "total": len(ordered_segments),
                    "segment_id": segment.segment_id,
                    "segment_type": segment.segment_type.value,
                    "display_name": segment.display_name,
                    "output_name": output_name,
                    "density": segment.params.get("current_density_ma_cm2"),
                },
            )
            _emit_gui_event(
                runtime_context,
                "segment_start",
                {
                    "runtime_key": current_runtime_key,
                    "index": index,
                    "total": len(ordered_segments),
                    "segment_id": segment.segment_id,
                    "segment_type": segment.segment_type.value,
                    "display_name": segment.display_name,
                },
            )

            _run_segment(runtime_context, segment, save_directory, current_runtime_key)
            _raise_if_stop_requested(runtime_context, f"workflow_after_segment:{segment.display_name}")

            runtime_context.logger.info(
                "Workflow segment completed: index=%s/%s type=%s name=%s",
                index,
                len(ordered_segments),
                segment.segment_type.value,
                segment.display_name,
            )
            _append_workflow_event(
                runtime_context,
                "workflow_segment_completed",
                {
                    "index": index,
                    "total": len(ordered_segments),
                    "segment_id": segment.segment_id,
                    "segment_type": segment.segment_type.value,
                    "display_name": segment.display_name,
                },
            )
            _emit_gui_event(
                runtime_context,
                "segment_completed",
                {
                    "runtime_key": current_runtime_key,
                    "index": index,
                    "total": len(ordered_segments),
                    "segment_id": segment.segment_id,
                    "segment_type": segment.segment_type.value,
                    "display_name": segment.display_name,
                },
            )
            current_segment = None
            current_runtime_key = ""

        if runtime_context.replay_record is not None and context is None:
            finalize_session(runtime_context.replay_record, status="completed")
        return runtime_context
    except RunStopRequested:
        raise
    except Exception as exc:
        runtime_context.logger.exception("Workflow execution failed.")
        _append_workflow_event(
            runtime_context,
            "workflow_segment_failed",
            {
                "segment_id": current_segment.segment_id if current_segment is not None else "",
                "display_name": current_segment.display_name if current_segment is not None else "",
                "error": str(exc),
            },
            level="ERROR",
        )
        _emit_gui_event(
            runtime_context,
            "segment_failed",
            {
                "runtime_key": current_runtime_key,
                "segment_id": current_segment.segment_id if current_segment is not None else "",
                "display_name": current_segment.display_name if current_segment is not None else "",
                "error": str(exc),
            },
        )
        _emit_gui_event(
            runtime_context,
            "runtime_message",
            {"message": f"运行失败：{exc}"},
        )
        if runtime_context.replay_record is not None and context is None:
            finalize_session(runtime_context.replay_record, status="error", error=str(exc))
        raise


def run_default_workflow(save_directory: str | Path) -> RuntimeContext:
    return run_workflow_segments(build_default_runnable_segment_plan(), save_directory=save_directory)
