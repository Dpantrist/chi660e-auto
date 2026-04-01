from __future__ import annotations

import argparse

from app.bootstrap import main as bootstrap_main
from app.task_runner import run_cv_front_half


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-cv-front-half",
        action="store_true",
        help="Run the minimal CV front-half loop after bootstrap.",
    )
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    if args.run_cv_front_half:
        try:
            run_cv_front_half()
        except Exception:
            print("[ERROR] Execution failed. See logs/app.log and debug/maa.log.")
            raise SystemExit(1)
    else:
        bootstrap_main()
