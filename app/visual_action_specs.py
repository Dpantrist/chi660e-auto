from __future__ import annotations

"""Visual action declaration layer.

Templates, geometry, state-only rules, and dropdown offsets live here. Flow
ordering and business parameter values must not be duplicated here.
"""

from app.template_click import VisualActionMode, VisualActionSpec


VISUAL_ACTION_SPECS: dict[str, VisualActionSpec] = {
    # Main-window button geometry is declared here. 10_common_main.json remains
    # a fallback click shell only and must not carry primary click offsets.
    "Main_CheckPauseDisabled": VisualActionSpec(
        name="Main_CheckPauseDisabled",
        template="main/main_btn_pause_disabled.png",
        threshold=0.9,
        mode=VisualActionMode.DETECT_ONLY,
        allow_pipeline_fallback=False,
    ),
    "Main_CheckPauseUsable": VisualActionSpec(
        name="Main_CheckPauseUsable",
        template="main/main_btn_pause_usable.png",
        threshold=0.9,
        mode=VisualActionMode.DETECT_ONLY,
        allow_pipeline_fallback=False,
    ),
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
    "Main_ClickParametersEIS": VisualActionSpec(
        name="Main_ClickParametersEIS",
        template="main/main_btn_parameters_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        expected_window_keyword="A.C. Impedance Parameters",
        timeout_sec=1.5,
        retry_timeout_sec=2.5,
        max_attempts=2,
        allow_pipeline_fallback=True,
    ),
    "Main_ClickControl": VisualActionSpec(
        name="Main_ClickControl",
        template="main/main_btn_control_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "Main_ClickRun": VisualActionSpec(
        name="Main_ClickRun",
        template="main/main_btn_run_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "Main_ClickSaveAs": VisualActionSpec(
        name="Main_ClickSaveAs",
        template="main/main_btn_save_as_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    # All actions below use Python/spec as the primary path and do not fall back
    # to legacy pipeline flow definitions.
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
        allow_pipeline_fallback=False,
    ),
    "Techniques_SelectEIS_Selected_Check": VisualActionSpec(
        name="Techniques_SelectEIS_Selected_Check",
        template="techniques/techniques_item_eis_selected.png",
        mode=VisualActionMode.DETECT_ONLY,
        state_only=True,
        require_full_window_roi=True,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "Techniques_SelectEIS_Unselected_Check": VisualActionSpec(
        name="Techniques_SelectEIS_Unselected_Check",
        template="techniques/techniques_item_eis_unselected.png",
        mode=VisualActionMode.DETECT_ONLY,
        require_full_window_roi=True,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "Techniques_SelectEIS_Unselected_Click": VisualActionSpec(
        name="Techniques_SelectEIS_Unselected_Click",
        template="techniques/techniques_item_eis_unselected.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        require_full_window_roi=True,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "Techniques_ClickOK": VisualActionSpec(
        name="Techniques_ClickOK",
        template="techniques/techniques_btn_ok_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        max_attempts=1,
        allow_pipeline_fallback=False,
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
        allow_pipeline_fallback=False,
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
        allow_pipeline_fallback=False,
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
        allow_pipeline_fallback=False,
    ),
    "CV_FocusSensitivity": VisualActionSpec(
        name="CV_FocusSensitivity",
        template="cv/cv_label_sensitivity.png",
        mode=VisualActionMode.WINDOW_FIXED_COLUMN_ROW_CLICK,
        column_x1=286,
        column_x2=444,
        row_top_offset=2,
        row_height=37,
        dropdown_option_offsets={
            "1.e-003": (0, 108),
        },
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "CV_ClickOK": VisualActionSpec(
        name="CV_ClickOK",
        template="cv/cv_btn_ok_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "EIS_FocusHighFrequency": VisualActionSpec(
        name="EIS_FocusHighFrequency",
        template="eis/eis_label_high_frequency.png",
        mode=VisualActionMode.WINDOW_FIXED_COLUMN_ROW_CLICK,
        column_x1=286,
        column_x2=444,
        row_top_offset=2,
        row_height=37,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "EIS_FocusInitPotential": VisualActionSpec(
        name="EIS_FocusInitPotential",
        template="eis/eis_label_init_potential.png",
        mode=VisualActionMode.WINDOW_FIXED_COLUMN_ROW_CLICK,
        column_x1=286,
        column_x2=444,
        row_top_offset=2,
        row_height=37,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "EIS_FocusLowFrequency": VisualActionSpec(
        name="EIS_FocusLowFrequency",
        template="eis/eis_label_low_frequency.png",
        mode=VisualActionMode.WINDOW_FIXED_COLUMN_ROW_CLICK,
        column_x1=286,
        column_x2=444,
        row_top_offset=2,
        row_height=37,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
    "EIS_ClickOK": VisualActionSpec(
        name="EIS_ClickOK",
        template="eis/eis_btn_ok_usable.png",
        mode=VisualActionMode.BOX_CENTER_CLICK,
        max_attempts=1,
        allow_pipeline_fallback=False,
    ),
}


def get_visual_action_spec(name: str) -> VisualActionSpec:
    try:
        return VISUAL_ACTION_SPECS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown visual action spec: {name}") from exc
