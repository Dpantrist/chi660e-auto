from __future__ import annotations

"""运行控制对象。

只提供 GUI/CLI 共享的协作式停止请求，不直接承载任何业务流程。
"""

import threading
import time

from app.errors import Chi660eAutoError


class RunStopRequested(Chi660eAutoError):
    """用户发起了协作式暂停/停止请求。"""


class RunControl:
    """线程安全的最小运行控制器。"""

    def __init__(self) -> None:
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def reset(self) -> None:
        self._stop_event.clear()

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def raise_if_stop_requested(self, stage: str | None = None) -> None:
        if not self.is_stop_requested():
            return
        message = "已收到暂停请求，自动化流程已停止。"
        if stage:
            message = f"已收到暂停请求，自动化流程已在 {stage} 停止。"
        raise RunStopRequested(message)


def sleep_with_run_control(
    run_control: RunControl | None,
    total_sec: float,
    stage: str,
    chunk_sec: float = 0.2,
) -> None:
    """可响应暂停请求的最小 sleep 包装。"""
    if total_sec <= 0:
        if run_control is not None:
            run_control.raise_if_stop_requested(stage)
        return

    deadline = time.monotonic() + float(total_sec)
    step = max(0.05, float(chunk_sec))

    while True:
        if run_control is not None:
            run_control.raise_if_stop_requested(stage)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(step, remaining))

