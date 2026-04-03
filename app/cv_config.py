from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CVFrontHalfConfig:
    high_potential: str = "0.8"
    scan_rate: str = "0.3"
    sweep_segments: str = "4"
    sensitivity: str = "1.e-003"


def get_default_cv_front_half_config() -> CVFrontHalfConfig:
    # TODO: replace these development defaults with validated experiment parameters.
    return CVFrontHalfConfig()
