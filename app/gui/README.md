# GUI Directory

This directory contains GUI-specific files for the CHI660E automation app.

- `gui.ui` is a reserved UI artifact/reference file.
- The active GUI implementation is `app/gui_app.py` and uses Tkinter/ttk.
- GUI state is persisted through `app/gui_persistence.py` into `config/gui_state.json`.
- GUI input is converted to workflow segments by `app/gui_controller.py`; the GUI layer does not execute automation directly.

The current GUI supports task selection, parameter editing, save-directory selection, execution-plan preview, runtime status, and start/pause control for the workflow runner.
