from __future__ import annotations

"""Visual action primitives and geometry models.

This module is the source of truth for visual-action geometry types. Flow logic
belongs in task_runner, while business values belong in config modules.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

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
    image_shape: tuple[int, ...] | None = None
    actual_roi: tuple[int, int, int, int] | None = None
    roi_mode: str = "full_window"


class VisualActionMode(str, Enum):
    DETECT_ONLY = "detect_only"
    BOX_CENTER_CLICK = "box_center_click"
    RELATIVE_ROI_CENTER_CLICK = "relative_roi_center_click"
    WINDOW_FIXED_COLUMN_ROW_CLICK = "window_fixed_column_row_click"


@dataclass(slots=True)
class VisualActionSpec:
    name: str
    template: str
    threshold: float = 0.8
    mode: VisualActionMode = VisualActionMode.BOX_CENTER_CLICK
    state_only: bool = False
    roi: tuple[int, int, int, int] | None = None
    require_full_window_roi: bool = False
    center_bias: tuple[int, int] = (0, 0)
    relative_roi: tuple[int, int, int, int] | None = None
    column_x1: int | None = None
    column_x2: int | None = None
    row_top_offset: int | None = None
    row_height: int | None = None
    dropdown_option_offsets: dict[str, tuple[int, int]] | None = None
    expected_window_keyword: str | list[str] | None = None
    timeout_sec: float = 1.5
    retry_timeout_sec: float = 2.0
    max_attempts: int = 2
    allow_pipeline_fallback: bool = True


@dataclass(slots=True)
class VisualActionResult:
    success: bool
    matched: bool
    clicked: bool
    box: tuple[int, int, int, int] | None
    score: float
    click_point: tuple[int, int] | None
    computed_center: tuple[int, int] | None
    relative_roi: tuple[int, int, int, int] | None
    computed_rect: tuple[int, int, int, int] | None
    mode: str
    fallback_used: bool
    template: str
    image_shape: tuple[int, ...] | None
    actual_roi: tuple[int, int, int, int] | None
    roi_mode: str
    error: str | None = None
    attempt: int = 1
    wait_result: Any | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


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


def compute_full_window_roi(image_shape: tuple[int, ...] | None) -> tuple[int, int, int, int] | None:
    if image_shape is None or len(image_shape) < 2:
        return None
    return (0, 0, int(image_shape[1]), int(image_shape[0]))


def _normalize_roi(
    roi: tuple[int, int, int, int],
    image_shape: tuple[int, ...] | None,
) -> tuple[int, int, int, int]:
    if image_shape is None or len(image_shape) < 2:
        raise Chi660eAutoError("Image shape is required to normalize ROI.")

    image_height = int(image_shape[0])
    image_width = int(image_shape[1])
    x, y, width, height = (int(value) for value in roi)
    x = max(0, min(x, max(0, image_width - 1)))
    y = max(0, min(y, max(0, image_height - 1)))
    width = max(1, min(width, image_width - x))
    height = max(1, min(height, image_height - y))
    return (x, y, width, height)


def compute_relative_roi_center(
    box: tuple[int, int, int, int],
    relative_roi: tuple[int, int, int, int],
) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    box_x, box_y, _, _ = box
    offset_x, offset_y, width, height = relative_roi
    roi_box = (
        int(box_x + offset_x),
        int(box_y + offset_y),
        int(width),
        int(height),
    )
    return roi_box, compute_box_center(roi_box)


def compute_fixed_column_row_rect(
    box: tuple[int, int, int, int],
    column_x1: int,
    column_x2: int,
    row_top_offset: int,
    row_height: int,
    image_shape: tuple[int, ...] | None = None,
) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    _, box_y, _, _ = box

    rx = int(column_x1)
    ry = int(box_y + row_top_offset)
    rw = max(1, int(column_x2 - column_x1))
    rh = max(1, int(row_height))

    if image_shape is not None and len(image_shape) >= 2:
        image_height, image_width = int(image_shape[0]), int(image_shape[1])
        rx = max(0, min(rx, max(0, image_width - 1)))
        ry = max(0, min(ry, max(0, image_height - 1)))
        max_width = max(1, image_width - rx)
        max_height = max(1, image_height - ry)
        rw = min(rw, max_width)
        rh = min(rh, max_height)

    rect = (rx, ry, rw, rh)
    return rect, compute_box_center(rect)


def match_template_on_current_window(
    controller,
    template_rel_path: str,
    threshold: float = 0.8,
    roi: tuple[int, int, int, int] | None = None,
) -> TemplateMatchResult:
    cv2 = _require_cv2()
    screenshot = capture_once(controller)
    template = load_template_image(template_rel_path)

    image_shape = tuple(screenshot.shape) if hasattr(screenshot, "shape") else None
    full_window_roi = compute_full_window_roi(image_shape)
    roi_mode = "full_window"
    actual_roi = full_window_roi
    match_image = screenshot

    if roi is not None:
        if image_shape is None:
            raise Chi660eAutoError("Screenshot image shape is unavailable for ROI matching.")
        actual_roi = _normalize_roi(roi, image_shape)
        roi_mode = "cropped"
        roi_x, roi_y, roi_width, roi_height = actual_roi
        match_image = screenshot[roi_y : roi_y + roi_height, roi_x : roi_x + roi_width]

    result = cv2.matchTemplate(match_image, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    width, height = template.shape[1], template.shape[0]
    box_x = int(max_loc[0])
    box_y = int(max_loc[1])
    if actual_roi is not None:
        box_x += int(actual_roi[0])
        box_y += int(actual_roi[1])
    box = (box_x, box_y, int(width), int(height))
    center = compute_box_center(box)
    return TemplateMatchResult(
        template=template_rel_path,
        box=box,
        score=float(max_val),
        center=center,
        success=bool(max_val >= threshold),
        image_shape=image_shape,
        actual_roi=actual_roi,
        roi_mode=roi_mode,
    )


def click_box_center(
    controller,
    box: tuple[int, int, int, int],
    center_bias: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    center_x, center_y = compute_box_center(box)
    return click_point(controller, center_x + center_bias[0], center_y + center_bias[1], (center_x, center_y))


def click_point(
    controller,
    x: int,
    y: int,
    computed_center: tuple[int, int] | None = None,
) -> dict[str, Any]:
    click_x = int(x)
    click_y = int(y)

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
        "center": computed_center,
        "click_point": (click_x, click_y),
    }


def run_visual_action(
    controller,
    spec: VisualActionSpec,
    logger=None,
    perform_click: bool = True,
) -> VisualActionResult:
    match_result = match_template_on_current_window(
        controller,
        spec.template,
        threshold=spec.threshold,
        roi=spec.roi,
    )
    result = VisualActionResult(
        success=False,
        matched=match_result.success,
        clicked=False,
        box=match_result.box,
        score=match_result.score,
        click_point=None,
        computed_center=match_result.center,
        relative_roi=None,
        computed_rect=None,
        mode=spec.mode.value,
        fallback_used=False,
        template=spec.template,
        image_shape=match_result.image_shape,
        actual_roi=match_result.actual_roi,
        roi_mode=match_result.roi_mode,
    )

    if not match_result.success or match_result.box is None:
        return result

    if spec.mode == VisualActionMode.DETECT_ONLY:
        result.success = True
        return result

    try:
        click_result: dict[str, Any] | None = None

        if spec.mode == VisualActionMode.BOX_CENTER_CLICK:
            target_point = (
                match_result.center[0] + spec.center_bias[0],
                match_result.center[1] + spec.center_bias[1],
            )
            if perform_click:
                click_result = click_box_center(
                    controller,
                    match_result.box,
                    center_bias=spec.center_bias,
                )
            else:
                result.click_point = target_point
        elif spec.mode == VisualActionMode.RELATIVE_ROI_CENTER_CLICK:
            if spec.relative_roi is None:
                raise Chi660eAutoError(
                    f"relative_roi is required for visual action {spec.name!r}."
                )
            roi_box, roi_center = compute_relative_roi_center(match_result.box, spec.relative_roi)
            target_point = (
                roi_center[0] + spec.center_bias[0],
                roi_center[1] + spec.center_bias[1],
            )
            if perform_click:
                click_result = click_point(
                    controller,
                    target_point[0],
                    target_point[1],
                    roi_center,
                )
            else:
                result.click_point = target_point
            result.relative_roi = roi_box
            result.computed_rect = roi_box
            result.computed_center = roi_center
        elif spec.mode == VisualActionMode.WINDOW_FIXED_COLUMN_ROW_CLICK:
            if None in (spec.column_x1, spec.column_x2, spec.row_top_offset, spec.row_height):
                raise Chi660eAutoError(
                    f"Fixed column row parameters are required for visual action {spec.name!r}."
                )
            row_rect, row_center = compute_fixed_column_row_rect(
                match_result.box,
                spec.column_x1,
                spec.column_x2,
                spec.row_top_offset,
                spec.row_height,
                image_shape=match_result.image_shape,
            )
            target_point = (
                row_center[0] + spec.center_bias[0],
                row_center[1] + spec.center_bias[1],
            )
            if perform_click:
                click_result = click_point(
                    controller,
                    target_point[0],
                    target_point[1],
                    row_center,
                )
            else:
                result.click_point = target_point
            result.computed_rect = row_rect
            result.computed_center = row_center
        else:
            raise Chi660eAutoError(f"Unsupported visual action mode: {spec.mode!r}")
    except Exception as exc:
        result.error = str(exc)
        if logger is not None:
            logger.debug(
                "Visual action click error: name=%s template=%s error=%s",
                spec.name,
                spec.template,
                exc,
            )
        return result

    result.success = True
    if click_result is not None:
        result.clicked = True
        result.click_point = click_result.get("click_point")
        if click_result.get("center") is not None:
            result.computed_center = click_result["center"]
    return result


def run_visual_action_expect_window(
    controller,
    spec: VisualActionSpec,
    wait_for_window: Callable[[float], Any] | None = None,
    before_attempt: Callable[[int], None] | None = None,
    logger=None,
) -> VisualActionResult:
    attempts = max(1, spec.max_attempts)
    last_result = VisualActionResult(
        success=False,
        matched=False,
        clicked=False,
        box=None,
        score=0.0,
        click_point=None,
        computed_center=None,
        relative_roi=None,
        computed_rect=None,
        mode=spec.mode.value,
        fallback_used=False,
        template=spec.template,
        image_shape=None,
        actual_roi=None,
        roi_mode="full_window",
        error="visual_action_not_run",
    )

    for attempt in range(1, attempts + 1):
        if before_attempt is not None:
            before_attempt(attempt)

        result = run_visual_action(controller, spec, logger=logger)
        result.attempt = attempt
        result.history.append(
            {
                "attempt": attempt,
                "matched": result.matched,
                "clicked": result.clicked,
                "box": result.box,
                "score": result.score,
                "click_point": result.click_point,
                "computed_center": result.computed_center,
                "relative_roi": result.relative_roi,
                "computed_rect": result.computed_rect,
                "image_shape": result.image_shape,
                "actual_roi": result.actual_roi,
                "roi_mode": result.roi_mode,
                "error": result.error,
            }
        )
        last_result = result

        if not result.success or spec.mode == VisualActionMode.DETECT_ONLY:
            if result.success or attempt >= attempts:
                return result
            continue

        if spec.expected_window_keyword is None or wait_for_window is None:
            return result

        timeout = spec.timeout_sec if attempt == 1 else spec.retry_timeout_sec
        try:
            result.wait_result = wait_for_window(timeout)
            return result
        except Exception as exc:
            result.error = str(exc)
            if logger is not None:
                logger.debug(
                    "Visual action expected window wait failed: name=%s attempt=%s error=%s",
                    spec.name,
                    attempt,
                    exc,
                )
            last_result = result

    return last_result


def template_center_click(
    controller,
    template_rel_path: str,
    threshold: float = 0.8,
    center_bias: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    spec = VisualActionSpec(
        name=template_rel_path,
        template=template_rel_path,
        threshold=threshold,
        mode=VisualActionMode.BOX_CENTER_CLICK,
        center_bias=center_bias,
        allow_pipeline_fallback=False,
    )
    result = run_visual_action(controller, spec)
    return {
        "template": result.template,
        "box": result.box,
        "score": result.score,
        "center": result.computed_center,
        "click_point": result.click_point,
        "success": result.success,
        "matched": result.matched,
    }
