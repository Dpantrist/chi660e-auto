from __future__ import annotations

import unittest

from app.gui_controller import build_default_execution_plan_preview, build_default_gui_state, build_segments_from_gui_state
from app.workflow_runner import validate_workflow_segments
from app.workflow_segments import (
    build_output_filename_for_segment,
    build_default_runnable_segment_plan,
    build_eis_after_activation_segment,
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

    def test_modeled_but_unwired_segment_is_blocked(self) -> None:
        issues = validate_workflow_segments([build_eis_after_activation_segment(order=1)])
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
