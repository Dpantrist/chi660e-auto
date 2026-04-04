from __future__ import annotations

"""系统“另存为”对话框处理层。

这里专门处理标准 Windows 文件对话框，不使用 TemplateMatch 作为主路径。
主保存路径采用“控件定位 + 写入后读回校验”，快捷键仅作为末级 fallback。
"""

import ctypes
import ctypes.wintypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.errors import Chi660eAutoError, WindowNotFoundError
from app.window_linker import list_desktop_windows

USER32 = ctypes.windll.user32

SW_RESTORE = 9
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
BM_CLICK = 0x00F5
CB_SELECTSTRING = 0x014D
CB_GETCOUNT = 0x0146
CB_GETCURSEL = 0x0147
CB_GETLBTEXT = 0x0148
CB_GETLBTEXTLEN = 0x0149
CB_SETCURSEL = 0x014E
CB_SHOWDROPDOWN = 0x014F
WM_COMMAND = 0x0111
CBN_CLOSEUP = 8
CBN_SELCHANGE = 1
VK_RETURN = 0x0D
VK_MENU = 0x12
VK_CONTROL = 0x11
VK_HOME = 0x24
VK_UP = 0x26
VK_DOWN = 0x28
VK_D = 0x44
VK_L = 0x4C
VK_N = 0x4E
VK_T = 0x54
VK_A = 0x41
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

SAVE_DIALOG_STEP_INTERVAL_SEC = 1.0

FILE_NAME_LABEL_KEYWORDS = ("文件名", "file name", "filename")
SAVE_TYPE_LABEL_KEYWORDS = ("保存类型", "save as type", "type")
OVERWRITE_KEYWORDS = (
    "已存在",
    "替换",
    "覆盖",
    "确认另存为",
    "确认保存",
    "already exists",
    "replace",
    "overwrite",
)
OVERWRITE_CONFIRM_BUTTON_KEYWORDS = (
    "替换",
    "是",
    "yes",
    "保存",
    "确定",
    "ok",
)
OVERWRITE_NEGATIVE_BUTTON_KEYWORDS = (
    "否",
    "no",
    "cancel",
    "取消",
)

_LAST_DIALOG_STEP_AT = 0.0


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hwndActive", ctypes.wintypes.HWND),
        ("hwndFocus", ctypes.wintypes.HWND),
        ("hwndCapture", ctypes.wintypes.HWND),
        ("hwndMenuOwner", ctypes.wintypes.HWND),
        ("hwndMoveSize", ctypes.wintypes.HWND),
        ("hwndCaret", ctypes.wintypes.HWND),
        ("rcCaret", RECT),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT),
    ]


@dataclass(slots=True)
class SaveDialogWindow:
    hwnd: int
    title: str
    class_name: str


@dataclass(slots=True)
class DialogChildWindow:
    hwnd: int
    class_name: str
    text: str
    rect: tuple[int, int, int, int]


@dataclass(slots=True)
class ResolvedDialogControl:
    hwnd: int
    class_name: str
    method: str
    text: str
    rect: tuple[int, int, int, int]


def _mark_dialog_step() -> None:
    global _LAST_DIALOG_STEP_AT
    _LAST_DIALOG_STEP_AT = time.monotonic()


def _dialog_step_gap(step_name: str, min_interval_sec: float = SAVE_DIALOG_STEP_INTERVAL_SEC, logger=None) -> None:
    global _LAST_DIALOG_STEP_AT
    now = time.monotonic()
    if _LAST_DIALOG_STEP_AT <= 0:
        _LAST_DIALOG_STEP_AT = now
        return

    elapsed = now - _LAST_DIALOG_STEP_AT
    sleep_sec = max(0.0, float(min_interval_sec) - elapsed)
    if sleep_sec > 0:
        if logger is not None:
            logger.info("Save dialog step gap enforced: step=%s sleep=%.3fs", step_name, sleep_sec)
        time.sleep(sleep_sec)
    _LAST_DIALOG_STEP_AT = time.monotonic()


def _is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(USER32.IsWindow(ctypes.c_void_p(hwnd)))


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    USER32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    return (
        int(rect.left),
        int(rect.top),
        int(rect.right - rect.left),
        int(rect.bottom - rect.top),
    )


def _message_text(hwnd: int) -> str:
    length = int(USER32.SendMessageW(ctypes.c_void_p(hwnd), WM_GETTEXTLENGTH, 0, 0))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    USER32.SendMessageW(ctypes.c_void_p(hwnd), WM_GETTEXT, len(buffer), buffer)
    return buffer.value


def _window_text(hwnd: int) -> str:
    length = USER32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
    if length > 0:
        buffer = ctypes.create_unicode_buffer(length + 1)
        USER32.GetWindowTextW(ctypes.c_void_p(hwnd), buffer, len(buffer))
        if buffer.value:
            return buffer.value
    return _message_text(hwnd)


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(ctypes.c_void_p(hwnd), buffer, len(buffer))
    return buffer.value


def _visible(hwnd: int) -> bool:
    return bool(USER32.IsWindowVisible(ctypes.c_void_p(hwnd)))


def _set_foreground_window(hwnd: int) -> None:
    USER32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
    USER32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    USER32.SetActiveWindow(ctypes.c_void_p(hwnd))
    time.sleep(0.1)


