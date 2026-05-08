# Architecture Layers

- `chi660e_auto.py`: command-line entry. Launches the GUI by default and keeps debug/workflow entries available.
- `app/gui_app.py`: Tkinter GUI layer. Owns layout, local form state, task preview, runtime status, and start/pause interaction.
- `app/gui_controller.py`: GUI adapter layer. Maps GUI state into runnable workflow segments and validates user-facing numeric fields.
- `app/workflow_segments.py` / `app/gui_models.py`: workflow parameter and plan models.
- `app/workflow_runner.py`: workflow execution layer. Owns ordered segment execution, repeat loops, rest segments, EIS interval triggers, pause checkpoints, and segment-level failure boundaries.
- `app/task_runner.py`: front-half flow layer. Owns CHI660E technique selection, parameter window interaction, and shared window switching.
- `app/post_run_flow.py`: post-run flow layer. Owns run/poll/save orchestration after parameter confirmation.
- `app/visual_action_specs.py`: visual declaration layer. Owns templates, modes, geometry, and state-only rules.
- `app/cv_config.py`, `app/eis_config.py`, `app/gcd_config.py`: business parameter defaults and conversion helpers.
- `app/naming_rules.py`: filename rule layer. Owns save-name generation only.
- `app/save_dialog.py`: Windows "Save As" dialog layer.
- `resource/pipeline/*.json`: Maa atomic action layer. Owns reusable fallback click shells and atomic actions such as `InputText`.

## Main Window Fallback Shell

- `resource/pipeline/10_common_main.json` is a fallback click shell only.
- Main-window click geometry must come from `app/visual_action_specs.py`, not from pipeline JSON.

## Entry Points

- `chi660e_auto.py --run-cv-front-half`: CV front-half debug entry only.
- `chi660e_auto.py --run-eis-front-half`: EIS front-half debug entry only.
- `chi660e_auto.py --run-gcd-front-half`: GCD front-half debug entry only.
- `chi660e_auto.py --run-open-circuit-potential`: OCP read debug entry only.
- `chi660e_auto.py --run-default-workflow`: workflow entry for the minimal runnable default plan.
- `chi660e_auto.py --run-eis-workflow`: workflow entry for a single EIS-after-activation segment.
- `chi660e_auto.py --run-gcd-workflow`: workflow entry for a single GCD segment.
- `chi660e_auto.py --gui`: launches the workflow planner GUI.

## Current Segment Status

- Runnable now: `activation_cv`, `eis_after_activation`, `cv_series_item`, `rest`, `eis_after_cv`, `gcd_series_item`, `eis_after_gcd`.
- `eis_after_cv` and `eis_after_gcd` are consumed inline by the corresponding CV/GCD repeat loops when those series are enabled. They trigger by interval cycles and can also run once as standalone EIS segments when no matching series consumes them.
- CV/GCD repeat loops run the front-half parameter flow on the first repeat of each scan rate/current density. Later repeats skip duplicate parameter filling unless an inline EIS was inserted, in which case the next repeat forces the front-half flow to restore the correct technique.
- CV cycle count is derived from `Sweep Segments`; GCD cycle count is derived from `Number of Segments`. Odd segment counts use `(segments - 1) / 2`; even segment counts use `segments / 2`.
- CLI `--run-default-workflow` intentionally runs only the minimal activation CV plan. The GUI builds the full configurable plan from user selections.

## Runtime State

- `config/gui_state.json` stores local GUI selections and is ignored by git.
- `logs/`, `debug/`, and `replay/` are runtime output directories and are ignored by git.
- `tests/test_data/` is the default save directory for CLI debug/workflow entries.
- Packaged releases are built as PyInstaller one-directory, no-console bundles. The current local release package is `dist/chi660e_auto_v1.1_noupx.zip`; UPX is disabled to reduce Defender/SmartScreen false positives.
