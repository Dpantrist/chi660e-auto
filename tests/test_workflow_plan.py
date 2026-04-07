from __future__ import annotations

import unittest

from app.gcd_config import (
    DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2,
    build_gcd_run_values,
    get_default_gcd_front_half_config,
)
from app.gui_controller import build_default_execution_plan_preview, build_default_gui_state, build_segments_from_gui_state
from app.workflow_runner import validate_workflow_segments
from app.workflow_segments import (
    build_default_runnable_segment_plan,
    build_eis_after_activation_segment,
    build_eis_after_cv_segment,
    build_gcd_run_values_for_segment,
    build_gcd_series_item_segment,
    build_output_filename_for_segment,
    segment_is_runnable,
)


class WorkflowPlanTestCase(unittest.TestCase):
    def test_default_runnable_plan_is_valid(self) -> None:
        segments = build_default_runnable_segment_plan()
        self.assertTrue(segments)
        self.assertTrue(all(segment_is_runnable(item) for item in segments))
        self.assertEqual(validate_workflow_segments(segments), [])
        self.assertEqual(segments[0].params["scan_rate_vs"], 0.2)
        self.assertEqual(build_output_filename_for_segment(segments[0]), "活化 200mv.txt")

    def test_eis_after_activation_segment_is_valid(self) -> None:
        segment = build_eis_after_activation_segment(order=1)
        self.assertTrue(segment_is_runnable(segment))
        self.assertEqual(validate_workflow_segments([segment]), [])
        self.assertEqual(segment.params["high_frequency_hz"], "1000000")
        self.assertEqual(segment.params["low_frequency_hz"], "0.01")
        self.assertEqual(build_output_filename_for_segment(segment), "EIS-after activation.txt")

    def test_gcd_series_item_segment_is_valid(self) -> None:
        segment = build_gcd_series_item_segment(
            order=1,
            current_density_ma_cm2=2,
            electrode_area_cm2=1.0,
            high_e_limit_mv=600,
            data_storage_interval_sec="0.05",
            number_of_segments="11",
        )
        self.assertTrue(segment_is_runnable(segment))
        self.assertEqual(validate_workflow_segments([segment]), [])
        self.assertEqual(build_output_filename_for_segment(segment), "gcd 2ma.txt")

        run_values = build_gcd_run_values_for_segment(segment)
        self.assertEqual(run_values["cathodic_current_a_text"], "0.002")
        self.assertEqual(run_values["anodic_current_a_text"], "0.002")
        self.assertEqual(run_values["high_e_limit_v_text"], "0.6")
        self.assertEqual(run_values["data_storage_interval_text"], "0.05")
        self.assertEqual(run_values["number_of_segments_text"], "11")
        self.assertEqual(run_values["density_label_text"], "2")

    def test_gcd_default_config_and_density_candidates(self) -> None:
        config = get_default_gcd_front_half_config()
        self.assertEqual(config.current_density_ma_cm2_list, (2.0,))
        self.assertIn(2.0, DEFAULT_GCD_CURRENT_DENSITY_CANDIDATES_MA_CM2)
        run_values = build_gcd_run_values(config, 2.0)
        self.assertEqual(run_values["cathodic_current_a_text"], "0.002")

    def test_modeled_but_unwired_segment_is_blocked(self) -> None:
        issues = validate_workflow_segments([build_eis_after_cv_segment(order=1)])
        self.assertEqual(len(issues), 1)
        self.assertIn("尚未接通", issues[0]["reason"])

    def test_gui_default_preview_is_not_empty(self) -> None:
        state = build_default_gui_state()
        segments = build_segments_from_gui_state(state)
        preview = build_default_execution_plan_preview()
        self.assertTrue(segments)
        self.assertTrue(preview)


if __name__ == "__main__":
    unittest.main()
