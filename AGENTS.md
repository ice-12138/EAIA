# EAIA Project Memory

## Python Runtime

- Always use the Conda `EAIA` environment for Python commands, scripts, tests, and package operations.
- Prefer `conda run -n EAIA python ...` for one-off commands and automation. For an interactive shell, run `conda activate EAIA` before invoking `python`.
- Run tests with `conda run -n EAIA python -m unittest discover -s tests -v`.
- Install Python packages with `conda run -n EAIA python -m pip install ...`.

## Project Notes

- This repository contains the EAIA HOScrcpy screen-capture, OCR, equipment database, and optimizer workflows.
- Python dependencies and runtime behavior should be verified in the `EAIA` environment rather than the system or base Python installation.

## Frontend Entry Architecture

- `frontend/src/UnifiedWorkspace.vue` is the only frontend page shell and route-page entry.
- `frontend/src/main.js` must mount only `UnifiedWorkspace.vue`.
- Add new page modes to `UnifiedWorkspace.vue`'s hash-based routing; do not introduce alternate page-shell Vue entry components.
