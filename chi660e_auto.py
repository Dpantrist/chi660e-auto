from __future__ import annotations

import argparse
from collections.abc import Callable

from app.bootstrap import (
    format_bootstrap_terminal_error,
    is_main_window_connection_failure,
    main as bootstrap_main,
)
from app.gcd_config import get_default_gcd_front_half_config
from app.gui_app import launch_workflow_gui
from app.paths import BASE_DIR
from app.task_runner import (
    run_cv_front_half,
    run_eis_front_half,
    run_gcd_front_half,
    run_open_circuit_potential,
)
from app.workflow_runner import run_default_workflow, run_workflow_segments
from app.workflow_segments import build_eis_after_activation_segment, build_gcd_series_item_segment


DEFAULT_SAVE_DIRECTORY = BASE_DIR / "tests" / "test_data"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CHI660E 自动化入口。按用途区分前半圈调试、workflow 执行与 GUI 规划入口。",
    )

    debug_group = parser.add_argument_group("前半圈调试入口")
    debug_group.add_argument(
        "--run-cv-front-half",
        action="store_true",
        help="只跑 CV 前半圈调试：Technique -> CV Parameters -> 填参 -> OK。",
    )
    debug_group.add_argument(
        "--run-eis-front-half",
        action="store_true",
        help="只跑 EIS 前半圈调试：Technique -> A.C. Impedance Parameters -> 填参 -> OK。",
    )
    debug_group.add_argument(
        "--run-gcd-front-half",
        action="store_true",
        help="只跑 GCD 前半圈调试：Technique -> Chronopotentiometry Parameters -> 填参 -> OK。",
    )
    debug_group.add_argument(
        "--run-open-circuit-potential",
        action="store_true",
        help="只跑 Open Circuit Potential 读取调试：Control -> OCP 窗口 -> 读值 -> OK。",
    )

    workflow_group = parser.add_argument_group("Workflow 入口")
    workflow_group.add_argument(
        "--run-default-workflow",
        action="store_true",
        help="运行当前默认 workflow（当前默认计划：activation CV 完整闭环）。",
    )
    workflow_group.add_argument(
        "--run-eis-workflow",
        action="store_true",
        help="运行最小 EIS workflow（当前为单段 eis_after_activation 完整闭环）。",
    )
    workflow_group.add_argument(
        "--run-gcd-workflow",
        action="store_true",
        help="运行最小 GCD workflow（当前为单电流密度 2 mA/cm^2 完整闭环）。",
    )

    gui_group = parser.add_argument_group("GUI 入口")
    gui_group.add_argument(
        "--gui",
        action="store_true",
        help="启动 workflow 规划 GUI。",
    )
    return parser.parse_args(argv)


def _print_runtime_error(exc: Exception) -> None:
    if is_main_window_connection_failure(exc):
        print(format_bootstrap_terminal_error(exc))
        return
    print(f"[ERROR] {exc}")


def _run_with_handled_errors(action: Callable[[], object]) -> int:
    try:
        action()
    except Exception as exc:
        _print_runtime_error(exc)
        return 1
    return 0


def _run_eis_workflow_entry() -> None:
    segments = [build_eis_after_activation_segment(order=1)]
    run_workflow_segments(segments, save_directory=DEFAULT_SAVE_DIRECTORY)


def _run_gcd_workflow_entry() -> None:
    config = get_default_gcd_front_half_config()
    density = float(config.current_density_ma_cm2_list[0])
    segments = [
        build_gcd_series_item_segment(
            order=1,
            current_density_ma_cm2=density,
            electrode_area_cm2=config.electrode_area_cm2,
            high_e_limit_mv=config.high_e_limit_mv,
            data_storage_interval_sec=config.data_storage_interval_sec,
            number_of_segments=config.number_of_segments,
        )
    ]
    run_workflow_segments(segments, save_directory=DEFAULT_SAVE_DIRECTORY)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.gui:
        launch_workflow_gui()
        return 0

    if args.run_default_workflow:
        return _run_with_handled_errors(lambda: run_default_workflow(DEFAULT_SAVE_DIRECTORY))

    if args.run_eis_workflow:
        return _run_with_handled_errors(_run_eis_workflow_entry)

    if args.run_gcd_workflow:
        return _run_with_handled_errors(_run_gcd_workflow_entry)

    if args.run_eis_front_half:
        return _run_with_handled_errors(run_eis_front_half)

    if args.run_gcd_front_half:
        return _run_with_handled_errors(run_gcd_front_half)

    if args.run_open_circuit_potential:
        return _run_with_handled_errors(run_open_circuit_potential)

    if args.run_cv_front_half:
        return _run_with_handled_errors(run_cv_front_half)

    launch_workflow_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