def _click_screen_point(x: int, y: int) -> None:
    USER32.SetCursorPos(int(x), int(y))
    USER32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    USER32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _click_window_center(hwnd: int) -> None:
    left, top, width, height = _window_rect(hwnd)
    _click_screen_point(left + max(1, width // 2), top + max(1, height // 2))


def _tap_vk(vk_code: int) -> None:
    USER32.keybd_event(vk_code, 0, 0, 0)
    USER32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


def _press_modified_key(modifier_vk: int, key_vk: int) -> None:
    USER32.keybd_event(modifier_vk, 0, 0, 0)
    time.sleep(0.03)
    _tap_vk(key_vk)
    time.sleep(0.03)
    USER32.keybd_event(modifier_vk, 0, KEYEVENTF_KEYUP, 0)


def _send_unicode_text(text: str) -> None:
    for char in text:
        code_point = ord(char)
        press = INPUT(type=1, ki=KEYBDINPUT(0, code_point, 0x0004, 0, None))
        release = INPUT(type=1, ki=KEYBDINPUT(0, code_point, 0x0004 | KEYEVENTF_KEYUP, 0, None))
        USER32.SendInput(1, ctypes.byref(press), ctypes.sizeof(INPUT))
        USER32.SendInput(1, ctypes.byref(release), ctypes.sizeof(INPUT))


def _enum_child_windows(hwnd: int, *, visible_only: bool = True) -> list[DialogChildWindow]:
    children: list[DialogChildWindow] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(child_hwnd, _lparam):
        child = int(child_hwnd)
        if visible_only and not _visible(child):
            return True
        children.append(
            DialogChildWindow(
                hwnd=child,
                class_name=_class_name(child),
                text=_window_text(child),
                rect=_window_rect(child),
            )
        )
        return True

    USER32.EnumChildWindows(ctypes.c_void_p(hwnd), callback, 0)
    return children


def _pick_bottom_most(children: Iterable[DialogChildWindow]) -> DialogChildWindow | None:
    sorted_children = sorted(children, key=lambda item: (item.rect[1], item.rect[0], item.rect[2]), reverse=True)
    return sorted_children[0] if sorted_children else None


def _rect_center_y(rect: tuple[int, int, int, int]) -> float:
    return rect[1] + rect[3] / 2.0


def _normalize_label_text(text: str) -> str:
    normalized = text.lower()
    for token in ("&", "：", ":", "(", ")", " ", "\t", "\r", "\n"):
        normalized = normalized.replace(token, "")
    return normalized


def _find_label_by_keywords(dialog: SaveDialogWindow, keywords: tuple[str, ...]) -> DialogChildWindow | None:
    normalized_keywords = tuple(_normalize_label_text(item) for item in keywords)
    labels = [
        child
        for child in _enum_child_windows(dialog.hwnd, visible_only=False)
        if child.class_name in {"Static", "DirectUIHWND"} and child.text
    ]
    for label in labels:
        normalized = _normalize_label_text(label.text)
        if any(keyword in normalized for keyword in normalized_keywords):
            return label
    return None


def _get_focused_child_hwnd(dialog_hwnd: int) -> int | None:
    thread_id = USER32.GetWindowThreadProcessId(ctypes.c_void_p(dialog_hwnd), None)
    if not thread_id:
        return None

    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not USER32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return None
    focused = int(info.hwndFocus or 0)
    return focused or None


def _read_control_text(hwnd: int) -> str:
    text = _message_text(hwnd)
    if text:
        return text
    return _window_text(hwnd)


def _resolved_control_debug_string(control: ResolvedDialogControl | None) -> str:
    if control is None:
        return "<none>"
    return (
        f"hwnd={control.hwnd} class={control.class_name} "
        f"text={control.text!r} rect={control.rect} method={control.method}"
    )


def _child_debug_string(child: DialogChildWindow) -> str:
    return f"hwnd={child.hwnd} class={child.class_name} text={child.text!r} rect={child.rect}"


def _collect_combo_controls(dialog: SaveDialogWindow) -> list[DialogChildWindow]:
    return [
        child
        for child in _enum_child_windows(dialog.hwnd, visible_only=False)
        if child.class_name in {"ComboBox", "ComboBoxEx32"}
    ]


def _log_combo_control_candidates(dialog: SaveDialogWindow, logger=None) -> None:
    if logger is None:
        return
    combos = _collect_combo_controls(dialog)
    if not combos:
        logger.info("Save As combo candidates: <none>")
        return
    for index, combo in enumerate(combos, start=1):
        logger.info("Save As combo candidate[%s]: %s", index, _child_debug_string(combo))


def _get_focus_debug_string(dialog_hwnd: int) -> str:
    focused_hwnd = _get_focused_child_hwnd(dialog_hwnd)
    if not focused_hwnd:
        return "<none>"
    return (
        f"hwnd={focused_hwnd} class={_class_name(focused_hwnd)} "
        f"text={_read_control_text(focused_hwnd)!r} rect={_window_rect(focused_hwnd)}"
    )


def _control_sort_key_by_label(label: DialogChildWindow, child: DialogChildWindow) -> tuple[float, int, int]:
    vertical_delta = abs(_rect_center_y(child.rect) - _rect_center_y(label.rect))
    horizontal_delta = max(0, child.rect[0] - (label.rect[0] + label.rect[2]))
    width_rank = -child.rect[2]
    return (vertical_delta, horizontal_delta, width_rank)


def _find_nearest_control_to_label(
    dialog: SaveDialogWindow,
    label_keywords: tuple[str, ...],
    control_classes: tuple[str, ...],
) -> ResolvedDialogControl | None:
    label = _find_label_by_keywords(dialog, label_keywords)
    if label is None:
        return None

    controls = [
        child
        for child in _enum_child_windows(dialog.hwnd, visible_only=False)
        if child.class_name in control_classes and child.hwnd != label.hwnd
    ]
    if not controls:
        return None

    controls.sort(key=lambda item: _control_sort_key_by_label(label, item))
    selected = controls[0]
    return ResolvedDialogControl(
        hwnd=selected.hwnd,
        class_name=selected.class_name,
        method="label_association",
        text=selected.text,
        rect=selected.rect,
    )


def _find_filename_edit_fallback(dialog: SaveDialogWindow) -> ResolvedDialogControl | None:
    edits = [
        child
        for child in _enum_child_windows(dialog.hwnd, visible_only=False)
        if child.class_name == "Edit" and child.rect[2] >= 80
    ]
    focused_hwnd = _get_focused_child_hwnd(dialog.hwnd)
    edits.sort(key=lambda item: (item.rect[1], item.rect[2], item.rect[0]), reverse=True)
    if focused_hwnd:
        edits.sort(key=lambda item: item.hwnd == focused_hwnd, reverse=True)
    if not edits:
        return None

    selected = edits[0]
    return ResolvedDialogControl(
        hwnd=selected.hwnd,
        class_name=selected.class_name,
        method="fallback_edit_guess",
        text=selected.text,
        rect=selected.rect,
    )


def _resolve_edit_from_control(control: ResolvedDialogControl) -> ResolvedDialogControl:
    if control.class_name == "Edit":
        return control

    edit_children = [
        child
        for child in _enum_child_windows(control.hwnd, visible_only=False)
        if child.class_name == "Edit"
    ]
    if not edit_children:
        return control

    edit_children.sort(key=lambda item: (item.rect[2], -item.rect[1], -item.rect[0]), reverse=True)
    selected = edit_children[0]
    return ResolvedDialogControl(
        hwnd=selected.hwnd,
        class_name=selected.class_name,
        method=f"{control.method}+child_edit",
        text=selected.text,
        rect=selected.rect,
    )


def _resolve_filename_control(dialog: SaveDialogWindow, logger=None) -> ResolvedDialogControl:
    control = _find_nearest_control_to_label(dialog, FILE_NAME_LABEL_KEYWORDS, ("Edit", "ComboBoxEx32", "ComboBox"))
    if control is not None:
        control = _resolve_edit_from_control(control)
        if logger is not None:
            logger.info(
                "Save As filename control resolved: hwnd=%s class=%s method=%s",
                control.hwnd,
                control.class_name,
                control.method,
            )
        return control

    fallback = _find_filename_edit_fallback(dialog)
    if fallback is not None:
        if logger is not None:
            logger.info(
                "Save As filename control resolved by fallback: hwnd=%s class=%s method=%s",
                fallback.hwnd,
                fallback.class_name,
                fallback.method,
            )
        return fallback

    raise Chi660eAutoError("Failed to resolve Save As filename control.")


def _find_directory_edit_guess(dialog: SaveDialogWindow) -> ResolvedDialogControl | None:
    dialog_top = dialog_rect_top = _window_rect(dialog.hwnd)[1]
    dialog_height = _window_rect(dialog.hwnd)[3]
    candidates: list[ResolvedDialogControl] = []
    for child in _enum_child_windows(dialog.hwnd, visible_only=False):
        if child.class_name not in {"Edit", "ComboBoxEx32", "ComboBox"}:
            continue
        # 地址栏通常位于对话框上半部分，且宽度较大。
        if child.rect[2] < 150:
            continue
        if child.rect[1] > dialog_rect_top + int(dialog_height * 0.55):
            continue
        resolved = _resolve_edit_from_control(
            ResolvedDialogControl(
                hwnd=child.hwnd,
                class_name=child.class_name,
                method="control_tree_guess",
                text=child.text,
                rect=child.rect,
            )
        )
        if resolved.class_name != "Edit":
            continue
        candidates.append(resolved)

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item.rect[1], -item.rect[2], item.rect[0]))
    return candidates[0]


