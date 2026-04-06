import importlib
import unittest


MODULES = [
    "chi660e_auto",
    "app.constants",
    "app.paths",
    "app.errors",
    "app.dto",
    "app.cv_config",
    "app.eis_config",
    "app.logging_utils",
    "app.window_linker",
    "app.window_preset",
    "app.controller_manager",
    "app.resource_loader",
    "app.template_click",
    "app.visual_action_specs",
    "app.naming_rules",
    "app.save_dialog",
    "app.gui_models",
    "app.gui_controller",
    "app.gui_app",
    "app.workflow_segments",
    "app.post_run_flow",
    "app.workflow_runner",
    "app.task_runner",
    "app.tasker_manager",
    "app.screenshot_manager",
    "app.replay_manager",
    "app.runtime_context",
    "app.bootstrap",
]


class ImportTestCase(unittest.TestCase):
    def test_module_imports(self) -> None:
        for module_name in MODULES:
            self.assertIsNotNone(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
