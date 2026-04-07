from __future__ import annotations

"""最小 GUI 编排模型。

GUI 只维护用户选择与排序结果，不直接承担流程、视觉几何或原子动作逻辑。
"""

from dataclasses import dataclass, field
from pathlib import Path

from app.gcd_config import (
    DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2,
    DEFAULT_GCD_DATA_STORAGE_INTERVAL_SEC,
    DEFAULT_GCD_ELECTRODE_AREA_CM2,
    DEFAULT_GCD_HIGH_E_LIMIT_MV,
    DEFAULT_GCD_NUMBER_OF_SEGMENTS,
)
from app.paths import BASE_DIR
from app.workflow_segments import (
    DEFAULT_ACTIVATION_SCAN_RATE_VS,
    DEFAULT_CV_SCAN_RATE_SERIES_MV,
    DEFAULT_REST_DURATION_SEC,
)


@dataclass(slots=True)
class WorkflowGuiState:
    save_directory: str = str(BASE_DIR / "tests" / "test_data")
    electrode_area_cm2: float = DEFAULT_GCD_ELECTRODE_AREA_CM2
    activation_scan_rate_vs: float = DEFAULT_ACTIVATION_SCAN_RATE_VS
    activation_high_potential: str = "0.8"
    activation_sweep_segments: str = "4"
    activation_sensitivity: str = "1.e-003"
    cv_scan_rates_mv: list[float] = field(
        default_factory=lambda: [float(item) for item in DEFAULT_CV_SCAN_RATE_SERIES_MV]
    )
    gcd_current_densities_ma_cm2: list[float] = field(
        default_factory=lambda: [float(item) for item in DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2]
    )
    gcd_high_e_limit_mv: float = DEFAULT_GCD_HIGH_E_LIMIT_MV
    gcd_data_storage_interval_sec: str = DEFAULT_GCD_DATA_STORAGE_INTERVAL_SEC
    gcd_number_of_segments: str = DEFAULT_GCD_NUMBER_OF_SEGMENTS
    rest_duration_sec: int = DEFAULT_REST_DURATION_SEC
    segment_order: list[str] = field(
        default_factory=lambda: [
            "activation_cv",
            "eis_after_activation",
            "cv_series",
            "rest",
            "eis_after_cv",
            "gcd_series",
            "eis_after_gcd",
        ]
    )
    enable_activation_cv: bool = True
    enable_eis_after_activation: bool = True
    enable_cv_series: bool = True
    enable_rest: bool = True
    enable_eis_after_cv: bool = True
    enable_gcd_series: bool = True
    enable_eis_after_gcd: bool = True

    def resolve_save_directory(self) -> Path:
        return Path(self.save_directory)
