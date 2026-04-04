from __future__ import annotations

import argparse

from app.bootstrap import (
    format_bootstrap_terminal_error,
    is_main_window_connection_failure,
    main as bootstrap_main,
)
from app.gui_app import launch_workflow_gui
from app.paths import BASE_DIR
from app.task_runner import run_cv_front_half
from app.workflow_runner import run_default_workflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-cv-front-half",
        action="store_true",
        help="Run the minimal CV front-half loop after bootstrap.",
    )
    parser.add_argument(
        "--run-default-workflow",
        action="store_true",
        help="Run the default runnable workflow plan (currently activation CV only).",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the minimal workflow planner GUI.",
    )
    return parser.parse_args()


def _print_runtime_error(exc: Exception) -> None:
    if is_main_window_connection_failure(exc):
        print(format_bootstrap_terminal_error(exc))
        return
    print(f"[ERROR] {exc}")


if __name__ == "__main__":
    args = parse_args()
    if args.gui:
        launch_workflow_gui()
    elif args.run_default_workflow:
        try:
            run_default_workflow(BASE_DIR / "tests" / "test_data")
        except Exception as exc:
            _print_runtime_error(exc)
            raise SystemExit(1)
    elif args.run_cv_front_half:
        try:
            run_cv_front_half()
        except Exception as exc:
            _print_runtime_error(exc)
            raise SystemExit(1)
    else:
        bootstrap_main()
