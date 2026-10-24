# Verification record

The initial authoring environment was Linux, Python 3.12.14, PySide6-Essentials 6.12.0,
pytest 9.1.1 and Ruff 0.17.2. Dependency versions are recorded in `requirements-dev.lock`.

## Executed locally

- **70 pytest tests passed**; measured core/CLI coverage **93.44%** (desktop module excluded).
- Four Qt real-widget integration tests cover preview/apply/undo, canceled application,
  rule invalidation, errors, and preview-only watching.
- Two subprocesses actually exited with code 77 inside the two link/unlink interruption
  windows, leaving the journal `applying`; a fresh process recovered and undid each run.
- Separate injected OSError cases exercise graceful partial-run reporting and external collisions.
- Cross-process lock contention, symlink boundaries, path/template validation and stale content
  checks passed. Duplicate files remained unchanged.
- Watch tests use controlled monotonic times and demonstrate settling, disappearing inputs,
  filename reuse and explicit automatic execution on synthetic files.
- Ruff check/format passed; Python source distribution and wheel were built.
- Actual Qt screenshots were captured offscreen. The harness generated seven moves, one
  duplicate group and one skipped temporary file, performed a real apply, then an undo.
  See `demo-evidence.json`; no fabricated dashboard counters or filesystem benchmarks.

## CI

The workflow runs Linux on Python 3.11/3.12 and Windows on Python 3.12, installing the optional
Qt dependency so GUI tests actually run. It executes tests with a 90% measured-core coverage
gate, lint, formatting, package builds and uploads verification artifacts. Consult the live run
for its exact platform results; configure-and-push alone is not evidence of a passing job.

The [first completed CI run](https://github.com/Davorin51/fileflow/actions/runs/40128654773)
passed all three jobs: Linux/Python 3.11, Linux/Python 3.12 and Windows/Python 3.12, including
real filesystem and Qt tests, lint/format checks and package builds. Later visual refinements
are covered by subsequent workflow runs and the repeated local 70-test pass.

macOS has not been verified. Windows/Linux native display, screen-reader behavior, high DPI,
code signing and installer behavior need further manual/device validation. Offscreen widget
rendering is not evidence that every desktop platform has been visually exercised.

## Boundaries

Coverage excludes `desktop.py` and the tiny module launcher; it is not whole-product or branch
coverage. Process-exit tests are not power-loss tests. Synthetic fixtures are not a disk-size
benchmark. The test suite runs only in temporary/demo folders, never user documents.
