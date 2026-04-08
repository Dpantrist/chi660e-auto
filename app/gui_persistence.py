from __future__ import annotations

"""GUI 状态持久化。

只负责 GUI state 的 JSON 读写，不承载 workflow 业务逻辑。
"""

import json
from pathlib import Path

from app.gui_models import WorkflowGuiState, build_default_gui_state
from app.paths import CONFIG_DIR


GUI_STATE_FILE = CONFIG_DIR / "gui_state.json"


def load_gui_state(path: Path = GUI_STATE_FILE) -> WorkflowGuiState:
    path.parent.mkdir(parents=True, exist_ok=True)
    default_state = build_default_gui_state()
    if not path.exists():
        return default_state

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_state
    return WorkflowGuiState.from_dict(data)


def save_gui_state(state: WorkflowGuiState, path: Path = GUI_STATE_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path

