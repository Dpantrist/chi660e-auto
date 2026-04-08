from __future__ import annotations

"""GUI 状态模型。

GUI 只维护用户输入、启用状态和当前页面，不直接承载自动化业务逻辑。
默认值尽量从现有 config / segment builder 读取，避免多处漂移。
"""

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from app.eis_config import get_default_eis_front_half_config
from app.gcd_config import (
    DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2,
    get_default_gcd_gui_config,
)
from app.paths import BASE_DIR
from app.workflow_segments import (
    DEFAULT_CV_SCAN_RATE_SERIES_MV,
    DEFAULT_REST_DURATION_SEC,
    build_activation_cv_segment,
)


TASK_BUCKET_ORDER = (
    "activation_cv",
    "eis_after_activation",
    "cv_series",
    "eis_after_cv",
    "gcd_series",
    "eis_after_gcd",
)
PAGE_GLOBAL_SETTINGS = "global_settings"
DEFAULT_SELECTED_PAGE = "activation_cv"
CV_SCAN_RATE_OPTIONS_MV = tuple(float(item) for item in DEFAULT_CV_SCAN_RATE_SERIES_MV)
GCD_CURRENT_DENSITY_OPTIONS_MA_CM2 = tuple(
    float(item) for item in DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2
)


def _format_plain_number(value: float | int | str) -> str:
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:g}"


def _coerce_float_list(value: Any, fallback: tuple[float, ...]) -> list[float]:
    if not isinstance(value, list):
        return [float(item) for item in fallback]

    result: list[float] = []
    for item in value:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    return result or [float(item) for item in fallback]


@dataclass(slots=True)
class WorkflowGuiState:
    selected_page: str = DEFAULT_SELECTED_PAGE
    save_directory: str = str(BASE_DIR / "tests" / "test_data")

    enable_activation_cv: bool = True
    enable_eis_after_activation: bool = True
    enable_cv_series: bool = True
    enable_eis_after_cv: bool = False
    enable_gcd_series: bool = True
    enable_eis_after_gcd: bool = False

    activation_high_e_v: str = "0.8"
    activation_scan_rate_vs: str = "0.2"
    activation_sweep_segments: str = "4"
    activation_sensitivity: str = "1.e-003"

    eis_after_activation_high_frequency_hz: str = "1000000"
    eis_after_activation_low_frequency_hz: str = "0.01"

    cv_high_e_v: str = "0.8"
    cv_scan_rates_mv: list[float] = field(default_factory=lambda: [float(item) for item in CV_SCAN_RATE_OPTIONS_MV])
    cv_sweep_segments: str = "4"
    cv_sensitivity: str = "1.e-003"

    eis_after_cv_rest_minutes: str = "10"
    eis_after_cv_high_frequency_hz: str = "1000000"
    eis_after_cv_low_frequency_hz: str = "0.01"

    gcd_area_cm2: str = "1"
    gcd_current_densities_ma_cm2: list[float] = field(
        default_factory=lambda: [float(item) for item in GCD_CURRENT_DENSITY_OPTIONS_MA_CM2]
    )
    gcd_high_e_limit_v: str = "0.8"
    gcd_number_of_segments: str = "11"
    gcd_data_storage_interval_sec: str = "0.001"

    eis_after_gcd_rest_minutes: str = "10"
    eis_after_gcd_high_frequency_hz: str = "1000000"
    eis_after_gcd_low_frequency_hz: str = "0.01"

    task_order: list[str] = field(default_factory=lambda: list(TASK_BUCKET_ORDER))

    def resolve_save_directory(self) -> Path:
        return Path(self.save_directory)

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WorkflowGuiState":
        default_state = build_default_gui_state()
        if not isinstance(data, dict):
            return default_state

        raw: dict[str, Any] = default_state.to_dict()
        raw.update({key: value for key, value in data.items() if key in raw})
        raw["cv_scan_rates_mv"] = _coerce_float_list(raw.get("cv_scan_rates_mv"), CV_SCAN_RATE_OPTIONS_MV)
        raw["gcd_current_densities_ma_cm2"] = _coerce_float_list(
            raw.get("gcd_current_densities_ma_cm2"),
            GCD_CURRENT_DENSITY_OPTIONS_MA_CM2,
        )
        raw["task_order"] = [
            str(item) for item in raw.get("task_order", TASK_BUCKET_ORDER) if str(item) in TASK_BUCKET_ORDER
        ] or list(TASK_BUCKET_ORDER)
        return cls(**raw)


def build_default_gui_state() -> WorkflowGuiState:
    activation_segment = build_activation_cv_segment(order=1)
    activation_params = activation_segment.params
    eis_config = get_default_eis_front_half_config()
    gcd_config = get_default_gcd_gui_config()

    return WorkflowGuiState(
        activation_high_e_v=str(activation_params["high_potential"]),
        activation_scan_rate_vs=_format_plain_number(activation_params["scan_rate_vs"]),
        activation_sweep_segments=str(activation_params["sweep_segments"]),
        activation_sensitivity=str(activation_params["sensitivity"]),
        eis_after_activation_high_frequency_hz=str(eis_config.high_frequency_hz),
        eis_after_activation_low_frequency_hz=str(eis_config.low_frequency_hz),
        cv_high_e_v=str(activation_params["high_potential"]),
        cv_scan_rates_mv=[float(item) for item in CV_SCAN_RATE_OPTIONS_MV],
        cv_sweep_segments=str(activation_params["sweep_segments"]),
        cv_sensitivity=str(activation_params["sensitivity"]),
        eis_after_cv_rest_minutes=str(int(DEFAULT_REST_DURATION_SEC // 60)),
        eis_after_cv_high_frequency_hz=str(eis_config.high_frequency_hz),
        eis_after_cv_low_frequency_hz=str(eis_config.low_frequency_hz),
        gcd_area_cm2=_format_plain_number(gcd_config.electrode_area_cm2),
        gcd_current_densities_ma_cm2=[float(item) for item in gcd_config.current_density_ma_cm2_list],
        gcd_high_e_limit_v=_format_plain_number(float(gcd_config.high_e_limit_mv) / 1000.0),
        gcd_number_of_segments=str(gcd_config.number_of_segments),
        gcd_data_storage_interval_sec=str(gcd_config.data_storage_interval_sec),
        eis_after_gcd_rest_minutes=str(int(DEFAULT_REST_DURATION_SEC // 60)),
        eis_after_gcd_high_frequency_hz=str(eis_config.high_frequency_hz),
        eis_after_gcd_low_frequency_hz=str(eis_config.low_frequency_hz),
    )

