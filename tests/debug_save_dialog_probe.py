from __future__ import annotations

"""Save As 对话框诊断探针。

用途：
1. 用户手动打开“另存为”窗口后运行此脚本。
2. 枚举控件树并输出文件名/保存类型相关控件诊断。
3. 不执行真实保存动作，只做附着与分析。
"""

import ctypes

from app.save_dialog import (
    DialogChildWindow,
    SaveDialogWindow,
    _child_debug_string,
    _class_name,
    _collect_combo_controls,
    _enum_child_windows,
    _find_label_by_keywords,
    _find_nearest_control_to_label,
    _find_type_combo_fallback,
    _read_combo_text,
    _read_control_text,
    _resolved_control_debug_string,
    _visible,
    _window_rect,
    _window_text,
    wait_for_save_as_dialog,
)

USER32 = ctypes.windll.user32

FILE_NAME_LABEL_KEYWORDS = ("文件名", "file name", "filename")
SAVE_TYPE_LABEL_KEYWORDS = ("保存类型", "save as type", "type")


def _parent_hwnd(hwnd: int) -> int:
    return int(USER32.GetParent(ctypes.c_void_p(hwnd)) or 0)


def _emit(title: str, lines: list[str]) -> None:
    print(f"\n[{title}]")
    for line in lines:
        print(line)


def _child_tree_lines(dialog: SaveDialogWindow) -> list[str]:
    lines: list[str] = []
    for child in _enum_child_windows(dialog.hwnd, visible_only=False):
        lines.append(
            "hwnd={hwnd} parent={parent} class={class_name} visible={visible} text={text!r} rect={rect}".format(
                hwnd=child.hwnd,
                parent=_parent_hwnd(child.hwnd),
                class_name=child.class_name,
                visible=_visible(child.hwnd),
                text=child.text,
                rect=child.rect,
            )
        )
    return lines


def _label_lines(dialog: SaveDialogWindow, keywords: tuple[str, ...], label_name: str) -> list[str]:
    label = _find_label_by_keywords(dialog, keywords)
    if label is None:
        return [f"{label_name}: <not found>"]
    return [f"{label_name}: {_child_debug_string(label)}"]


def _resolve_lines(dialog: SaveDialogWindow) -> list[str]:
    filename_control = _find_nearest_control_to_label(dialog, FILE_NAME_LABEL_KEYWORDS, ("Edit",))
    type_control = _find_nearest_control_to_label(dialog, SAVE_TYPE_LABEL_KEYWORDS, ("ComboBox", "ComboBoxEx32"))
    fallback_type = _find_type_combo_fallback(dialog)
    return [
        f"filename label-association -> {_resolved_control_debug_string(filename_control)}",
        f"type label-association -> {_resolved_control_debug_string(type_control)}",
        f"type fallback_combo_guess -> {_resolved_control_debug_string(fallback_type)}",
    ]


def _combo_lines(dialog: SaveDialogWindow) -> list[str]:
    combos = _collect_combo_controls(dialog)
    if not combos:
        return ["<none>"]
    lines: list[str] = []
    for combo in combos:
        lines.append(
            "{base} current={current!r}".format(
                base=_child_debug_string(combo),
                current=_read_combo_text(combo.hwnd),
            )
        )
    return lines


def _edit_lines(dialog: SaveDialogWindow) -> list[str]:
    edits = [
        child
        for child in _enum_child_windows(dialog.hwnd, visible_only=False)
        if child.class_name == "Edit"
    ]
    if not edits:
        return ["<none>"]
    return [
        "{base} current={current!r}".format(
            base=_child_debug_string(edit),
            current=_read_control_text(edit.hwnd),
        )
        for edit in edits
    ]


def main() -> None:
    dialog = wait_for_save_as_dialog(timeout_sec=10.0, logger=None)
    print("[dialog]")
    print(f"title={dialog.title!r} class={dialog.class_name!r} hwnd={dialog.hwnd}")
    print(f"dialog_text={_window_text(dialog.hwnd)!r}")
    print(f"dialog_rect={_window_rect(dialog.hwnd)}")
    print(f"dialog_class={_class_name(dialog.hwnd)!r}")

    _emit("all-controls", _child_tree_lines(dialog))
    _emit("labels", _label_lines(dialog, FILE_NAME_LABEL_KEYWORDS, "filename_label") + _label_lines(dialog, SAVE_TYPE_LABEL_KEYWORDS, "type_label"))
    _emit("resolved-controls", _resolve_lines(dialog))
    _emit("combo-controls", _combo_lines(dialog))
    _emit("edit-controls", _edit_lines(dialog))


if __name__ == "__main__":
    main()
