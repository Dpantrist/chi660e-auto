from __future__ import annotations

from pathlib import Path
from typing import Any

from app.paths import DEBUG_LATEST_DIR, ensure_project_dirs


def _coerce_array(image: Any):
    try:
        import numpy as np
    except ImportError:
        return image

    try:
        return np.asarray(image)
    except Exception:
        return image


def _write_note(path: Path, message: str) -> None:
    path.write_text(message + "\n", encoding="utf-8")


def _save_capture(image: Any, target_dir: Path, name: str) -> Path | None:
    ensure_project_dirs()
    target_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(name).stem
    png_path = target_dir / f"{stem}.png"
    array = _coerce_array(image)

    try:
        import cv2
    except ImportError:
        cv2 = None

    if cv2 is not None:
        try:
            if cv2.imwrite(str(png_path), array):
                return png_path
        except Exception as exc:
            _write_note(
                target_dir / f"{stem}.txt",
                f"cv2.imwrite failed while saving capture: {exc}",
            )

    try:
        import numpy as np
    except ImportError:
        np = None

    if np is not None:
        npy_path = target_dir / f"{stem}.npy"
        try:
            np.save(npy_path, array)
            _write_note(
                target_dir / f"{stem}.txt",
                f"opencv-python is unavailable; numpy fallback saved to: {npy_path.name}",
            )
            return npy_path
        except Exception as exc:
            _write_note(
                target_dir / f"{stem}.txt",
                f"Failed to persist capture with numpy fallback: {exc}",
            )
            return None

    _write_note(
        target_dir / f"{stem}.txt",
        "Capture was not persisted because neither cv2 nor numpy save fallback succeeded.",
    )
    return None


def save_debug_capture(image: Any, name: str) -> Path | None:
    return _save_capture(image, DEBUG_LATEST_DIR, name)


def save_replay_capture(image: Any, run_dir: Path, name: str) -> Path | None:
    return _save_capture(image, Path(run_dir) / "screenshots", name)
