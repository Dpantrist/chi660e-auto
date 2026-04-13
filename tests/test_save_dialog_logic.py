from __future__ import annotations

import time
import unittest
import uuid

from app.paths import BASE_DIR
from app.save_dialog import _target_file_ready


class SaveDialogLogicTestCase(unittest.TestCase):
    def test_new_target_file_is_ready_once_created(self) -> None:
        temp_dir = BASE_DIR / "tests" / "test_data"
        target = temp_dir / f"save_dialog_logic_{uuid.uuid4().hex}.txt"
        try:
            target.write_text("new", encoding="utf-8")
            self.assertTrue(_target_file_ready(target, existed_before=False, previous_mtime_ns=None, previous_size=None))
        finally:
            target.unlink(missing_ok=True)

    def test_existing_target_requires_change_signal(self) -> None:
        temp_dir = BASE_DIR / "tests" / "test_data"
        target = temp_dir / f"save_dialog_logic_{uuid.uuid4().hex}.txt"
        try:
            target.write_text("old", encoding="utf-8")
            before_stat = target.stat()

            self.assertFalse(
                _target_file_ready(
                    target,
                    existed_before=True,
                    previous_mtime_ns=before_stat.st_mtime_ns,
                    previous_size=before_stat.st_size,
                )
            )

            time.sleep(0.01)
            target.write_text("old-but-longer", encoding="utf-8")
            after_stat = target.stat()
            self.assertTrue(
                _target_file_ready(
                    target,
                    existed_before=True,
                    previous_mtime_ns=before_stat.st_mtime_ns,
                    previous_size=before_stat.st_size,
                )
            )
            self.assertNotEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
