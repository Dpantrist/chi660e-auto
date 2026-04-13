# Architecture Layers

- `app/task_runner.py`: flow layer. Owns sequence, window switching, branching, and fallback decisions.
- `app/post_run_flow.py`: post-run flow layer. Owns shared run/poll/save orchestration after parameter confirmation.
- `app/workflow_runner.py`: workflow flow layer. Owns ordered segment execution and segment-level failure boundaries.
- `app/visual_action_specs.py`: visual declaration layer. Owns templates, modes, geometry, and state-only rules.
- `app/cv_config.py`: business parameter layer. Owns CV front-half runtime values.
- `app/workflow_segments.py` / `app/gui_models.py`: workflow parameter and plan models.
- `app/naming_rules.py`: filename rule layer. Owns save-name generation only.
- `app/save_dialog.py`: system dialog layer. Owns Windows “另存为” handling without TemplateMatch.
- `resource/pipeline/*.json`: atomic action layer. Owns fallback click shells and atomic actions such as `InputText`.

## Main Window Fallback Shell

- `resource/pipeline/10_common_main.json` is a fallback click shell only.
- Main-window click geometry must come from `app/visual_action_specs.py`, not from pipeline JSON.

## Entry Points

- `chi660e_auto.py --run-cv-front-half`: front-half debug entry only.
- `chi660e_auto.py --run-default-workflow`: workflow entry for the current runnable default plan.
- `chi660e_auto.py --gui`: launches the minimal workflow planner GUI.

## Current Segment Status

- Runnable now: `activation_cv`, `cv_series_item`, `rest`
- Modeled but not wired yet: `eis_after_activation`, `eis_after_cv`, `eis_after_gcd`, `gcd_series_item`
