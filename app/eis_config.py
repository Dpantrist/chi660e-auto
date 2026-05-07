from __future__ import annotations

"""EIS front-half business parameter source of truth.

Only runtime values live here. Visual geometry stays in visual_action_specs,
and pipeline JSON remains atomic InputText shells only.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class EISFrontHalfConfig:
    init_potential_v: str = "0"
    high_frequency_hz: str = "1000000"
    low_frequency_hz: str = "0.01"
    avg_cycles_0p1_to_1hz: str = "1"
    avg_cycles_0p01_to_0p1hz: str = "1"


def get_default_eis_front_half_config() -> EISFrontHalfConfig:
    return EISFrontHalfConfig()
