"""Path validation and content identities for ordinary, trusted local folders.

These checks are not a sandbox against hostile processes racing directory replacements.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from fileflow.errors import FileFlowError

PRIVATE = ".fileflow"
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
}


def component(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or value[-1:] in {".", " "}
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
        or value.split(".", 1)[0].upper() in RESERVED
        or len(value.encode("utf-8")) > 240
    ):
        raise FileFlowError(f"Not a portable filename: {value!r}")
    return value


def relative(value: str, *, private: bool = False) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise FileFlowError("Expected a relative path with forward slashes")
    result = PurePosixPath(value)
    if result.is_absolute() or value != result.as_posix():
        raise FileFlowError(f"Non-canonical relative path: {value!r}")
    for part in result.parts:
        component(part)
    if not private and any(part.casefold() == PRIVATE for part in result.parts):
        raise FileFlowError("FileFlow's journal directory cannot be organized")
    return result


def root_path(value: str | Path) -> Path:
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileFlowError(f"Cannot open source folder: {exc}") from exc
    if not root.is_dir():
        raise FileFlowError("Select a directory, not a file")
    return root


def checked_path(root: Path, value: str, *, private: bool = False) -> Path:
    path = root
    for part in relative(value, private=private).parts:
        path = path / part
        if path.is_symlink():
            raise FileFlowError(f"Symbolic links are not supported: {value}")
    try:
        if not path.resolve().is_relative_to(root):
            raise FileFlowError(f"Path escapes selected folder: {value}")
    except (OSError, RuntimeError) as exc:
        raise FileFlowError(f"Cannot resolve path: {value}") from exc
    return path


@dataclass(frozen=True)
class Identity:
    size: int
    mtime_ns: int
    device: int
    inode: int
    sha256: str

    @classmethod
    def read(cls, path: Path) -> Identity:
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise FileFlowError(f"Not a regular file: {path.name}")
            digest = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise FileFlowError(f"File changed while opening: {path.name}")
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                after = os.fstat(stream.fileno())

            def keys(s):
                return (s.st_size, s.st_mtime_ns, s.st_dev, s.st_ino)

            if keys(before) != keys(after):
                raise FileFlowError(f"File changed while reading: {path.name}")
            return cls(*keys(after), digest.hexdigest())
        except OSError as exc:
            raise FileFlowError(f"Cannot read {path.name}: {exc}") from exc

    def check(self, path: Path) -> None:
        if self != Identity.read(path):
            raise FileFlowError(f"File changed since preview: {path.name}. Create a fresh plan.")

    def to_dict(self) -> dict:
        return asdict(self)


def exclusive_move(root: Path, source: str, destination: str, identity: Identity) -> None:
    """Recoverable same-filesystem hard-link + unlink; never replaces a destination.

    An interrupted link/unlink can leave both names. A retry accepts that only when both
    point to the expected original inode and content. No cross-device copy fallback exists.
    """
    src, dst = checked_path(root, source), checked_path(root, destination)
    if src == dst:
        raise FileFlowError("Source and destination must differ")
    source_exists, dest_exists = src.exists(), dst.exists()
    if not source_exists:
        if dest_exists:
            identity.check(dst)
            return  # unlink completed before the journal commit
        raise FileFlowError(f"Both source and destination are missing: {source}")
    identity.check(src)
    if dest_exists:
        identity.check(dst)
        if not os.path.samefile(src, dst):
            raise FileFlowError(f"Destination already exists: {destination}")
    else:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            checked_path(root, destination)
            os.link(src, dst, follow_symlinks=False)
        except OSError as exc:
            raise FileFlowError(
                f"Cannot create destination {destination}: {exc}. "
                "A local filesystem supporting hard links is required; source was retained."
            ) from exc
    identity.check(dst)
    identity.check(src)
    try:
        src.unlink()
    except OSError as exc:
        raise FileFlowError(f"Could not remove old name {source}: {exc}. Use recovery.") from exc