def _resolve_directory_control(dialog: SaveDialogWindow, logger=None) -> ResolvedDialogControl:
    direct = _find_directory_edit_guess(dialog)
    if direct is not None:
        if logger is not None:
            logger.info(
                "Save As directory control resolved: hwnd=%s class=%s method=%s",
                direct.hwnd,
                direct.class_name,
                direct.method,
            )
        return direct

    _set_foreground_window(dialog.hwnd)
    for shortcut_name, modifier, key in (
        ("alt_d_focus", VK_MENU, VK_D),
        ("ctrl_l_focus", VK_CONTROL, VK_L),
    ):
        _press_modified_key(modifier, key)
        time.sleep(0.05)
        focused_hwnd = _get_focused_child_hwnd(dialog.hwnd)
        if focused_hwnd and _class_name(focused_hwnd) == "Edit":
            control = ResolvedDialogControl(
                hwnd=focused_hwnd,
                class_name="Edit",
                method=shortcut_name,
                text=_read_control_text(focused_hwnd),
                rect=_window_rect(focused_hwnd),
            )
            if logger is not None:
                logger.info(
                    "Save As directory control resolved: hwnd=%s class=%s method=%s",
                    control.hwnd,
                    control.class_name,
                    control.method,
                )
            return control

    raise Chi660eAutoError("Failed to resolve Save As directory control.")


