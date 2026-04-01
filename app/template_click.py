from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.controller_manager import capture_once
from app.errors import Chi660eAutoError
from app.paths import IMAGE_DIR


@dataclass(slots=True)
class TemplateMatchResult:
    template: str
    box: tuple[int, int, int, int] | None
    score: float
    center: tuple[int, int] | None
    success: bool


def _require_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise Chi660eAutoError("opencv-python is required for dynamic template click.") from exc
    return cv2


def load_template_image(template_rel_path: str):
    cv2 = _require_cv2()
    template_path = IMAGE_DIR / Path(template_rel_path)
    image = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if image is None:
        raise Chi660eAutoError(f"Failed to load template image: {template_path}")
    return image


def compute_box_center(box: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, width, height = box
    return int(x + width // 2), int(y + height // 2)


def match_template_on_current_window(
    controller,
    template_rel_path: str,
    threshold: float = 0.8,
) -> TemplateMatchResult:
    cv2 = _require_cv2()
    screenshot = capture_once(controller)
    template = load_template_image(template_rel_path)

    result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    width, height = template.shape[1], template.shape[0]
    box = (int(max_loc[0]), int(max_loc[1]), int(width), int(height))
    center = compute_box_center(box)
    return TemplateMatchResult(
        template=template_rel_path,
        box=box,
        score=float(max_val),
        center=center,
        success=bool(max_val >= threshold),
    )


def click_box_center(
    controller,
    box: tuple[int, int, int, int],
    center_bias: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    center_x, center_y = compute_box_center(box)
    click_x = int(center_x + center_bias[0])
    click_y = int(center_y + center_bias[1])

    try:
        job = controller.post_click(click_x, click_y).wait()
    except Exception as exc:
        raise Chi660eAutoError(
            f"Dynamic template click failed at point ({click_x}, {click_y})."
        ) from exc

    if not getattr(job, "succeeded", False):
        raise Chi660eAutoError(
            f"Dynamic template click was rejected at point ({click_x}, {click_y})."
        )

    return {
        "success": True,
        "center": (center_x, center_y),
        "click_point": (click_x, click_y),
    }


def template_center_click(
    controller,
    template_rel_path: str,
    threshold: float = 0.8,
    center_bias: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    match_result = match_template_on_current_window(controller, template_rel_path, threshold=threshold)
    result: dict[str, Any] = {
        "template": match_result.template,
        "box": match_result.box,
        "score": match_result.score,
        "center": match_result.center,
        "success": match_result.success,
    }
    if not match_result.success or match_result.box is None:
        return result

    click_result = click_box_center(controller, match_result.box, center_bias=center_bias)
    result.update(click_result)
    return result
