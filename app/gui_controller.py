from __future__ import annotations

"""GUI 控制器。

只负责把 GUI state 映射成 workflow segments、执行计划预览与状态提示，
不直接执行自动化动作。
"""

from app.eis_config import EISFrontHalfConfig
from app.gui_models import (
    CV_SCAN_RATE_OPTIONS_MV,
    DEFAULT_SELECTED_PAGE,
    GCD_CURRENT_DENSITY_OPTIONS_MA_CM2,
    PAGE_GLOBAL_SETTINGS,
    TASK_BUCKET_ORDER,
    WorkflowGuiState,
    build_default_gui_state as _build_default_gui_state,
)
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

TASK_BUCKET_LABELS = {
    "activation_cv": "活化",
    "eis_after_activation": "EIS-after activation",
    "cv_series": "CV",
    "eis_after_cv": "EIS-after cv",
    "gcd_series": "GCD",
    "eis_after_gcd": "EIS-after gcd",
    PAGE_GLOBAL_SETTINGS: "全局设置",
}

TASK_SETTINGS_PAGES = {
    "activation_cv": "活化",
    "eis_after_activation": "EIS-after activation",
    "cv_series": "CV",
    "eis_after_cv": "EIS-after cv",
    "gcd_series": "GCD",
    "eis_after_gcd": "EIS-after gcd",
    PAGE_GLOBAL_SETTINGS: "全局设置",
}


def build_default_gui_state() -> WorkflowGuiState:
    return _build_default_gui_state()


def parse_numeric_series(text: str) -> list[float]:
    values: list[float] = []
    for raw in text.replace("\n", ",").split(","):
        stripped = raw.strip()
        if not stripped:
            continue
        values.append(float(stripped))
    return values


def _parse_float(text: str, field_name: str) -> float:
    stripped = str(text).strip()
    if not stripped:
        raise ValueError(f"{field_name} 不能为空。")
    return float(stripped)


def _parse_int(text: str, field_name: str) -> int:
    stripped = str(text).strip()
    if not stripped:
        raise ValueError(f"{field_name} 不能为空。")
    return int(float(stripped))


def _parse_positive_int(text: str, field_name: str) -> int:
    stripped = str(text).strip()
    if not stripped:
        raise ValueError(f"{field_name} 不能为空。")
    try:
        value = int(stripped)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须为正整数。") from exc
    if value < 1:
        raise ValueError(f"{field_name} 必须大于等于 1。")
    return value


def _minutes_to_seconds(text: str, field_name: str) -> int:
    minutes = _parse_float(text, field_name)
    return max(0, int(minutes * 60))


def _resolve_selected_cv_scan_rates_mv(state: WorkflowGuiState) -> list[float]:
    values: list[float] = []
    for index in state.cv_scan_rate_selected_indices:
        try:
            raw_value = str(state.cv_scan_rate_entry_values_mv[index]).strip()
        except IndexError as exc:
            raise ValueError(f"CV Scan Rate 第 {index + 1} 项索引无效。") from exc
        if not raw_value:
            raise ValueError(f"CV Scan Rate 第 {index + 1} 项不能为空。")
        try:
            values.append(float(raw_value))
        except ValueError as exc:
            raise ValueError(f"CV Scan Rate 第 {index + 1} 项不是有效数字。") from exc
    return values


def _resolve_selected_gcd_current_densities_ma_cm2(state: WorkflowGuiState) -> list[float]:
    values: list[float] = []
    for index in state.gcd_current_density_selected_indices:
        try:
            raw_value = str(state.gcd_current_density_entry_values_ma_cm2[index]).strip()
        except IndexError as exc:
            raise ValueError(f"GCD 电流密度第 {index + 1} 项索引无效。") from exc
        if not raw_value:
            raise ValueError(f"GCD 电流密度第 {index + 1} 项不能为空。")
        try:
            values.append(float(raw_value))
        except ValueError as exc:
            raise ValueError(f"GCD 电流密度第 {index + 1} 项不是有效数字。") from exc
    return values


def move_segment_bucket(state: WorkflowGuiState, bucket: str, direction: int) -> None:
    """保留未来扩展用；当前 GUI 固定顺序，本轮不在界面中暴露。"""
    try:
        index = state.task_order.index(bucket)
    except ValueError:
        return

    new_index = max(0, min(len(state.task_order) - 1, index + direction))
    if new_index == index:
        return

    state.task_order[index], state.task_order[new_index] = (
        state.task_order[new_index],
        state.task_order[index],
    )


