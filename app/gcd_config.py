from __future__ import annotations

"""GCD front-half business parameter source of truth.

GCD/CP 的业务参数只在这里维护；视觉几何仍在 visual_action_specs，
pipeline JSON 只保留原子 InputText 壳。
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


DEFAULT_GCD_ELECTRODE_AREA_CM2 = 1.0
DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2: tuple[float, ...] = (
    0.1,
    0.25,
    0.5,
    1.0,
    1.5,
    2.0,
    3.0,
    4.0,
    5.0,
    6.0,
    7.0,
    8.0,
    9.0,
    10.0,
)
DEFAULT_GCD_DEBUG_CURRENT_DENSITY_SERIES_MA_CM2: tuple[float, ...] = (2.0,)
DEFAULT_GCD_HIGH_E_LIMIT_MV = 800.0
DEFAULT_GCD_DATA_STORAGE_INTERVAL_SEC = "0.001"
DEFAULT_GCD_NUMBER_OF_SEGMENTS = "11"


@dataclass(slots=True)
class GCDFrontHalfConfig:
    electrode_area_cm2: float = DEFAULT_GCD_ELECTRODE_AREA_CM2
    current_density_ma_cm2_list: tuple[float, ...] = DEFAULT_GCD_DEBUG_CURRENT_DENSITY_SERIES_MA_CM2
    high_e_limit_mv: float = DEFAULT_GCD_HIGH_E_LIMIT_MV
    low_e_limit_v: str = "0"
    data_storage_interval_sec: str = DEFAULT_GCD_DATA_STORAGE_INTERVAL_SEC
    number_of_segments: str = DEFAULT_GCD_NUMBER_OF_SEGMENTS


def get_default_gcd_front_half_config() -> GCDFrontHalfConfig:
    return GCDFrontHalfConfig(
        current_density_ma_cm2_list=DEFAULT_GCD_DEBUG_CURRENT_DENSITY_SERIES_MA_CM2,
    )


def get_default_gcd_gui_config() -> GCDFrontHalfConfig:
    return GCDFrontHalfConfig(
        current_density_ma_cm2_list=DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2,
    )


def _to_decimal(value: float | int | str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid GCD numeric value: {value!r}") from exc


def _format_plain_decimal(value: float | int | str | Decimal) -> str:
    decimal_value = value if isinstance(value, Decimal) else _to_decimal(value)
    text = format(decimal_value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_gcd_run_values(config: GCDFrontHalfConfig, density_ma_cm2: float | int) -> dict[str, str]:
    density_decimal = _to_decimal(density_ma_cm2)
    area_decimal = _to_decimal(config.electrode_area_cm2)
    current_a = area_decimal * density_decimal * Decimal("0.001")
    high_e_limit_v = _to_decimal(config.high_e_limit_mv) / Decimal("1000")

    return {
        "cathodic_current_a_text": _format_plain_decimal(current_a),
        "anodic_current_a_text": _format_plain_decimal(current_a),
        "high_e_limit_v_text": _format_plain_decimal(high_e_limit_v),
        "low_e_limit_v_text": _format_plain_decimal(config.low_e_limit_v),
        "data_storage_interval_text": _format_plain_decimal(config.data_storage_interval_sec),
        "number_of_segments_text": _format_plain_decimal(config.number_of_segments),
        "density_label_text": _format_plain_decimal(density_decimal),
    }