def _find_type_combo_fallback(dialog: SaveDialogWindow) -> ResolvedDialogControl | None:
    combos = [
        child
        for child in _enum_child_windows(dialog.hwnd, visible_only=False)
        if child.class_name in {"ComboBox", "ComboBoxEx32"}
    ]
    if not combos:
        return None

    preferred_keywords = (".bin", "*.bin", "data files", "文件")
    preferred: list[DialogChildWindow] = []
    for combo in combos:
        combo_text = (_read_combo_text(combo.hwnd) or combo.text).lower()
        if any(keyword in combo_text for keyword in preferred_keywords):
            preferred.append(combo)

    used_preferred = bool(preferred)
    selected = _pick_bottom_most(preferred or combos)
    if selected is None:
        return None
    return ResolvedDialogControl(
        hwnd=selected.hwnd,
        class_name=selected.class_name,
        method="fallback_combo_by_text" if used_preferred else "fallback_combo_guess",
        text=selected.text,
        rect=selected.rect,
    )


def _resolve_type_control(dialog: SaveDialogWindow, logger=None) -> ResolvedDialogControl:
    label = _find_label_by_keywords(dialog, SAVE_TYPE_LABEL_KEYWORDS)
    if logger is not None:
        if label is None:
            logger.info("Save As type label association missed: label_not_found keywords=%s", SAVE_TYPE_LABEL_KEYWORDS)
        else:
            logger.info("Save As type label matched: %s", _child_debug_string(label))

    control = _find_nearest_control_to_label(dialog, SAVE_TYPE_LABEL_KEYWORDS, ("ComboBox", "ComboBoxEx32"))
    if control is not None:
        if logger is not None:
            logger.info(
                "Save As type control resolved: %s",
                _resolved_control_debug_string(control),
            )
        return control

    fallback = _find_type_combo_fallback(dialog)
    if fallback is not None:
        if logger is not None:
            logger.info(
                "Save As type control resolved by fallback: %s",
                _resolved_control_debug_string(fallback),
            )
        return fallback

    raise Chi660eAutoError("Failed to resolve Save As type control.")


def _combo_message_hwnd(combo_hwnd: int) -> int:
    if _class_name(combo_hwnd) != "ComboBoxEx32":
        return combo_hwnd
    children = _enum_child_windows(combo_hwnd, visible_only=False)
    for child in children:
        if child.class_name == "ComboBox":
            return child.hwnd
    return combo_hwnd


def _read_combo_text(combo_hwnd: int) -> str:
    message_hwnd = _combo_message_hwnd(combo_hwnd)
    selected_index = int(USER32.SendMessageW(ctypes.c_void_p(message_hwnd), CB_GETCURSEL, 0, 0))
    if selected_index >= 0:
        length = int(USER32.SendMessageW(ctypes.c_void_p(message_hwnd), CB_GETLBTEXTLEN, selected_index, 0))
        if length > 0:
            buffer = ctypes.create_unicode_buffer(length + 1)
            USER32.SendMessageW(ctypes.c_void_p(message_hwnd), CB_GETLBTEXT, selected_index, buffer)
            if buffer.value:
                return buffer.value

    return _read_control_text(message_hwnd) or _read_control_text(combo_hwnd)


def _enumerate_combo_items(combo_hwnd: int) -> list[str]:
    message_hwnd = _combo_message_hwnd(combo_hwnd)
    count = int(USER32.SendMessageW(ctypes.c_void_p(message_hwnd), CB_GETCOUNT, 0, 0))
    if count <= 0:
        return []

    items: list[str] = []
    for index in range(count):
        length = int(USER32.SendMessageW(ctypes.c_void_p(message_hwnd), CB_GETLBTEXTLEN, index, 0))
        if length < 0:
            items.append("")
            continue
        buffer = ctypes.create_unicode_buffer(length + 1)
        USER32.SendMessageW(ctypes.c_void_p(message_hwnd), CB_GETLBTEXT, index, buffer)
        items.append(buffer.value)
    return items


def _find_txt_item_index(items: list[str]) -> int | None:
    priorities = (".txt", "txt", "text")
    lowered_items = [item.lower() for item in items]
    for token in priorities:
        for index, item in enumerate(lowered_items):
            if token in item:
                return index
    return None


def _notify_combo_selection_changed(combo_hwnd: int) -> None:
    source_hwnd = _combo_message_hwnd(combo_hwnd)
    control_id = int(USER32.GetDlgCtrlID(ctypes.c_void_p(source_hwnd)))
    if control_id < 0:
        control_id = 0

    parents: list[int] = []
    parent = int(USER32.GetParent(ctypes.c_void_p(source_hwnd)))
    if parent:
        parents.append(parent)
        grand_parent = int(USER32.GetParent(ctypes.c_void_p(parent)))
        if grand_parent and grand_parent != parent:
            parents.append(grand_parent)

    for notify_code in (CBN_SELCHANGE, CBN_CLOSEUP):
        wparam = (int(notify_code) << 16) | (control_id & 0xFFFF)
        for parent_hwnd in parents:
            USER32.SendMessageW(ctypes.c_void_p(parent_hwnd), WM_COMMAND, wparam, source_hwnd)


def _select_combo_index_direct(combo_hwnd: int, index: int) -> str:
    message_hwnd = _combo_message_hwnd(combo_hwnd)
    result = int(USER32.SendMessageW(ctypes.c_void_p(message_hwnd), CB_SETCURSEL, index, 0))
    if result == -1:
        return _read_combo_text(combo_hwnd)
    _notify_combo_selection_changed(combo_hwnd)
    return _read_combo_text(combo_hwnd)


