# File operation contract

## Invariants tested

- Preview and duplicate review do not move files or create operation history.
- New destinations are created exclusively. Existing files are never replaced.
- Every source is checked against the reviewed size, mtime, device, inode and SHA-256.
- Symbolic links and relative path escapes are rejected; `.fileflow` cannot be a move target.
- An input/config change requires a fresh preview in the desktop UI.
- Undo verifies the same original identity and refuses a conflicting new file at the old path.
- On interruption, the journal and actual file names determine recoverability; a run is not
  marked applied merely because the API request started.
- Two FileFlow execution processes cannot hold the folder lock simultaneously.
- Duplicate results are read-only and never initiate deletion.

## Supported environment

An ordinary local folder controlled by the user, on a single filesystem supporting hard links.
Hard-link failures (including EXDEV and permissions) preserve the source. NTFS/ext4/APFS are
examples of suitable filesystem families, not a guarantee for every mounted provider/version.
Readonly file attributes and directory permissions may require user intervention before retry.

Symlink checks and multiple identity checks do not make this a security sandbox. Another
hostile or privileged process can race directory/file changes between checks. Do not run the
app with elevated privileges against folders writable by untrusted users. Cloud sync, network
mounts, power-loss and storage-driver behavior are outside the verified operating envelope.

## Interruption cases

| Source name | Destination name | Recovery |
| --- | --- | --- |
| Original exists | Missing | Attempt exclusive link, verify, then unlink old name |
| Missing | Expected original inode/content | Recognize completed move and commit progress |
| Both point to expected original inode/content | Both exist | Finish unlinking the old name |
| Different file/content, or both missing | Conflicting/incomplete | Stop, record partial state and require resolution |

Undo uses the opposite direction, in reverse order. If a pending operation never happened
because an unrelated target appeared, undo leaves that unrelated target alone and reverses
previous completed operations. Empty directories are intentionally retained.

## Privacy

Selected file paths and fingerprints are stored locally in plan JSON and `.fileflow/history.sqlite3`.
The app has no upload endpoint or telemetry. A plan checksum is integrity checking for accidental
edits, not authentication; do not execute unreviewed plans from another source. The journal is
not encrypted and is not a replacement for a backup.

## Deliberate first-version restrictions

No duplicate deletion, source-overwrite policy, replace-in-place operation, chained rename cycles,
file permission rewriting, automatic cleanup of unknown partial files or cross-volume copy fallback.
Those behaviors would need separate designs and tests before being enabled.
