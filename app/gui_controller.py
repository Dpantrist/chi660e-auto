from __future__ import annotations

"""最小 GUI 控制器。

负责把 GUI 输入映射成任务段列表、执行计划预览与状态提示，不直接执行实验流程。
"""

from app.gui_models import WorkflowGuiState
from app.workflow_segments import (
    WorkflowSegment,
    build_activation_cv_segment,
    build_cv_series_item_segment,
    build_default_segment_plan,
    build_eis_after_activation_segment,
    build_eis_after_cv_segment,
    build_eis_after_gcd_segment,
    build_gcd_series_item_segment,
    build_rest_segment,
    segment_block_reason,
    sort_enabled_segments,
)

SEGMENT_BUCKET_LABELS = {
    "activation_cv": "活化 CV",
    "eis_after_activation": "EIS-after activation",
    "cv_series": "CV 序列",
    "rest": "静置",
    "eis_after_cv": "EIS-after cv",
    "gcd_series": "GCD 序列",
    "eis_after_gcd": "EIS-after GCD",
}


def build_default_gui_state() -> WorkflowGuiState:
    return WorkflowGuiState()


def parse_numeric_series(text: str) -> list[float]:
    values: list[float] = []
    for raw in text.replace("\n", ",").split(","):
        stripped = raw.strip()
        if not stripped:
            continue
        values.append(float(stripped))
    return values


def move_segment_bucket(state: WorkflowGuiState, bucket: str, direction: int) -> None:
    try:
        index = state.segment_order.index(bucket)
    except ValueError:
        return

    new_index = max(0, min(len(state.segment_order) - 1, index + direction))
    if new_index == index:
        return

    state.segment_order[index], state.segment_order[new_index] = (
        state.segment_order[new_index],
        state.segment_order[index],
    )


def build_segments_from_gui_state(state: WorkflowGuiState) -> list[WorkflowSegment]:
    segments: list[WorkflowSegment] = []
    order = 1

    for bucket in state.segment_order:
        if bucket == "activation_cv":
            segment = build_activation_cv_segment(
                order=order,
                scan_rate_vs=state.activation_scan_rate_vs,
                high_potential=state.activation_high_potential,
                sweep_segments=state.activation_sweep_segments,
                sensitivity=state.activation_sensitivity,
            )
            segment.enabled = state.enable_activation_cv
            segments.append(segment)
            order += 1
            continue

        if bucket == "eis_after_activation":
            segment = build_eis_after_activation_segment(order=order)
            segment.enabled = state.enable_eis_after_activation
            segments.append(segment)
            order += 1
            continue

        if bucket == "cv_series":
            for scan_rate_mv in state.cv_scan_rates_mv:
                segment = build_cv_series_item_segment(
                    order=order,
                    scan_rate_mv=scan_rate_mv,
                    high_potential=state.activation_high_potential,
                    sweep_segments=state.activation_sweep_segments,
                    sensitivity=state.activation_sensitivity,
                )
                segment.enabled = state.enable_cv_series
                segments.append(segment)
                order += 1
            continue

        if bucket == "rest":
            segment = build_rest_segment(order=order, duration_sec=state.rest_duration_sec)
            segment.enabled = state.enable_rest
            segments.append(segment)
            order += 1
            continue

        if bucket == "eis_after_cv":
            segment = build_eis_after_cv_segment(order=order)
            segment.enabled = state.enable_eis_after_cv
            segments.append(segment)
            order += 1
            continue

        if bucket == "gcd_series":
            for current_density in state.gcd_current_densities_ma_cm2:
                segment = build_gcd_series_item_segment(
                    order=order,
                    current_density_ma_cm2=current_density,
                    electrode_area_cm2=state.electrode_area_cm2,
                )
                segment.enabled = state.enable_gcd_series
                segments.append(segment)
                order += 1
            continue

        if bucket == "eis_after_gcd":
            segment = build_eis_after_gcd_segment(order=order)
            segment.enabled = state.enable_eis_after_gcd
            segments.append(segment)
            order += 1
            continue

    return segments


def build_execution_plan_preview(state: WorkflowGuiState) -> list[str]:
    preview: list[str] = []
    for index, segment in enumerate(sort_enabled_segments(build_segments_from_gui_state(state)), start=1):
        preview.append(f"{index}. {segment.display_name}")
    return preview


def build_execution_plan_preview_with_status(state: WorkflowGuiState) -> list[str]:
    preview: list[str] = []
    for index, segment in enumerate(sort_enabled_segments(build_segments_from_gui_state(state)), start=1):
        reason = segment_block_reason(segment)
        suffix = f" [BLOCKED: {reason}]" if reason is not None else ""
        preview.append(f"{index}. {segment.display_name}{suffix}")
    return preview


def build_default_execution_plan_preview() -> list[str]:
    state = build_default_gui_state()
    return build_execution_plan_preview_with_status(state)


def build_default_segment_plan_for_gui() -> list[WorkflowSegment]:
    return build_default_segment_plan()