def build_segments_from_gui_state(state: WorkflowGuiState) -> list[WorkflowSegment]:
    segments: list[WorkflowSegment] = []
    order = 1

    for bucket in state.task_order or list(TASK_BUCKET_ORDER):
        if bucket == "activation_cv" and state.enable_activation_cv:
            segments.append(
                build_activation_cv_segment(
                    order=order,
                    scan_rate_vs=_parse_float(state.activation_scan_rate_vs, "活化 Scan Rate"),
                    high_potential=state.activation_high_e_v.strip(),
                    sweep_segments=state.activation_sweep_segments.strip(),
                    sensitivity=state.activation_sensitivity.strip(),
                )
            )
            order += 1
            continue

        if bucket == "eis_after_activation" and state.enable_eis_after_activation:
            segments.append(
                build_eis_after_activation_segment(
                    order=order,
                    config=EISFrontHalfConfig(
                        high_frequency_hz=state.eis_after_activation_high_frequency_hz.strip(),
                        low_frequency_hz=state.eis_after_activation_low_frequency_hz.strip(),
                        avg_cycles_0p1_to_1hz=state.eis_after_activation_avg_cycles_0p1_to_1hz.strip(),
                        avg_cycles_0p01_to_0p1hz=state.eis_after_activation_avg_cycles_0p01_to_0p1hz.strip(),
                    ),
                )
            )
            order += 1
            continue

        if bucket == "cv_series" and state.enable_cv_series:
            repeat_count = _parse_positive_int(state.cv_repeat_count, "CV 循环次数")
            for scan_rate_mv in _resolve_selected_cv_scan_rates_mv(state):
                segments.append(
                    build_cv_series_item_segment(
                        order=order,
                        scan_rate_mv=scan_rate_mv,
                        high_potential=state.cv_high_e_v.strip(),
                        sweep_segments=state.cv_sweep_segments.strip(),
                        sensitivity=state.cv_sensitivity.strip(),
                        repeat_count=repeat_count,
                    )
                )
                order += 1
            continue

        if bucket == "eis_after_cv" and state.enable_eis_after_cv:
            rest_segment = build_rest_segment(
                order=order,
                duration_sec=_minutes_to_seconds(state.eis_after_cv_rest_minutes, "EIS-after cv 静置"),
            )
            rest_segment.display_name = f"静置 {state.eis_after_cv_rest_minutes.strip()}min"
            segments.append(rest_segment)
            order += 1

            segment = build_eis_after_cv_segment(
                order=order,
                config=EISFrontHalfConfig(
                    high_frequency_hz=state.eis_after_cv_high_frequency_hz.strip(),
                    low_frequency_hz=state.eis_after_cv_low_frequency_hz.strip(),
                    avg_cycles_0p1_to_1hz=state.eis_after_cv_avg_cycles_0p1_to_1hz.strip(),
                    avg_cycles_0p01_to_0p1hz=state.eis_after_cv_avg_cycles_0p01_to_0p1hz.strip(),
                ),
            )
            segments.append(segment)
            order += 1
            continue

        if bucket == "gcd_series" and state.enable_gcd_series:
            electrode_area_cm2 = _parse_float(state.gcd_area_cm2, "GCD 面积")
            high_e_limit_v = _parse_float(state.gcd_high_e_limit_v, "GCD High E limit")
            repeat_count = _parse_positive_int(state.gcd_repeat_count, "GCD 循环次数")
            for density in _resolve_selected_gcd_current_densities_ma_cm2(state):
                segments.append(
                    build_gcd_series_item_segment(
                        order=order,
                        current_density_ma_cm2=density,
                        electrode_area_cm2=electrode_area_cm2,
                        high_e_limit_mv=high_e_limit_v * 1000.0,
                        data_storage_interval_sec=state.gcd_data_storage_interval_sec.strip(),
                        number_of_segments=state.gcd_number_of_segments.strip(),
                        repeat_count=repeat_count,
                    )
                )
                order += 1
            continue

        if bucket == "eis_after_gcd" and state.enable_eis_after_gcd:
            interval_cycles = _parse_positive_int(
                state.eis_after_gcd_interval_cycles,
                "EIS-after-GCD 间隔圈数",
            )
            rest_segment = build_rest_segment(
                order=order,
                duration_sec=_minutes_to_seconds(state.eis_after_gcd_rest_minutes, "EIS-after gcd 静置"),
            )
            rest_segment.display_name = f"静置 {state.eis_after_gcd_rest_minutes.strip()}min"
            segments.append(rest_segment)
            order += 1

            segment = build_eis_after_gcd_segment(
                order=order,
                config=EISFrontHalfConfig(
                    high_frequency_hz=state.eis_after_gcd_high_frequency_hz.strip(),
                    low_frequency_hz=state.eis_after_gcd_low_frequency_hz.strip(),
                    avg_cycles_0p1_to_1hz=state.eis_after_gcd_avg_cycles_0p1_to_1hz.strip(),
                    avg_cycles_0p01_to_0p1hz=state.eis_after_gcd_avg_cycles_0p01_to_0p1hz.strip(),
                ),
                interval_cycles=interval_cycles,
            )
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
        suffix = f" [未接通: {reason}]" if reason is not None else ""
        preview.append(f"{index}. {segment.display_name}{suffix}")
    return preview


def build_default_execution_plan_preview() -> list[str]:
    return build_execution_plan_preview_with_status(build_default_gui_state())


def build_default_segment_plan_for_gui() -> list[WorkflowSegment]:
    return build_default_segment_plan()
