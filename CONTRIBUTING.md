# Contributing

Use Python 3.11+ and an isolated environment. Install the tested development dependencies:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m ruff check .
python -m ruff format --check .
python -m pytest --cov=fileflow --cov-fail-under=90
python -m build
```

Use `QT_QPA_PLATFORM=offscreen` in headless environments. Test only in disposable temporary
folders. Do not introduce tests that operate on a developer's real Downloads/Documents.

Changes to filesystem behavior need failure-path tests: existing destinations, modified
content, interruption/recovery and undo. Maintain the no-overwrite/no-automatic-deletion
contract and make limitations visible. Keep the Qt layer separate from planning/execution.

No real user filenames/content in examples, logs or screenshots. Use original synthetic data.
Document new dependencies, provenance and licenses. No vendored private/employer/university code.
