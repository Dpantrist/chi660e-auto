from __future__ import annotations

import argparse
from collections.abc import Callable

from app.bootstrap import (
    format_bootstrap_terminal_error,
    is_main_window_connection_failure,
    main as bootstrap_main,
)
from app.gui_app import launch_workflow_gui
from app.paths import BASE_DIR
from app.task_runner import run_cv_front_half, run_eis_front_half
from app.workflow_runner import run_default_workflow, run_workflow_segments
from app.workflow_segments import build_eis_after_activation_segment


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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.gui:
        launch_workflow_gui()
        return 0

    if args.run_default_workflow:
        return _run_with_handled_errors(lambda: run_default_workflow(DEFAULT_SAVE_DIRECTORY))

    if args.run_eis_workflow:
        return _run_with_handled_errors(_run_eis_workflow_entry)

    if args.run_eis_front_half:
        return _run_with_handled_errors(run_eis_front_half)

    if args.run_cv_front_half:
        return _run_with_handled_errors(run_cv_front_half)

    bootstrap_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
