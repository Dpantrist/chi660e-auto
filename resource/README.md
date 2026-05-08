# Resource Layout

This directory contains the local MaaFramework resource bundle for CHI660E automation.

- `default_pipeline.json` is the bundle root pipeline file loaded by MaaFramework.
- `pipeline/` contains reusable atomic actions and fallback shells:
  - `10_common_main.json`: main-window fallback clicks.
  - `11_common_technique.json`: technique window interactions.
  - `12_common_param_input.json`: shared parameter-input actions.
  - `20_cv.json`, `21_eis.json`, `22_gcd.json`: technique-specific parameter actions.
  - `30_flow_sequence.json`: sequence-level action shells.
- `image/` contains template images grouped by window or technique:
  - `main/`, `techniques/`, `cv/`, `eis/`, `gcd/`, `common/`.
- `window_baseline/` stores baseline geometry for CHI660E windows used by visual action specs.
- `gui/reference/maa_wpf/` stores UI reference assets only; it is not part of the runtime automation path.
- `icon/` and `image/common/` contain app icon assets used by packaging or GUI display.

Keep pipeline JSON, image templates, and window baselines synchronized. Visual geometry should remain in `app/visual_action_specs.py`; pipeline JSON should stay focused on Maa atomic actions and fallback shells.
