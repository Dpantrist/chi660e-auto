# Architecture Layers

- `app/task_runner.py`: flow layer. Owns sequence, window switching, branching, and fallback decisions.
- `app/visual_action_specs.py`: visual declaration layer. Owns templates, modes, geometry, and state-only rules.
- `app/cv_config.py`: business parameter layer. Owns CV front-half runtime values.
- `resource/pipeline/*.json`: atomic action layer. Owns fallback click shells and atomic actions such as `InputText`.

## Main Window Fallback Shell

- `resource/pipeline/10_common_main.json` is a fallback click shell only.
- Main-window click geometry must come from `app/visual_action_specs.py`, not from pipeline JSON.
