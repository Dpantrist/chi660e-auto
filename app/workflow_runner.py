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
from app.replay_manager import append_event, finalize_session
from app.runtime_context import RuntimeContext
from app.task_runner import run_cv_front_half_on_context, run_eis_front_half_on_context
from app.workflow_segments import (
    WorkflowSegment,
    WorkflowSegmentType,
    build_cv_front_half_config_for_segment,
    build_eis_front_half_config_for_segment,
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


def _run_rest_segment(context: RuntimeContext, segment: WorkflowSegment) -> None:
    duration_sec = int(segment.params["duration_sec"])
    context.logger.info("Rest segment start: name=%s duration=%ss", segment.display_name, duration_sec)
    _append_workflow_event(
        context,
        "workflow_segment_rest_start",
        {"segment_id": segment.segment_id, "display_name": segment.display_name, "duration_sec": duration_sec},
    )
    time.sleep(max(0, duration_sec))
    _append_workflow_event(
        context,
        "workflow_segment_rest_completed",
        {"segment_id": segment.segment_id, "display_name": segment.display_name},
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


def _run_segment(context: RuntimeContext, segment: WorkflowSegment, save_directory: str | Path) -> None:
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

    if segment.segment_type == WorkflowSegmentType.EIS_AFTER_ACTIVATION:
        eis_config = build_eis_front_half_config_for_segment(segment)
        output_name = build_output_filename_for_segment(segment)
        if output_name is None:
            raise RuntimeError(f"Missing output filename for segment {segment.segment_id!r}.")

        run_eis_front_half_on_context(context, eis_config)
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

    if segment_is_rest(segment):
        _run_rest_segment(context, segment)
        return

    raise NotImplementedError(
        f"Segment type {segment.segment_type.value!r} is declared but its front-half flow is not implemented yet."
    )


def run_workflow_segments(
    segments: list[WorkflowSegment],
    save_directory: str | Path,
    context: RuntimeContext | None = None,
) -> RuntimeContext:
    ordered_segments = sort_enabled_segments(segments)
    issues = validate_workflow_segments(ordered_segments)
    _raise_workflow_validation_error(issues)

    runtime_context = context or bootstrap_app()

    try:
        for index, segment in enumerate(ordered_segments, start=1):
            output_name = build_output_filename_for_segment(segment)
            runtime_context.logger.info(
                "Workflow segment start: index=%s/%s type=%s name=%s output=%s",
                index,
                len(ordered_segments),
                segment.segment_type.value,
                segment.display_name,
                output_name,
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
                },
            )

            _run_segment(runtime_context, segment, save_directory)

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

        if runtime_context.replay_record is not None and context is None:
            finalize_session(runtime_context.replay_record, status="completed")
        return runtime_context
    except Exception as exc:
        runtime_context.logger.exception("Workflow execution failed.")
        _append_workflow_event(
            runtime_context,
            "workflow_segment_failed",
            {"error": str(exc)},
            level="ERROR",
        )
        if runtime_context.replay_record is not None and context is None:
            finalize_session(runtime_context.replay_record, status="error", error=str(exc))
        raise


def run_default_workflow(save_directory: str | Path) -> RuntimeContext:
    return run_workflow_segments(build_default_runnable_segment_plan(), save_directory=save_directory)
