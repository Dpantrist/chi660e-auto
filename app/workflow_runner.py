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
from app.naming_rules import build_cv_filename
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


def _build_repeat_output_name(file_name: str, repeat_index: int) -> str:
    path = Path(file_name)
    suffix = path.suffix or ".txt"
    stem = path.stem if path.suffix else file_name
    return f"{stem}-{repeat_index}{suffix}"


def _compute_cv_cycles_per_run(sweep_segments: Any) -> int:
    try:
        segment_count = int(str(sweep_segments).strip())
    except (TypeError, ValueError) as exc:
        raise Chi660eAutoError(f"CV Sweep Segments must be a positive integer: {sweep_segments}") from exc
    if segment_count <= 0:
        raise Chi660eAutoError(f"CV Sweep Segments must be greater than 0: {segment_count}")

    cycles = (segment_count - 1) // 2 if segment_count % 2 else segment_count // 2
    if cycles < 1:
        raise Chi660eAutoError(f"CV Sweep Segments must represent at least 1 cycle: {segment_count}")
    return cycles


def _compute_gcd_cycles_per_run(number_of_segments: Any) -> int:
    try:
        segment_count = int(str(number_of_segments).strip())
    except (TypeError, ValueError) as exc:
        raise Chi660eAutoError(f"GCD Number of Segments must be a positive integer: {number_of_segments}") from exc
    if segment_count <= 0:
        raise Chi660eAutoError(f"GCD Number of Segments must be greater than 0: {segment_count}")

    cycles = (segment_count - 1) // 2 if segment_count % 2 else segment_count // 2
    if cycles < 1:
        raise Chi660eAutoError(f"GCD Number of Segments must represent at least 1 cycle: {segment_count}")
    return cycles


def _build_eis_after_gcd_cycles_filename(
    density_label: Any,
    cycles: int,
    final: bool = False,
) -> str:
    density_part = str(density_label).strip().replace("/", "_").replace("\\", "_").replace(":", "_")
    if density_part:
        density_part = f" {density_part}"
    final_part = " final" if final else ""
    return f"EIS-after GCD{density_part}{final_part} {int(cycles)} cycles.txt"


def _build_eis_after_cv_cycles_filename(
    scan_rate_mv: Any,
    cycles: int,
    final: bool = False,
) -> str:
    scan_rate_part = Path(build_cv_filename(scan_rate_mv)).stem
    if scan_rate_part.lower().startswith("cv "):
        scan_rate_part = scan_rate_part[3:]
    scan_rate_part = scan_rate_part.strip().replace("/", "_").replace("\\", "_").replace(":", "_")
    if scan_rate_part:
        scan_rate_part = f" {scan_rate_part}"
    final_part = " final" if final else ""
    return f"EIS-after CV{scan_rate_part}{final_part} {int(cycles)} cycles.txt"


def _run_eis_after_cv_cycles(
    context: RuntimeContext,
    segment: WorkflowSegment,
    save_directory: str | Path,
    *,
    scan_rate_mv: Any,
    trigger_cycles: int,
    current_cycles: int,
    interval_cycles: int,
    final: bool,
) -> None:
    eis_config = build_eis_front_half_config_for_segment(segment)
    output_name = _build_eis_after_cv_cycles_filename(scan_rate_mv, current_cycles, final=final)
    context.logger.info(
        "Workflow EIS-after-CV trigger: scan_rate_mv=%s trigger_cycles=%s current_cycles=%s interval_cycles=%s file_name=%s final=%s",
        scan_rate_mv,
        trigger_cycles,
        current_cycles,
        interval_cycles,
        output_name,
        final,
    )
    _append_workflow_event(
        context,
        "workflow_eis_after_cv_triggered",
        {
            "segment_id": segment.segment_id,
            "display_name": segment.display_name,
            "scan_rate_mv": scan_rate_mv,
            "trigger_cycles": trigger_cycles,
            "current_cycles": current_cycles,
            "interval_cycles": interval_cycles,
            "file_name": output_name,
            "final": final,
        },
    )

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
            "scan_rate_mv": scan_rate_mv,
            "trigger_cycles": trigger_cycles,
            "current_cycles": current_cycles,
            "interval_cycles": interval_cycles,
            "file_name": output_name,
            "file_path": post_run_result["save"]["file_path"],
            "run_poll_count": post_run_result["run_finish"]["poll_count"],
            "final": final,
        },
    )


