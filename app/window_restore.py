from __future__ import annotations

import ctypes
import ctypes.wintypes
import time

GA_ROOTOWNER = 3
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMINNOACTIVE = 7
SW_RESTORE = 9
HWND_TOP = 0
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040

USER32 = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", RECT),
    ]


def get_root_owner_hwnd(hwnd: int) -> int:
    root = int(USER32.GetAncestor(ctypes.c_void_p(hwnd), GA_ROOTOWNER) or 0)
    return root or hwnd


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    if not USER32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return (0, 0, 0, 0)
    return (
        int(rect.left),
        int(rect.top),
        int(rect.right - rect.left),
        int(rect.bottom - rect.top),
    )


def window_rect_is_zero(hwnd: int) -> bool:
    _left, _top, width, height = window_rect(hwnd)
    return width <= 0 or height <= 0


def window_is_minimized(hwnd: int) -> bool:
    handle = ctypes.c_void_p(hwnd)
    if USER32.IsIconic(handle):
        return True

    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    if USER32.GetWindowPlacement(handle, ctypes.byref(placement)):
        if placement.showCmd in (SW_SHOWMINIMIZED, SW_SHOWMINNOACTIVE):
            return True
    return False


def window_needs_restore(hwnd: int) -> bool:
    root = get_root_owner_hwnd(hwnd)
    handle = ctypes.c_void_p(root)
    return (
        window_is_minimized(root)
        or not USER32.IsWindowVisible(handle)
        or window_rect_is_zero(root)
    )


def bring_window_to_foreground(hwnd: int) -> bool:
    root = get_root_owner_hwnd(hwnd)
    root_handle = ctypes.c_void_p(root)
    fg = int(USER32.GetForegroundWindow() or 0)
    current_tid = int(KERNEL32.GetCurrentThreadId())
    attached_tids: set[int] = set()

    try:
        if fg:
            fg_pid = ctypes.wintypes.DWORD()
            fg_tid = int(
                USER32.GetWindowThreadProcessId(ctypes.c_void_p(fg), ctypes.byref(fg_pid)) or 0
            )
            if fg_tid and fg_tid != current_tid:
                if USER32.AttachThreadInput(current_tid, fg_tid, True):
                    attached_tids.add(fg_tid)

        root_pid = ctypes.wintypes.DWORD()
        root_tid = int(
            USER32.GetWindowThreadProcessId(root_handle, ctypes.byref(root_pid)) or 0
        )
        if root_tid and root_tid != current_tid and root_tid not in attached_tids:
            if USER32.AttachThreadInput(current_tid, root_tid, True):
                attached_tids.add(root_tid)

        USER32.BringWindowToTop(root_handle)
        USER32.SetForegroundWindow(root_handle)
        USER32.SetActiveWindow(root_handle)
        USER32.SetFocus(root_handle)
        USER32.SetWindowPos(
            root_handle,
            ctypes.c_void_p(HWND_TOP),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        return True
    finally:
        for tid in attached_tids:
            USER32.AttachThreadInput(current_tid, tid, False)


def restore_window(hwnd: int, logger=None, attempts: int = 15, sleep_sec: float = 0.25) -> bool:
    root = get_root_owner_hwnd(hwnd)
    root_handle = ctypes.c_void_p(root)
    if not window_needs_restore(root):
        return True

    for attempt in range(1, attempts + 1):
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        if USER32.GetWindowPlacement(root_handle, ctypes.byref(placement)):
            if placement.showCmd in (SW_SHOWMINIMIZED, SW_SHOWMINNOACTIVE):
                placement.showCmd = SW_RESTORE
            USER32.SetWindowPlacement(root_handle, ctypes.byref(placement))

        USER32.ShowWindowAsync(root_handle, SW_RESTORE)
        USER32.ShowWindowAsync(root_handle, SW_SHOWNORMAL)
        USER32.SetWindowPos(
            root_handle,
            ctypes.c_void_p(HWND_TOP),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        bring_window_to_foreground(root)
        time.sleep(sleep_sec)

        if (
            not window_is_minimized(root)
            and USER32.IsWindowVisible(root_handle)
            and not window_rect_is_zero(root)
        ):
            return True

        if logger is not None:
            logger.debug(
                "Window restore retry: hwnd=%s root=%s attempt=%s visible=%s minimized=%s rect=%s",
                hwnd,
                root,
                attempt,
                bool(USER32.IsWindowVisible(root_handle)),
                window_is_minimized(root),
                window_rect(root),
            )

    return False


def ensure_window_restored_or_raise(hwnd: int, logger=None, attempts: int = 15) -> int:
    root = get_root_owner_hwnd(hwnd)
    if restore_window(root, logger=logger, attempts=attempts):
        return root
    raise RuntimeError("Window restore failed")
