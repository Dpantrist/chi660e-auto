# Resource Layout

This directory contains the local MaaFramework resource bundle skeleton for CHI660E automation.

- `image/` holds placeholder folders for future image assets.
- `pipeline/` contains only valid empty JSON placeholders at this stage.
- No business pipeline nodes are implemented here yet.
- The source-of-truth `default_pipeline.json` lives at the project root.
- At startup, the Python bootstrap synchronizes that file into the bundle root before `resource.post_bundle(...)`.
