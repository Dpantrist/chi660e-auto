from __future__ import annotations

"""命名规则层。

这里只负责实验结果文件名生成，不负责流程控制、参数填写或视觉几何。
"""

from decimal import Decimal, InvalidOperation


def _to_decimal(value: float | int | str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for naming rule: {value!r}") from exc


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_activation_cv_filename(scan_rate_vs: float | str) -> str:
    scan_rate_mv = _to_decimal(scan_rate_vs) * Decimal("1000")
    return f"活化 {_format_decimal(scan_rate_mv)}mv.txt"


def build_eis_after_activation_filename() -> str:
    return "EIS-after activation.txt"


def build_cv_filename(scan_rate_mv: int | float | str) -> str:
    return f"cv {_format_decimal(_to_decimal(scan_rate_mv))}mv.txt"


def build_eis_after_cv_filename() -> str:
    return "EIS-after cv.txt"


def build_gcd_filename(current_density_ma_cm2: float | str) -> str:
    return f"gcd {_format_decimal(_to_decimal(current_density_ma_cm2))}ma.txt"


def build_eis_after_gcd_filename() -> str:
    return "EIS-after GCD.txt"