def _run_eis_after_gcd_cycles(
    context: RuntimeContext,
    segment: WorkflowSegment,
    save_directory: str | Path,
    *,
    density_label: Any,
    trigger_cycles: int,
    current_cycles: int,
    interval_cycles: int,
    final: bool,
) -> None:
    eis_config = build_eis_front_half_config_for_segment(segment)
    output_name = _build_eis_after_gcd_cycles_filename(density_label, current_cycles, final=final)
    context.logger.info(
        "Workflow EIS-after-GCD trigger: trigger_cycles=%s current_cycles=%s interval_cycles=%s file_name=%s final=%s",
        trigger_cycles,
        current_cycles,
        interval_cycles,
        output_name,
        final,
    )
    _append_workflow_event(
        context,
        "workflow_eis_after_gcd_triggered",
        {
            "segment_id": segment.segment_id,
            "display_name": segment.display_name,
            "density": density_label,
            "trigger_cycles": trigger_cycles,
            "current_cycles": current_cycles,
            "interval_cycles": interval_cycles,
            "file_name": output_name,
            "final": final,
        },
    )

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
            "density": density_label,
            "trigger_cycles": trigger_cycles,
            "current_cycles": current_cycles,
            "interval_cycles": interval_cycles,
            "file_name": output_name,
            "file_path": post_run_result["save"]["file_path"],
            "run_poll_count": post_run_result["run_finish"]["poll_count"],
            "final": final,
        },
    )


