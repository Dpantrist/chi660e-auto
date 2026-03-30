from __future__ import annotations

from app.errors import TaskerBindError


def _import_tasker_type():
    try:
        from maa.tasker import Tasker
    except ImportError as exc:
        raise TaskerBindError(
            "Failed to import maa.tasker. Install the MaaFramework Python binding "
            "for this machine before running the project."
        ) from exc
    return Tasker


def create_tasker():
    tasker_type = _import_tasker_type()
    try:
        return tasker_type()
    except Exception as exc:
        raise TaskerBindError("Failed to create MaaFramework Tasker.") from exc


def bind_tasker(tasker, resource, controller):
    try:
        tasker.bind(resource, controller)
    except Exception as exc:
        raise TaskerBindError("Tasker bind(resource, controller) raised an exception.") from exc

    if not getattr(tasker, "inited", False):
        raise TaskerBindError("Tasker binding failed: tasker.inited is False.")
    return tasker