def _select_combo_index_physical(
    dialog: SaveDialogWindow,
    combo_hwnd: int,
    index: int,
    logger=None,
) -> str:
    _set_foreground_window(dialog.hwnd)
    _dialog_step_gap("set_save_type_physical_click_combo", logger=logger)
    _click_window_center(combo_hwnd)

    _dialog_step_gap("set_save_type_physical_expand", logger=logger)
    USER32.SendMessageW(ctypes.c_void_p(_combo_message_hwnd(combo_hwnd)), CB_SHOWDROPDOWN, 1, 0)

    _dialog_step_gap("set_save_type_physical_home", logger=logger)
    _tap_vk(VK_HOME)

    _dialog_step_gap("set_save_type_physical_down", logger=logger)
    for _ in range(max(0, index)):
        _tap_vk(VK_DOWN)
        time.sleep(0.03)

    _dialog_step_gap("set_save_type_physical_enter", logger=logger)
    _tap_vk(VK_RETURN)
    return _read_combo_text(combo_hwnd)


def _write_and_verify_edit_text(edit_hwnd: int, expected_text: str) -> str:
    USER32.SetFocus(ctypes.c_void_p(edit_hwnd))
    USER32.SendMessageW(ctypes.c_void_p(edit_hwnd), WM_SETTEXT, 0, ctypes.c_wchar_p(expected_text))
    time.sleep(0.05)
    return _read_control_text(edit_hwnd)


def _write_edit_with_keyboard_fallback(edit_hwnd: int, expected_text: str) -> str:
    USER32.SetFocus(ctypes.c_void_p(edit_hwnd))
    time.sleep(0.05)
    _press_modified_key(VK_CONTROL, VK_A)
    time.sleep(0.05)
    _send_unicode_text(expected_text)
    time.sleep(0.05)
    return _read_control_text(edit_hwnd)


def _combined_dialog_text(dialog_hwnd: int) -> str:
    title = _window_text(dialog_hwnd)
    child_text = " ".join(child.text for child in _enum_child_windows(dialog_hwnd, visible_only=False) if child.text)
    return f"{title} {child_text}".strip()


def _find_overwrite_dialog(exclude_hwnds: set[int] | None = None) -> SaveDialogWindow | None:
    exclude_hwnds = exclude_hwnds or set()
    for window in list_desktop_windows():
        if window.hwnd in exclude_hwnds or not window.visible or window.class_name != "#32770":
            continue
        blob = _combined_dialog_text(window.hwnd).lower()
        if any(keyword.lower() in blob for keyword in OVERWRITE_KEYWORDS):
            return SaveDialogWindow(
                hwnd=window.hwnd,
                title=window.title,
                class_name=window.class_name,
            )
    return None


def _find_overwrite_confirm_button(dialog: SaveDialogWindow) -> DialogChildWindow | None:
    buttons = [
        child
        for child in _enum_child_windows(dialog.hwnd)
        if child.class_name == "Button" and child.text
    ]
    if not buttons:
        return None

    for keyword in OVERWRITE_CONFIRM_BUTTON_KEYWORDS:
        for button in buttons:
            lowered = button.text.lower()
            if any(blocked in lowered for blocked in OVERWRITE_NEGATIVE_BUTTON_KEYWORDS):
                continue
            if keyword.lower() in lowered:
                return button
    return None


def _list_overwrite_candidate_buttons(dialog: SaveDialogWindow) -> list[str]:
    return [
        child.text
        for child in _enum_child_windows(dialog.hwnd)
        if child.class_name == "Button" and child.text
    ]


def _normalize_button_text(text: str) -> str:
    normalized = text.lower()
    for token in (" ", "\t", "\r", "\n", "&", "(", ")", "（", "）"):
        normalized = normalized.replace(token, "")
    return normalized


def _normalize_path_text(text: str) -> str:
    normalized = text.strip().strip('"')
    return normalized.rstrip("\\/").lower()


def _find_save_button(dialog: SaveDialogWindow, logger=None) -> DialogChildWindow | None:
    buttons = [
        child
        for child in _enum_child_windows(dialog.hwnd)
        if child.class_name == "Button" and child.text
    ]
    if logger is not None:
        for index, button in enumerate(buttons, start=1):
            logger.info("Save As button candidate[%s]: %s", index, _child_debug_string(button))

    if not buttons:
        return None

    target_keywords = ("保存", "保存s", "save")
    negative_keywords = ("取消", "cancel", "close", "关闭")

    matched: list[DialogChildWindow] = []
    for button in buttons:
        lowered = _normalize_button_text(button.text)
        if any(keyword in lowered for keyword in negative_keywords):
            continue
        if any(keyword in lowered for keyword in target_keywords):
            matched.append(button)

    if not matched:
        return None

    matched.sort(key=lambda item: (item.rect[1], item.rect[0]))
    return matched[0]


def _did_dialog_close_after_action(hwnd: int, timeout_sec: float = 0.6) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _is_window(hwnd):
            return True
        time.sleep(0.1)
    return not _is_window(hwnd)


