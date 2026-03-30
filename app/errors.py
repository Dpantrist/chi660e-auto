class Chi660eAutoError(Exception):
    """Base exception for the CHI660E automation skeleton."""


class WindowNotFoundError(Chi660eAutoError):
    """Raised when the target desktop window cannot be found."""


class ControllerInitError(Chi660eAutoError):
    """Raised when the Win32 controller cannot be initialized or connected."""


class ResourceLoadError(Chi660eAutoError):
    """Raised when the MaaFramework resource bundle cannot be loaded."""


class TaskerBindError(Chi660eAutoError):
    """Raised when the tasker cannot be created or bound."""
