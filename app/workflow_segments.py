from __future__ import annotations

"""工作流任务段模型。

这里定义可排序、可启停、可序列化的任务段结构。
workflow_runner 负责执行，visual_action_specs 继续负责视觉几何。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.cv_config import CVFrontHalfConfig
from app.eis_config import EISFrontHalfConfig, get_default_eis_front_half_config
from app.gcd_config import (
    DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2,
    DEFAULT_GCD_DATA_STORAGE_INTERVAL_SEC,
    DEFAULT_GCD_ELECTRODE_AREA_CM2,
    DEFAULT_GCD_HIGH_E_LIMIT_MV,
    DEFAULT_GCD_NUMBER_OF_SEGMENTS,
    GCDFrontHalfConfig,
    build_gcd_run_values,
)
from app.naming_rules import (
    build_activation_cv_filename,
    build_cv_filename,
    build_eis_after_activation_filename,
    build_eis_after_cv_filename,
    build_eis_after_gcd_filename,
    build_gcd_filename,
)


DEFAULT_ACTIVATION_SCAN_RATE_VS = 0.2
DEFAULT_CV_SCAN_RATE_SERIES_MV = [2, 5, 10, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 600, 700, 800, 900, 1000]
DEFAULT_GCD_CURRENT_DENSITY_SERIES_MA_CM2 = list(DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2)
DEFAULT_REST_DURATION_SEC = 600


class WorkflowSegmentType(str, Enum):
    ACTIVATION_CV = "activation_cv"
    EIS_AFTER_ACTIVATION = "eis_after_activation"
    CV_SERIES_ITEM = "cv_series_item"
    REST = "rest"
    EIS_AFTER_CV = "eis_after_cv"
    GCD_SERIES_ITEM = "gcd_series_item"
    EIS_AFTER_GCD = "eis_after_gcd"


@dataclass(slots=True)
class WorkflowSegment:
    segment_id: str
    segment_type: WorkflowSegmentType
    enabled: bool
    order: int
    display_name: str
    params: dict[str, Any] = field(default_factory=dict)


def segment_block_reason(segment: WorkflowSegment) -> str | None:
    if segment.segment_type in {
        WorkflowSegmentType.ACTIVATION_CV,
        WorkflowSegmentType.EIS_AFTER_ACTIVATION,
        WorkflowSegmentType.CV_SERIES_ITEM,
        WorkflowSegmentType.REST,
        WorkflowSegmentType.EIS_AFTER_CV,
        WorkflowSegmentType.GCD_SERIES_ITEM,
        WorkflowSegmentType.EIS_AFTER_GCD,
    }:
        return None
    return f"Unknown segment type: {segment.segment_type.value!r}"


def segment_is_runnable(segment: WorkflowSegment) -> bool:
    return segment_block_reason(segment) is None


def build_activation_cv_segment(
    order: int,
    scan_rate_vs: float = DEFAULT_ACTIVATION_SCAN_RATE_VS,
    high_potential: str = "0.8",
    sweep_segments: str = "4",
    sensitivity: str = "1.e-003",
) -> WorkflowSegment:
    return WorkflowSegment(
        segment_id="activation_cv",
        segment_type=WorkflowSegmentType.ACTIVATION_CV,
        enabled=True,
        order=order,
        display_name=f"活化 {_format_mv(scan_rate_vs)}mv",
        params={
            "scan_rate_vs": float(scan_rate_vs),
            "high_potential": str(high_potential),
            "sweep_segments": str(sweep_segments),
            "sensitivity": str(sensitivity),
        },
    )


def build_eis_after_activation_segment(
    order: int,
    config: EISFrontHalfConfig | None = None,
) -> WorkflowSegment:
    config = config or get_default_eis_front_half_config()
    return WorkflowSegment(
        segment_id="eis_after_activation",
        segment_type=WorkflowSegmentType.EIS_AFTER_ACTIVATION,
        enabled=True,
        order=order,
        display_name="EIS-after activation",
        params={
            "high_frequency_hz": str(config.high_frequency_hz),
            "low_frequency_hz": str(config.low_frequency_hz),
            "avg_cycles_0p1_to_1hz": str(config.avg_cycles_0p1_to_1hz),
            "avg_cycles_0p01_to_0p1hz": str(config.avg_cycles_0p01_to_0p1hz),
        },
    )


def build_cv_series_item_segment(
    order: int,
    scan_rate_mv: float | int,
    high_potential: str = "0.8",
    sweep_segments: str = "4",
    sensitivity: str = "1.e-003",
    repeat_count: int = 1,
) -> WorkflowSegment:
    return WorkflowSegment(
        segment_id=f"cv_{scan_rate_mv}",
        segment_type=WorkflowSegmentType.CV_SERIES_ITEM,
        enabled=True,
        order=order,
        display_name=f"cv {_format_number(scan_rate_mv)}mv",
        params={
            "scan_rate_mv": float(scan_rate_mv),
            "high_potential": str(high_potential),
            "sweep_segments": str(sweep_segments),
            "sensitivity": str(sensitivity),
            "repeat_count": int(repeat_count),
        },
    )


def build_rest_segment(order: int, duration_sec: int = DEFAULT_REST_DURATION_SEC) -> WorkflowSegment:
    return WorkflowSegment(
        segment_id=f"rest_{duration_sec}",
        segment_type=WorkflowSegmentType.REST,
        enabled=True,
        order=order,
        display_name=f"静置 {int(duration_sec)}s",
        params={"duration_sec": int(duration_sec)},
    )


def build_eis_after_cv_segment(
    order: int,
    config: EISFrontHalfConfig | None = None,
    interval_cycles: int = 1000,
) -> WorkflowSegment:
    config = config or get_default_eis_front_half_config()
    return WorkflowSegment(
        segment_id="eis_after_cv",
        segment_type=WorkflowSegmentType.EIS_AFTER_CV,
        enabled=True,
        order=order,
        display_name="EIS-after cv",
        params={
            "high_frequency_hz": str(config.high_frequency_hz),
            "low_frequency_hz": str(config.low_frequency_hz),
            "avg_cycles_0p1_to_1hz": str(config.avg_cycles_0p1_to_1hz),
            "avg_cycles_0p01_to_0p1hz": str(config.avg_cycles_0p01_to_0p1hz),
            "interval_cycles": int(interval_cycles),
        },
    )


def build_gcd_series_item_segment(
    order: int,
    current_density_ma_cm2: float | int,
    electrode_area_cm2: float = DEFAULT_GCD_ELECTRODE_AREA_CM2,
    high_e_limit_mv: float = DEFAULT_GCD_HIGH_E_LIMIT_MV,
    data_storage_interval_sec: str = DEFAULT_GCD_DATA_STORAGE_INTERVAL_SEC,
    number_of_segments: str = DEFAULT_GCD_NUMBER_OF_SEGMENTS,
    repeat_count: int = 1,
) -> WorkflowSegment:
    return WorkflowSegment(
        segment_id=f"gcd_{current_density_ma_cm2}",
        segment_type=WorkflowSegmentType.GCD_SERIES_ITEM,
        enabled=True,
        order=order,
        display_name=f"gcd {_format_number(current_density_ma_cm2)}ma",
        params={
            "current_density_ma_cm2": float(current_density_ma_cm2),
            "electrode_area_cm2": float(electrode_area_cm2),
            "high_e_limit_mv": float(high_e_limit_mv),
            "data_storage_interval_sec": str(data_storage_interval_sec),
            "number_of_segments": str(number_of_segments),
            "repeat_count": int(repeat_count),
        },
    )


def build_eis_after_gcd_segment(
    order: int,
    config: EISFrontHalfConfig | None = None,
    interval_cycles: int = 1000,
) -> WorkflowSegment:
    config = config or get_default_eis_front_half_config()
    return WorkflowSegment(
        segment_id="eis_after_gcd",
        segment_type=WorkflowSegmentType.EIS_AFTER_GCD,
        enabled=True,
        order=order,
        display_name="EIS-after GCD",
        params={
            "high_frequency_hz": str(config.high_frequency_hz),
            "low_frequency_hz": str(config.low_frequency_hz),
            "avg_cycles_0p1_to_1hz": str(config.avg_cycles_0p1_to_1hz),
            "avg_cycles_0p01_to_0p1hz": str(config.avg_cycles_0p01_to_0p1hz),
            "interval_cycles": int(interval_cycles),
        },
    )


def segment_requires_run(segment: WorkflowSegment) -> bool:
    return segment.segment_type != WorkflowSegmentType.REST


def segment_requires_save_as(segment: WorkflowSegment) -> bool:
    return segment.segment_type in {
        WorkflowSegmentType.ACTIVATION_CV,
        WorkflowSegmentType.CV_SERIES_ITEM,
        WorkflowSegmentType.EIS_AFTER_ACTIVATION,
        WorkflowSegmentType.EIS_AFTER_CV,
        WorkflowSegmentType.GCD_SERIES_ITEM,
        WorkflowSegmentType.EIS_AFTER_GCD,
    }


def segment_is_rest(segment: WorkflowSegment) -> bool:
    return segment.segment_type == WorkflowSegmentType.REST


def build_cv_front_half_config_for_segment(segment: WorkflowSegment) -> CVFrontHalfConfig:
    if segment.segment_type == WorkflowSegmentType.ACTIVATION_CV:
        return CVFrontHalfConfig(
            high_potential=str(segment.params["high_potential"]),
            scan_rate=str(segment.params["scan_rate_vs"]),
            sweep_segments=str(segment.params["sweep_segments"]),
            sensitivity=str(segment.params["sensitivity"]),
        )

    if segment.segment_type == WorkflowSegmentType.CV_SERIES_ITEM:
        scan_rate_vs = float(segment.params["scan_rate_mv"]) / 1000.0
        return CVFrontHalfConfig(
            high_potential=str(segment.params["high_potential"]),
            scan_rate=str(scan_rate_vs),
            sweep_segments=str(segment.params["sweep_segments"]),
            sensitivity=str(segment.params["sensitivity"]),
        )

    raise NotImplementedError(
        f"Front-half parameter build is not implemented for segment type {segment.segment_type.value!r}."
    )


def build_eis_front_half_config_for_segment(segment: WorkflowSegment) -> EISFrontHalfConfig:
    if segment.segment_type in {
        WorkflowSegmentType.EIS_AFTER_ACTIVATION,
        WorkflowSegmentType.EIS_AFTER_CV,
        WorkflowSegmentType.EIS_AFTER_GCD,
    }:
        return EISFrontHalfConfig(
            high_frequency_hz=str(segment.params["high_frequency_hz"]),
            low_frequency_hz=str(segment.params["low_frequency_hz"]),
            avg_cycles_0p1_to_1hz=str(segment.params["avg_cycles_0p1_to_1hz"]),
            avg_cycles_0p01_to_0p1hz=str(segment.params["avg_cycles_0p01_to_0p1hz"]),
        )

    raise NotImplementedError(
        f"EIS front-half parameter build is not implemented for segment type {segment.segment_type.value!r}."
    )


def build_gcd_run_values_for_segment(segment: WorkflowSegment) -> dict[str, str]:
    if segment.segment_type != WorkflowSegmentType.GCD_SERIES_ITEM:
        raise NotImplementedError(
            f"GCD run-value build is not implemented for segment type {segment.segment_type.value!r}."
        )

    config = GCDFrontHalfConfig(
        electrode_area_cm2=float(segment.params["electrode_area_cm2"]),
        current_density_ma_cm2_list=(float(segment.params["current_density_ma_cm2"]),),
        high_e_limit_mv=float(segment.params["high_e_limit_mv"]),
        data_storage_interval_sec=str(segment.params["data_storage_interval_sec"]),
        number_of_segments=str(segment.params["number_of_segments"]),
    )
    return build_gcd_run_values(config, float(segment.params["current_density_ma_cm2"]))


def build_output_filename_for_segment(segment: WorkflowSegment) -> str | None:
    if segment.segment_type == WorkflowSegmentType.ACTIVATION_CV:
        return build_activation_cv_filename(segment.params["scan_rate_vs"])
    if segment.segment_type == WorkflowSegmentType.EIS_AFTER_ACTIVATION:
        return build_eis_after_activation_filename()
    if segment.segment_type == WorkflowSegmentType.CV_SERIES_ITEM:
        return build_cv_filename(segment.params["scan_rate_mv"])
    if segment.segment_type == WorkflowSegmentType.EIS_AFTER_CV:
        return build_eis_after_cv_filename()
    if segment.segment_type == WorkflowSegmentType.GCD_SERIES_ITEM:
        return build_gcd_filename(segment.params["current_density_ma_cm2"])
    if segment.segment_type == WorkflowSegmentType.EIS_AFTER_GCD:
        return build_eis_after_gcd_filename()
    return None


def sort_enabled_segments(segments: list[WorkflowSegment]) -> list[WorkflowSegment]:
    return sorted((segment for segment in segments if segment.enabled), key=lambda item: item.order)


def build_default_segment_plan(
    electrode_area_cm2: float = DEFAULT_GCD_ELECTRODE_AREA_CM2,
    rest_duration_sec: int = DEFAULT_REST_DURATION_SEC,
) -> list[WorkflowSegment]:
    order = 1
    segments: list[WorkflowSegment] = [
        build_activation_cv_segment(order=order),
    ]
    order += 1
    segments.append(build_eis_after_activation_segment(order=order))
    order += 1

    for scan_rate_mv in DEFAULT_CV_SCAN_RATE_SERIES_MV:
        segments.append(build_cv_series_item_segment(order=order, scan_rate_mv=scan_rate_mv))
        order += 1

    segments.append(build_rest_segment(order=order, duration_sec=rest_duration_sec))
    order += 1
    segments.append(build_eis_after_cv_segment(order=order))
    order += 1

    for current_density in DEFAULT_GCD_CURRENT_DENSITY_SERIES_MA_CM2:
        segments.append(
            build_gcd_series_item_segment(
                order=order,
                current_density_ma_cm2=current_density,
                electrode_area_cm2=electrode_area_cm2,
            )
        )
        order += 1

    segments.append(build_eis_after_gcd_segment(order=order))
    return segments


def build_default_runnable_segment_plan() -> list[WorkflowSegment]:
    return [
        build_activation_cv_segment(order=1),
    ]


def _format_number(value: float | int) -> str:
    return f"{float(value):g}"


def _format_mv(scan_rate_vs: float) -> str:
    return _format_number(float(scan_rate_vs) * 1000.0)

