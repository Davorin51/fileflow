# Architecture

FileFlow is a Python package with a dependency-free standard-library core and an optional Qt
Widgets frontend. It deliberately has no API server, account service or cloud database.

## Data flow

1. `Settings` validates portable category names, unique extension rules and a bounded template grammar.
2. `scan` enumerates regular files in the selected root, excluding state/organized outputs and
   symlinks. Traversal is opt-in; no home-directory or disk-wide scanning occurs implicitly.
3. `make_plan` computes streamed SHA-256 identities and exact destinations. Numbered conflict
   alternatives are chosen in the preview. A move records source, destination and original identity.
4. `apply` obtains a cross-process folder lock, validates every source/destination, then commits
   the planned operation rows before starting any move.
5. Per-operation progress is committed individually. A batch can be partially complete, which
   is visible in history rather than disguised as success.
6. `resume` finishes the recorded direction or reverses operations in reverse order. It never
   assumes that a saved score/count proves a file operation happened.

## Why hard-link then unlink?

Plain `os.rename` can replace an existing file on POSIX, so a check-then-rename is insufficient
for the no-overwrite contract. Creating a new hard link fails if a destination exists. Original
inode identity is preserved and makes interrupted two-name states recognizable. The trade-off
is a same-filesystem, hard-link-capable storage requirement. There is no hidden cross-device
copy fallback and no hard-link-based disk-space saving claim for duplicate groups.

The operation is recoverable under tested process-exit scenarios, not an atomic batch or a
power-failure protocol. The source and new name can coexist transiently. Directory fsync,
storage-provider semantics and hostile directory races remain outside the current claim.

## Journal states

Runs transition through `applying` → `applied`, or `partial` on an error. Reversal uses
`undoing` → `undone`. A partial run retains its intended direction. Operation rows are
`pending`, `moved` or `undone`, and are reconciled against actual file identities on retry.
The persistent lock file uses an OS advisory lock; process termination releases the lock
without requiring a dangerous "delete a stale lock" workflow.

## UI boundaries

The native Qt window uses a worker pool for hashing and operations. Rule changes invalidate
previews. Apply/undo/recover require an explicit confirmation dialog; task failures appear in
the status area. Tables and counters come from actual plans/duplicate groups/journal rows.
Desktop watching previews only. CLI automatic watching requires two explicit opt-in flags.

The watcher accepts a monotonic timestamp, making settle/replacement behavior testable without
sleeping in tests. A watcher is not a reliable signal that a downloading application has closed
its file; execution must still revalidate the identity.

## Current limits

No installer/signing, undo after arbitrary content changes, cross-device moves, deletion,
network shares, file-content categorization, EXIF dates or untrusted multi-user security boundary.
These are documented limits, not placeholder features represented as finished.
