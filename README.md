# CHI660E Auto

`chi660e-auto` is an initialization skeleton for a CHI660E automation project built on MaaFramework.

Current scope:

- Only the Python layer and project skeleton are implemented.
- No CV/EIS/GCD/activation business pipeline nodes are implemented.
- No GUI is implemented.
- No OCR model or OCR code is included.

Startup:

```bash
python chi660e_auto.py
```

Environment notes:

- Python 3.10+ is required.
- `numpy` and `opencv-python` are listed in `requirements.txt` for capture persistence.
- MaaFramework Python bindings are not declared here because the package name and installation path can vary by machine.
- Install the MaaFramework Python binding according to your local MaaFW environment before running.

Runtime behavior:

- The app only performs startup initialization.
- It searches for the target main window with keyword `CHI660E Electrochemical Workstation`.
- It then connects the Win32 controller, validates one screencap, loads the resource bundle, and binds the tasker.
- It does not execute any business pipeline entry.

Project notes:

- Root `default_pipeline.json` is the source of truth for shared default parameters.
- Before resource loading, the app synchronizes that file into the resource bundle root so MaaFW can consume it.
- Project logging, debug captures, and replay records are independent from MaaFW debug configuration.