def _wait_for_dialog_close(hwnd: int, timeout_sec: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _is_window(hwnd):
            return
        time.sleep(0.1)
    raise Chi660eAutoError("Timed out waiting for dialog to close.")


def _confirm_overwrite_dialog(
    dialog: SaveDialogWindow,
    close_timeout_sec: float = 5.0,
    logger=None,
) -> dict[str, object]:
    _dialog_step_gap("overwrite_confirm", logger=logger)
    _set_foreground_window(dialog.hwnd)
    candidate_buttons = _list_overwrite_candidate_buttons(dialog)
    if logger is not None:
        logger.info("Save As overwrite candidate buttons: %s", candidate_buttons)
    button = _find_overwrite_confirm_button(dialog)
    if button is not None:
        USER32.SendMessageW(ctypes.c_void_p(button.hwnd), BM_CLICK, 0, 0)
        method = "overwrite_button_click"
    else:
        if logger is not None:
            logger.warning("Save As overwrite handling deferred")
        _mark_dialog_step()
        return {
            "success": False,
            "method": "deferred",
            "dialog_title": dialog.title,
            "dialog_class": dialog.class_name,
            "candidate_buttons": candidate_buttons,
        }

    _wait_for_dialog_close(dialog.hwnd, timeout_sec=close_timeout_sec)
    _mark_dialog_step()
    if logger is not None:
        logger.info("Save As overwrite confirmed: method=%s", method)
    return {
        "success": True,
        "method": method,
        "dialog_title": dialog.title,
        "dialog_class": dialog.class_name,
    }


def _target_file_ready(
    target_file: Path,
    existed_before: bool,
    previous_mtime_ns: int | None,
    previous_size: int | None,
) -> bool:
    if not target_file.exists():
        return False
    if not existed_before:
        return True

    stat = target_file.stat()
    if previous_mtime_ns is not None and stat.st_mtime_ns != previous_mtime_ns:
        return True
    if previous_size is not None and stat.st_size != previous_size:
        return True
    return False


def wait_for_save_as_dialog(
    timeout_sec: float = 5.0,
    interval_sec: float = 0.2,
    title_keyword: str = "另存为",
    logger=None,
) -> SaveDialogWindow:
    deadline = time.monotonic() + timeout_sec
    keyword_lower = title_keyword.lower()

    while time.monotonic() < deadline:
        candidates = [
            window
            for window in list_desktop_windows()
            if keyword_lower in window.title.lower()
        ]
        if candidates:
            candidates.sort(key=lambda item: (item.class_name == "#32770", item.title), reverse=True)
            selected = candidates[0]
            if logger is not None:
                logger.info(
                    "Save As dialog detected: title=%s class=%s hwnd=%s",
                    selected.title,
                    selected.class_name,
                    selected.hwnd,
                )
            _mark_dialog_step()
            return SaveDialogWindow(
                hwnd=selected.hwnd,
                title=selected.title,
                class_name=selected.class_name,
            )
        time.sleep(interval_sec)

    raise WindowNotFoundError("Timed out waiting for system 'Save As' dialog.")


def set_save_directory(dialog: SaveDialogWindow, directory: str | Path, logger=None) -> dict[str, object]:
    directory_path = str(Path(directory))
    if logger is not None:
        logger.info("entered set_save_directory")
        logger.info("Save As directory set requested: %s", directory_path)
    _dialog_step_gap("set_save_directory", logger=logger)
    _set_foreground_window(dialog.hwnd)

    control = _resolve_directory_control(dialog, logger=logger)
    actual = _write_and_verify_edit_text(control.hwnd, directory_path)
    method = f"{control.method}+wm_settext"
    if _normalize_path_text(actual) != _normalize_path_text(directory_path):
        actual = _write_edit_with_keyboard_fallback(control.hwnd, directory_path)
        method = f"{control.method}+keyboard_fallback"

    _dialog_step_gap("set_save_directory_jump", logger=logger)
    _tap_vk(VK_RETURN)
    _dialog_step_gap("set_save_directory_verify", logger=logger)
    actual_after = _read_control_text(control.hwnd)
    matched = _normalize_path_text(actual_after) == _normalize_path_text(directory_path)

    if logger is not None:
        logger.info(
            "Save As directory verify: expected=%s actual=%s matched=%s method=%s",
            directory_path,
            actual_after,
            matched,
            method,
        )
    if not matched:
        raise Chi660eAutoError(
            f"Save As directory verification failed: expected={directory_path!r} actual={actual_after!r}"
        )

    _mark_dialog_step()
    result = {
        "success": True,
        "method": method,
        "directory": directory_path,
        "actual": actual_after,
        "matched": True,
    }
    if logger is not None:
        logger.info("Save As directory set: directory=%s method=%s", directory_path, result["method"])
    return result


def set_filename(dialog: SaveDialogWindow, filename: str, logger=None) -> dict[str, object]:
    requested_filename = Path(str(filename)).name
    if logger is not None:
        logger.info("entered set_filename")
        logger.info("Save As filename set requested: %s", requested_filename)
    _dialog_step_gap("set_filename", logger=logger)
    _set_foreground_window(dialog.hwnd)

    control = _resolve_filename_control(dialog, logger=logger)
    actual = _write_and_verify_edit_text(control.hwnd, requested_filename)
    method = f"{control.method}+wm_settext"
    if actual != requested_filename:
        actual = _write_edit_with_keyboard_fallback(control.hwnd, requested_filename)
        method = f"{control.method}+keyboard_fallback"

    matched = actual == requested_filename
    if logger is not None:
        logger.info(
            "Save As filename verify: expected=%s actual=%s matched=%s method=%s",
            requested_filename,
            actual,
            matched,
            method,
        )
    if not matched:
        raise Chi660eAutoError(
            f"Save As filename verification failed: expected={requested_filename!r} actual={actual!r}"
        )

    _mark_dialog_step()
    payload = {
        "success": True,
        "method": method,
        "filename": requested_filename,
        "actual": actual,
        "matched": True,
    }
    if logger is not None:
        logger.info("Save As filename set: filename=%s method=%s", requested_filename, payload["method"])
    return payload


def set_save_type_to_txt(dialog: SaveDialogWindow, logger=None) -> dict[str, object]:
    if logger is not None:
        logger.info("entered set_save_type_to_txt")
    _dialog_step_gap("set_save_type", logger=logger)
    _set_foreground_window(dialog.hwnd)
    _log_combo_control_candidates(dialog, logger=logger)
    combo = _resolve_type_control(dialog, logger=logger)

    items = _enumerate_combo_items(combo.hwnd)
    if not items:
        _dialog_step_gap("set_save_type_dropdown_enum", logger=logger)
        USER32.SendMessageW(ctypes.c_void_p(_combo_message_hwnd(combo.hwnd)), CB_SHOWDROPDOWN, 1, 0)
        _dialog_step_gap("set_save_type_dropdown_read", logger=logger)
        items = _enumerate_combo_items(combo.hwnd)

    if not items:
        _dialog_step_gap("set_save_type_physical_dropdown_enum", logger=logger)
        _set_foreground_window(dialog.hwnd)
        _click_window_center(combo.hwnd)
        _dialog_step_gap("set_save_type_physical_dropdown_read", logger=logger)
        USER32.SendMessageW(ctypes.c_void_p(_combo_message_hwnd(combo.hwnd)), CB_SHOWDROPDOWN, 1, 0)
        items = _enumerate_combo_items(combo.hwnd)

    if logger is not None:
        logger.info("Save As type combo items: %s", items)

    if not items:
        raise Chi660eAutoError("Save As type combo items unavailable after dropdown")

    target_index = _find_txt_item_index(items)
    if logger is not None:
        logger.info("Save As type target index: %s", target_index)
    if target_index is None:
        if logger is not None:
            logger.error("Save As type verification failed with items=%s", items)
        raise Chi660eAutoError(f"Save As type option containing txt was not found: items={items!r}")

    actual = _select_combo_index_direct(combo.hwnd, target_index)
    method = f"{combo.method}+direct_index_select"
    if logger is not None:
        logger.info("Save As type direct select result: actual=%s", actual)

    matched = "txt" in actual.lower()
    if not matched:
        actual = _select_combo_index_physical(dialog, combo.hwnd, target_index, logger=logger)
        method = f"{combo.method}+physical_index_select"
        if logger is not None:
            logger.info("Save As type physical select result: actual=%s", actual)
        matched = "txt" in actual.lower()

    if logger is not None:
        logger.info(
            "Save As type verify: actual=%s matched=%s method=%s",
            actual,
            matched,
            method,
        )
    if not matched:
        if logger is not None:
            logger.error("Save As type verification failed with items=%s", items)
        raise Chi660eAutoError(
            f"Save As type verification failed: expected contains 'txt' actual={actual!r}"
        )

    payload = {
        "success": True,
        "method": method,
        "selection": actual,
        "matched": True,
    }
    _mark_dialog_step()
    if logger is not None:
        logger.info("Save As type set: selection=%s method=%s", payload["selection"], payload["method"])
    return payload


def set_target_path_in_filename_field(dialog: SaveDialogWindow, target_path: str | Path, logger=None) -> dict[str, object]:
    if logger is not None:
        logger.info("entered set_target_path_in_filename_field")
    _dialog_step_gap("set_target_path", logger=logger)
    _set_foreground_window(dialog.hwnd)

    target_path_text = str(Path(target_path))
    if logger is not None:
        logger.info("Save As filename control final target: %s", target_path_text)
    control = _resolve_filename_control(dialog, logger=logger)

    actual = _write_and_verify_edit_text(control.hwnd, target_path_text)
    method = f"{control.method}+wm_settext"
    if actual != target_path_text:
        actual = _write_edit_with_keyboard_fallback(control.hwnd, target_path_text)
        method = f"{control.method}+keyboard_fallback"

    matched = actual == target_path_text
    if logger is not None:
        logger.info(
            "Save As target path verify: expected=%s actual=%s matched=%s method=%s",
            target_path_text,
            actual,
            matched,
            method,
        )
    if not matched:
        raise Chi660eAutoError(
            f"Save As filename verification failed: expected={target_path_text!r} actual={actual!r}"
        )

    payload = {
        "success": True,
        "method": method,
        "target_path": target_path_text,
        "actual": actual,
        "matched": True,
    }
    if logger is not None:
        logger.info("Save As filename set: filename=%s method=%s", target_path_text, payload["method"])
    return payload


def confirm_save(dialog: SaveDialogWindow, logger=None) -> dict[str, object]:
    if logger is not None:
        logger.info("entered confirm_save")
    _dialog_step_gap("confirm_save", logger=logger)
    _set_foreground_window(dialog.hwnd)
    if logger is not None:
        logger.info("Save As confirm focus before: %s", _get_focus_debug_string(dialog.hwnd))

    method = ""
    button = _find_save_button(dialog, logger=logger)
    if button is not None:
        USER32.SendMessageW(ctypes.c_void_p(button.hwnd), BM_CLICK, 0, 0)
        method = "button_bm_click"
        time.sleep(0.5)
        if _did_dialog_close_after_action(dialog.hwnd):
            payload = {
                "success": True,
                "method": method,
            }
            _mark_dialog_step()
            if logger is not None:
                logger.info("Save As confirm triggered: method=%s", method)
                logger.info("Save As confirm focus after: %s", _get_focus_debug_string(dialog.hwnd))
            return payload
        if logger is not None:
            logger.warning("Save As confirm dialog still open after method=%s", method)

        _click_window_center(button.hwnd)
        method = "button_physical_click"
        time.sleep(0.5)
        if _did_dialog_close_after_action(dialog.hwnd):
            payload = {
                "success": True,
                "method": method,
            }
            _mark_dialog_step()
            if logger is not None:
                logger.info("Save As confirm triggered: method=%s", method)
                logger.info("Save As confirm focus after: %s", _get_focus_debug_string(dialog.hwnd))
            return payload
        if logger is not None:
            logger.warning("Save As confirm dialog still open after method=%s", method)

    _tap_vk(VK_RETURN)
    method = "enter_key_fallback"
    time.sleep(0.5)
    if not _did_dialog_close_after_action(dialog.hwnd):
        if logger is not None:
            logger.error("Save As confirm dialog still open after method=%s", method)
            logger.info("Save As confirm focus after: %s", _get_focus_debug_string(dialog.hwnd))
        raise Chi660eAutoError("Save As confirm action did not close dialog.")

    payload = {
        "success": True,
        "method": method,
    }
    _mark_dialog_step()
    if logger is not None:
        logger.info("Save As confirm triggered: method=%s", method)
        logger.info("Save As confirm focus after: %s", _get_focus_debug_string(dialog.hwnd))
    return payload


def _wait_for_save_completion(
    dialog: SaveDialogWindow,
    target_file: Path,
    *,
    file_timeout_sec: float,
    existed_before: bool,
    previous_mtime_ns: int | None,
    previous_size: int | None,
    logger=None,
) -> dict[str, object]:
    _dialog_step_gap("wait_for_completion", logger=logger)
    deadline = time.monotonic() + file_timeout_sec
    overwrite_result: dict[str, object] | None = None

    while time.monotonic() < deadline:
        overwrite_dialog = _find_overwrite_dialog(exclude_hwnds={dialog.hwnd})
        if overwrite_dialog is not None and overwrite_result is None:
            if logger is not None:
                logger.info(
                    "Save As overwrite dialog detected: title=%s class=%s",
                    overwrite_dialog.title,
                    overwrite_dialog.class_name,
                )
                logger.info("Save As overwrite dialog text blob: %s", _combined_dialog_text(overwrite_dialog.hwnd))
            overwrite_result = _confirm_overwrite_dialog(overwrite_dialog, logger=logger)

        if (
            not _is_window(dialog.hwnd)
            and _find_overwrite_dialog(exclude_hwnds={dialog.hwnd}) is None
            and _target_file_ready(target_file, existed_before, previous_mtime_ns, previous_size)
        ):
            if logger is not None:
                logger.info("Save As target file exists: path=%s", target_file)
            return {
                "success": True,
                "overwrite_result": overwrite_result,
                "file_path": target_file,
            }

        time.sleep(0.2)

    if logger is not None:
        if overwrite_result is None:
            logger.error(
                "Save As target missing after confirm and no overwrite dialog was confirmed: path=%s",
                target_file,
            )
        if _is_window(dialog.hwnd):
            logger.error(
                "Save As dialog still open when completion timed out: hwnd=%s title=%s",
                dialog.hwnd,
                dialog.title,
            )
        else:
            logger.error("Save As dialog closed but target file missing: path=%s", target_file)
        logger.error("Save As failed after confirm: expected target missing: %s", target_file)
    raise Chi660eAutoError(f"Saved file was not found after dialog closed: {target_file}")


def save_as_txt(
    save_directory: str | Path,
    filename: str,
    dialog_timeout_sec: float = 5.0,
    file_timeout_sec: float = 5.0,
    logger=None,
) -> dict[str, object]:
    save_dir_path = Path(save_directory)
    save_dir_path.mkdir(parents=True, exist_ok=True)

    normalized_filename = Path(str(filename)).name
    target_file = save_dir_path / normalized_filename
    target_existed_before = target_file.exists()
    previous_mtime_ns = target_file.stat().st_mtime_ns if target_existed_before else None
    previous_size = target_file.stat().st_size if target_existed_before else None

    dialog = wait_for_save_as_dialog(timeout_sec=dialog_timeout_sec, logger=logger)
    directory_result = set_save_directory(dialog, save_dir_path, logger=logger)
    filename_result = set_filename(dialog, normalized_filename, logger=logger)
    type_result = set_save_type_to_txt(dialog, logger=logger)
    confirm_result = confirm_save(dialog, logger=logger)
    completion_result = _wait_for_save_completion(
        dialog,
        target_file,
        file_timeout_sec=file_timeout_sec,
        existed_before=target_existed_before,
        previous_mtime_ns=previous_mtime_ns,
        previous_size=previous_size,
        logger=logger,
    )

    return {
        "success": True,
        "dialog": dialog,
        "directory_result": directory_result,
        "type_result": type_result,
        "filename_result": filename_result,
        "confirm_result": confirm_result,
        "overwrite_result": completion_result["overwrite_result"],
        "file_path": completion_result["file_path"],
    }
