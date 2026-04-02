from __future__ import annotations

from app.template_click import VisualActionMode, VisualActionSpec


VISUAL_ACTION_SPECS: dict[str, VisualActionSpec] = {
    "Main_ClickTechnique": VisualActionSpec(
        name="Main_ClickTechnique",
        template="main/main_btn_technique_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        expected_window_keyword="Electrochemical Techniques",
        timeout_sec=1.5,
        retry_timeout_sec=2.0,
        max_attempts=2,
        allow_pipeline_fallback=True,
    ),
    "Main_ClickParameters": VisualActionSpec(
        name="Main_ClickParameters",
        template="main/main_btn_parameters_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        expected_window_keyword="Cyclic Voltammetry Parameters",
        timeout_sec=1.5,
        retry_timeout_sec=2.5,
        max_attempts=2,
        allow_pipeline_fallback=True,
    ),
    "Techniques_SelectCV_Selected_Check": VisualActionSpec(
        name="Techniques_SelectCV_Selected_Check",
        template="techniques/techniques_item_cv_selected.png",
        mode=VisualActionMode.DETECT_ONLY,
        state_only=True,
        require_full_window_roi=True,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "Techniques_SelectCV_Unselected_Check": VisualActionSpec(
        name="Techniques_SelectCV_Unselected_Check",
        template="techniques/techniques_item_cv_unselected.png",
        mode=VisualActionMode.DETECT_ONLY,
        require_full_window_roi=True,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "Techniques_SelectCV_Unselected_Click": VisualActionSpec(
        name="Techniques_SelectCV_Unselected_Click",
        template="techniques/techniques_item_cv_unselected.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        require_full_window_roi=True,
        max_attempts=1,
        allow_pipeline_fallback=True,
    ),
    "Techniques_ClickOK": VisualActionSpec(
        name="Techniques_ClickOK",
        template="techniques/techniques_btn_ok_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        max_attempts=1,
        allow_pipeline_fallback=True,
    ),
    "CV_FocusHighPotential": VisualActionSpec(
        name="CV_FocusHighPotential",
        template="cv/cv_label_high_potential.png",
        mode=VisualActionMode.WINDOW_FIXED_COLUMN_ROW_CLICK,
        column_x1=286,
        column_x2=444,
        row_top_offset=2,
        row_height=37,
        max_attempts=1,
        allow_pipeline_fallback=True,
    ),
    "CV_FocusScanRate": VisualActionSpec(
        name="CV_FocusScanRate",
        template="cv/cv_label_scan_rate.png",
        mode=VisualActionMode.WINDOW_FIXED_COLUMN_ROW_CLICK,
        column_x1=286,
        column_x2=444,
        row_top_offset=2,
        row_height=37,
        max_attempts=1,
        allow_pipeline_fallback=True,
    ),
    "CV_FocusSweepSegments": VisualActionSpec(
        name="CV_FocusSweepSegments",
        template="cv/cv_label_sweep_segments.png",
        mode=VisualActionMode.WINDOW_FIXED_COLUMN_ROW_CLICK,
        column_x1=286,
        column_x2=444,
        row_top_offset=2,
        row_height=37,
        max_attempts=1,
        allow_pipeline_fallback=True,
    ),
    "CV_ClickOK": VisualActionSpec(
        name="CV_ClickOK",
        template="cv/cv_btn_ok_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        max_attempts=1,
        allow_pipeline_fallback=True,
    ),
}


def get_visual_action_spec(name: str) -> VisualActionSpec:
    try:
        return VISUAL_ACTION_SPECS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown visual action spec: {name}") from exc