def _run_segment(
    context: RuntimeContext,
    segment: WorkflowSegment,
    save_directory: str | Path,
    runtime_key: str,
    eis_after_gcd_segment: WorkflowSegment | None = None,
    eis_after_cv_segment: WorkflowSegment | None = None,
) -> None:
    _raise_if_stop_requested(context, f"segment_start:{segment.display_name}")
    if not segment_is_runnable(segment):
        raise Chi660eAutoError(
            f"Segment is modeled but not runnable yet: {segment.display_name} ({segment.segment_type.value})"
        )

    if segment.segment_type == WorkflowSegmentType.ACTIVATION_CV:
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

    if segment.segment_type == WorkflowSegmentType.CV_SERIES_ITEM:
        cv_config = build_cv_front_half_config_for_segment(segment)
        output_name = build_output_filename_for_segment(segment)
        if output_name is None:
            raise RuntimeError(f"Missing output filename for segment {segment.segment_id!r}.")
        repeat_count = int(segment.params.get("repeat_count", 1))
        if repeat_count < 1:
            raise Chi660eAutoError(f"CV repeat_count must be >= 1: {repeat_count}")
        cv_cycles_per_run = _compute_cv_cycles_per_run(segment.params["sweep_segments"])
        total_cycles = cv_cycles_per_run * repeat_count
        interval_cycles = 1000
        next_eis_at = interval_cycles
        last_eis_after_cycles = 0
        if eis_after_cv_segment is not None:
            try:
                interval_cycles = int(eis_after_cv_segment.params.get("interval_cycles", 1000))
            except (TypeError, ValueError) as exc:
                raise Chi660eAutoError("EIS-after-CV 间隔圈数必须为正整数。") from exc
            if interval_cycles < 1:
                raise Chi660eAutoError("EIS-after-CV 间隔圈数必须为正整数。")
            next_eis_at = interval_cycles

        scan_rate_mv = segment.params.get("scan_rate_mv")
        context.logger.info(
            "Workflow CV run values: scan_rate_mv=%s repeat_count=%s cv_cycles_per_run=%s total_cycles=%s",
            scan_rate_mv,
            repeat_count,
            cv_cycles_per_run,
            total_cycles,
        )

        force_cv_front_half = False
        for repeat_index in range(1, repeat_count + 1):
            repeat_output_name = (
                output_name if repeat_count == 1 else _build_repeat_output_name(output_name, repeat_index)
            )
            run_front_half = repeat_index == 1 or force_cv_front_half
            context.logger.info(
                "Workflow CV repeat start: scan_rate_mv=%s repeat_index=%s repeat_count=%s file_name=%s run_front_half=%s",
                scan_rate_mv,
                repeat_index,
                repeat_count,
                repeat_output_name,
                run_front_half,
            )
            _append_workflow_event(
                context,
                "workflow_cv_repeat_start",
                {
                    "segment_id": segment.segment_id,
                    "display_name": segment.display_name,
                    "scan_rate_mv": scan_rate_mv,
                    "repeat_index": repeat_index,
                    "repeat_count": repeat_count,
                    "file_name": repeat_output_name,
                    "run_front_half": run_front_half,
                },
            )

            if run_front_half:
                run_cv_front_half_on_context(context, cv_config)
                force_cv_front_half = False
                _raise_if_stop_requested(context, f"segment_after_front_half:{segment.display_name}")
            else:
                context.logger.info(
                    "CV repeat skip front-half: scan_rate_mv=%s repeat=%s/%s",
                    scan_rate_mv,
                    repeat_index,
                    repeat_count,
                )
                _append_workflow_event(
                    context,
                    "workflow_cv_repeat_front_half_skipped",
                    {
                        "segment_id": segment.segment_id,
                        "display_name": segment.display_name,
                        "scan_rate_mv": scan_rate_mv,
                        "repeat_index": repeat_index,
                        "repeat_count": repeat_count,
                        "file_name": repeat_output_name,
                    },
                )
                _raise_if_stop_requested(context, f"segment_skip_front_half:{segment.display_name}")

            post_run_result = run_post_run_public_flow(
                context,
                save_directory=save_directory,
                file_name=repeat_output_name,
            )
            _append_workflow_event(
                context,
                "workflow_segment_saved",
                {
                    "segment_id": segment.segment_id,
                    "display_name": segment.display_name,
                    "scan_rate_mv": scan_rate_mv,
                    "repeat_index": repeat_index,
                    "repeat_count": repeat_count,
                    "file_name": repeat_output_name,
                    "file_path": post_run_result["save"]["file_path"],
                    "run_poll_count": post_run_result["run_finish"]["poll_count"],
                },
            )
            current_cycles = cv_cycles_per_run * repeat_index
            context.logger.info(
                "Workflow CV cycles: scan_rate_mv=%s repeat_index=%s repeat_count=%s current_cycles=%s total_cycles=%s cv_cycles_per_run=%s",
                scan_rate_mv,
                repeat_index,
                repeat_count,
                current_cycles,
                total_cycles,
                cv_cycles_per_run,
            )
            _append_workflow_event(
                context,
                "workflow_cv_cycles_updated",
                {
                    "segment_id": segment.segment_id,
                    "display_name": segment.display_name,
                    "scan_rate_mv": scan_rate_mv,
                    "repeat_index": repeat_index,
                    "repeat_count": repeat_count,
                    "current_cycles": current_cycles,
                    "total_cycles": total_cycles,
                    "cv_cycles_per_run": cv_cycles_per_run,
                },
            )
            if eis_after_cv_segment is not None and current_cycles >= next_eis_at:
                trigger_cycles = next_eis_at
                _run_eis_after_cv_cycles(
                    context,
                    eis_after_cv_segment,
                    save_directory,
                    scan_rate_mv=scan_rate_mv,
                    trigger_cycles=trigger_cycles,
                    current_cycles=current_cycles,
                    interval_cycles=interval_cycles,
                    final=False,
                )
                last_eis_after_cycles = current_cycles
                force_cv_front_half = True
                while next_eis_at <= current_cycles:
                    next_eis_at += interval_cycles
            context.logger.info(
                "Workflow CV repeat completed: scan_rate_mv=%s repeat_index=%s repeat_count=%s file_name=%s",
                scan_rate_mv,
                repeat_index,
                repeat_count,
                repeat_output_name,
            )
        if eis_after_cv_segment is not None and last_eis_after_cycles < total_cycles:
            _run_eis_after_cv_cycles(
                context,
                eis_after_cv_segment,
                save_directory,
                scan_rate_mv=scan_rate_mv,
                trigger_cycles=total_cycles,
                current_cycles=total_cycles,
                interval_cycles=interval_cycles,
                final=True,
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
        repeat_count = int(segment.params.get("repeat_count", 1))
        if repeat_count < 1:
            raise Chi660eAutoError(f"GCD repeat_count must be >= 1: {repeat_count}")
        gcd_cycles_per_run = _compute_gcd_cycles_per_run(segment.params["number_of_segments"])
        total_cycles = gcd_cycles_per_run * repeat_count
        interval_cycles = 1000
        next_eis_at = interval_cycles
        last_eis_after_cycles = 0
        if eis_after_gcd_segment is not None:
            try:
                interval_cycles = int(eis_after_gcd_segment.params.get("interval_cycles", 1000))
            except (TypeError, ValueError) as exc:
                raise Chi660eAutoError("EIS-after-GCD 间隔圈数必须为正整数。") from exc
            if interval_cycles < 1:
                raise Chi660eAutoError("EIS-after-GCD 间隔圈数必须为正整数。")
            next_eis_at = interval_cycles

        context.logger.info(
            "Workflow GCD run values: density=%s cathodic=%s anodic=%s high_e=%s repeat_count=%s gcd_cycles_per_run=%s total_cycles=%s",
            gcd_run_values["density_label_text"],
            gcd_run_values["cathodic_current_a_text"],
            gcd_run_values["anodic_current_a_text"],
            gcd_run_values["high_e_limit_v_text"],
            repeat_count,
            gcd_cycles_per_run,
            total_cycles,
        )

        force_gcd_front_half = False
        for repeat_index in range(1, repeat_count + 1):
            repeat_output_name = (
                output_name if repeat_count == 1 else _build_repeat_output_name(output_name, repeat_index)
            )
            run_front_half = repeat_index == 1 or force_gcd_front_half
            context.logger.info(
                "Workflow GCD repeat start: density=%s repeat_index=%s repeat_count=%s file_name=%s run_front_half=%s",
                gcd_run_values["density_label_text"],
                repeat_index,
                repeat_count,
                repeat_output_name,
                run_front_half,
            )
            _append_workflow_event(
                context,
                "workflow_gcd_repeat_start",
                {
                    "segment_id": segment.segment_id,
                    "display_name": segment.display_name,
                    "density": gcd_run_values["density_label_text"],
                    "repeat_index": repeat_index,
                    "repeat_count": repeat_count,
                    "file_name": repeat_output_name,
                    "run_front_half": run_front_half,
                },
            )

            if run_front_half:
                run_gcd_front_half_on_context(context, gcd_run_values)
                force_gcd_front_half = False
                _raise_if_stop_requested(context, f"segment_after_front_half:{segment.display_name}")
            else:
                context.logger.info(
                    "GCD repeat skip front-half: density=%s repeat=%s/%s",
                    gcd_run_values["density_label_text"],
                    repeat_index,
                    repeat_count,
                )
                _append_workflow_event(
                    context,
                    "workflow_gcd_repeat_front_half_skipped",
                    {
                        "segment_id": segment.segment_id,
                        "display_name": segment.display_name,
                        "density": gcd_run_values["density_label_text"],
                        "repeat_index": repeat_index,
                        "repeat_count": repeat_count,
                        "file_name": repeat_output_name,
                    },
                )
                _raise_if_stop_requested(context, f"segment_skip_front_half:{segment.display_name}")

            post_run_result = run_post_run_public_flow(
                context,
                save_directory=save_directory,
                file_name=repeat_output_name,
            )
            _append_workflow_event(
                context,
                "workflow_segment_saved",
                {
                    "segment_id": segment.segment_id,
                    "display_name": segment.display_name,
                    "density": gcd_run_values["density_label_text"],
                    "repeat_index": repeat_index,
                    "repeat_count": repeat_count,
                    "file_name": repeat_output_name,
                    "file_path": post_run_result["save"]["file_path"],
                    "run_poll_count": post_run_result["run_finish"]["poll_count"],
                },
            )
            current_cycles = gcd_cycles_per_run * repeat_index
            context.logger.info(
                "Workflow GCD cycles: density=%s repeat_index=%s repeat_count=%s current_cycles=%s total_cycles=%s gcd_cycles_per_run=%s",
                gcd_run_values["density_label_text"],
                repeat_index,
                repeat_count,
                current_cycles,
                total_cycles,
                gcd_cycles_per_run,
            )
            _append_workflow_event(
                context,
                "workflow_gcd_cycles_updated",
                {
                    "segment_id": segment.segment_id,
                    "display_name": segment.display_name,
                    "density": gcd_run_values["density_label_text"],
                    "repeat_index": repeat_index,
                    "repeat_count": repeat_count,
                    "current_cycles": current_cycles,
                    "total_cycles": total_cycles,
                    "gcd_cycles_per_run": gcd_cycles_per_run,
                },
            )
            if eis_after_gcd_segment is not None and current_cycles >= next_eis_at:
                trigger_cycles = next_eis_at
                _run_eis_after_gcd_cycles(
                    context,
                    eis_after_gcd_segment,
                    save_directory,
                    density_label=gcd_run_values["density_label_text"],
                    trigger_cycles=trigger_cycles,
                    current_cycles=current_cycles,
                    interval_cycles=interval_cycles,
                    final=False,
                )
                last_eis_after_cycles = current_cycles
                force_gcd_front_half = True
                while next_eis_at <= current_cycles:
                    next_eis_at += interval_cycles
            context.logger.info(
                "Workflow GCD repeat completed: density=%s repeat_index=%s repeat_count=%s file_name=%s",
                gcd_run_values["density_label_text"],
                repeat_index,
                repeat_count,
                repeat_output_name,
            )
        if eis_after_gcd_segment is not None and last_eis_after_cycles < total_cycles:
            _run_eis_after_gcd_cycles(
                context,
                eis_after_gcd_segment,
                save_directory,
                density_label=gcd_run_values["density_label_text"],
                trigger_cycles=total_cycles,
                current_cycles=total_cycles,
                interval_cycles=interval_cycles,
                final=True,
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
    inline_eis_after_cv_segment = next(
        (
            segment
            for segment in ordered_segments
            if segment.segment_type == WorkflowSegmentType.EIS_AFTER_CV
        ),
        None,
    )
    inline_eis_after_gcd_segment = next(
        (
            segment
            for segment in ordered_segments
            if segment.segment_type == WorkflowSegmentType.EIS_AFTER_GCD
        ),
        None,
    )
    has_cv_series = any(segment.segment_type == WorkflowSegmentType.CV_SERIES_ITEM for segment in ordered_segments)
    has_gcd_series = any(segment.segment_type == WorkflowSegmentType.GCD_SERIES_ITEM for segment in ordered_segments)
    skip_segment_indices: set[int] = set()
    cv_skip_segment_indices: set[int] = set()
    if inline_eis_after_cv_segment is not None and has_cv_series:
        eis_index = ordered_segments.index(inline_eis_after_cv_segment)
        skip_segment_indices.add(eis_index)
        cv_skip_segment_indices.add(eis_index)
        if eis_index > 0 and ordered_segments[eis_index - 1].segment_type == WorkflowSegmentType.REST:
            skip_segment_indices.add(eis_index - 1)
            cv_skip_segment_indices.add(eis_index - 1)
    if inline_eis_after_gcd_segment is not None and has_gcd_series:
        eis_index = ordered_segments.index(inline_eis_after_gcd_segment)
        skip_segment_indices.add(eis_index)
        if eis_index > 0 and ordered_segments[eis_index - 1].segment_type == WorkflowSegmentType.REST:
            skip_segment_indices.add(eis_index - 1)

    runtime_context = context or bootstrap_app()
    runtime_context.run_control = run_control

    try:
        current_segment: WorkflowSegment | None = None
        current_runtime_key = ""
        for index, segment in enumerate(ordered_segments, start=1):
            segment_index = index - 1
            if segment_index in skip_segment_indices:
                if segment_index in cv_skip_segment_indices:
                    runtime_context.logger.info(
                        "Workflow segment consumed by CV loop: index=%s/%s type=%s name=%s",
                        index,
                        len(ordered_segments),
                        segment.segment_type.value,
                        segment.display_name,
                    )
                    _append_workflow_event(
                        runtime_context,
                        "workflow_segment_consumed_by_cv_loop",
                        {
                            "index": index,
                            "total": len(ordered_segments),
                            "segment_id": segment.segment_id,
                            "segment_type": segment.segment_type.value,
                            "display_name": segment.display_name,
                        },
                    )
                    continue
                runtime_context.logger.info(
                    "Workflow segment consumed by GCD loop: index=%s/%s type=%s name=%s",
                    index,
                    len(ordered_segments),
                    segment.segment_type.value,
                    segment.display_name,
                )
                _append_workflow_event(
                    runtime_context,
                    "workflow_segment_consumed_by_gcd_loop",
                    {
                        "index": index,
                        "total": len(ordered_segments),
                        "segment_id": segment.segment_id,
                        "segment_type": segment.segment_type.value,
                        "display_name": segment.display_name,
                    },
                )
                continue
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

            _run_segment(
                runtime_context,
                segment,
                save_directory,
                current_runtime_key,
                eis_after_cv_segment=(
                    inline_eis_after_cv_segment
                    if segment.segment_type == WorkflowSegmentType.CV_SERIES_ITEM
                    and segment_index not in skip_segment_indices
                    else None
                ),
                eis_after_gcd_segment=(
                    inline_eis_after_gcd_segment
                    if segment.segment_type == WorkflowSegmentType.GCD_SERIES_ITEM
                    and segment_index not in skip_segment_indices
                    else None
                ),
            )
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
