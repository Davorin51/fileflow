# Roadmap

## Implemented first version

Preview, category/date/rename rules, read-only duplicates, same-filesystem journaled moves,
undo/recovery, CLI, optional native Qt frontend, stable-file watcher and automated verification.
The desktop watcher previews only; CLI execution requires explicit opt-in.

## Next milestones

| Work | Acceptance criteria | Estimate |
| --- | --- | --- |
| Windows packaging | Reproducible executable build, clean-machine smoke test, third-party notices and signing guidance | 4–7 h |
| Desktop validation | Native Windows/Linux display, high DPI, keyboard-only and screen-reader checks; fix confirmed issues | 4–6 h |
| Rule editor | UI category mapping editor, validated JSON import/export, examples and tests | 3–5 h |
| Review selection | Per-file selection with immutable filtered plans and accurate preview totals | 3–5 h |
| Large folders | Measure actual scan/hash behavior, cancellation, progress signals and bounded result display | 4–7 h |
| Journal versions | Explicit schema/version migrations and corrupt/unsupported journal diagnostics | 3–5 h |
| Cross-device design | Specify copy staging, verification, durable ownership markers and failure recovery before implementation | 5–9 h |
| macOS validation | Actual APFS and Qt UI checks; only then claim supported packaging | 3–5 h |

These are planning ranges, not logged hours or completed features. Cross-device moves and
optional deletion require a separate reviewed contract; no destructive feature is promised by default.
