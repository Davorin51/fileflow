from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from fileflow.errors import FileFlowError
from fileflow.models import Move, Plan, Settings, now
from fileflow.safety import Identity, checked_path, component, root_path

TEMP_EXTENSIONS = {".part", ".tmp", ".crdownload"}


def scan(root: Path, settings: Settings) -> tuple[list[Path], list[tuple[str, str]]]:
    files, skipped = [], []

    def walk_error(error: OSError) -> None:
        raise FileFlowError(f"Cannot scan folder: {error}")

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(directory)
        dirs[:] = (
            sorted(
                d
                for d in dirs
                if (
                    d.casefold() not in {".fileflow", settings.destination.casefold()}
                    and (settings.include_hidden or not d.startswith("."))
                    and not (base / d).is_symlink()
                )
            )
            if settings.recursive
            else []
        )
        for name in sorted(names, key=lambda n: (n.casefold(), n)):
            path = base / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                skipped.append((rel, "Symbolic link; left unchanged"))
            elif not path.is_file():
                skipped.append((rel, "Not a regular file"))
            elif name.startswith(".") and not settings.include_hidden:
                continue
            elif path.suffix.lower() in TEMP_EXTENSIONS:
                skipped.append((rel, "Temporary/incomplete download"))
            else:
                try:
                    checked_path(root, rel)
                except FileFlowError as exc:
                    skipped.append((rel, str(exc)))
                else:
                    files.append(path)
    return sorted(
        files, key=lambda p: (p.relative_to(root).as_posix().casefold(), p.as_posix())
    ), skipped


def make_plan(
    root: str | Path, settings: Settings | None = None, *, only: set[str] | None = None
) -> Plan:
    root = root_path(root)
    settings = settings or Settings()
    files, skipped = scan(root, settings)
    moves = []
    reserved = {p.relative_to(root).as_posix().casefold() for p in files}

    # Include all existing targets, including directories and differently cased names.
    def exists_casefold(value: str) -> bool:
        parent = root
        for piece in Path(value).parts:
            if not parent.exists():
                return False
            if not parent.is_dir():
                raise FileFlowError(f"Destination parent is not a directory: {parent.name}")
            matches = [
                child for child in parent.iterdir() if child.name.casefold() == piece.casefold()
            ]
            if not matches:
                return False
            parent = matches[0]
            if parent.is_symlink():
                raise FileFlowError(f"Destination contains a symbolic link: {parent.name}")
            if parent.name != piece and parent.is_dir():
                raise FileFlowError(f"Destination directory differs only in case: {parent.name}")
        return True

    for path in files:
        source = path.relative_to(root).as_posix()
        if only is not None and source not in only:
            continue
        identity = Identity.read(path)
        date = datetime.fromtimestamp(identity.mtime_ns / 1e9, UTC)
        name = path.name
        if settings.rename_template:
            name = settings.rename_template.format(
                stem=path.stem,
                suffix=path.suffix,
                index=len(moves) + 1,
                date=date.strftime("%Y-%m-%d"),
            )
            component(name)
        if settings.group_by == "none":
            folder = path.parent.relative_to(root)
        elif settings.group_by == "date":
            folder = Path(settings.destination, date.strftime("%Y"), date.strftime("%m"))
        else:
            category = next(
                (
                    name
                    for name, ext in settings.categories
                    if path.suffix.lower() in {e.lower() for e in ext}
                ),
                "Other",
            )
            folder = Path(settings.destination, category)
        target = (folder / name).as_posix()
        if target == source:
            skipped.append((source, "Already matches the selected rule"))
            continue
        count = 2
        while target.casefold() in reserved or exists_casefold(target):
            candidate = Path(name)
            target = (folder / f"{candidate.stem} ({count}){candidate.suffix}").as_posix()
            count += 1
        checked_path(root, target)
        reserved.add(target.casefold())
        moves.append(Move(source, target, identity))
    return Plan(str(root), settings, tuple(moves), tuple(skipped), now())


def duplicates(root: str | Path, settings: Settings | None = None) -> list[dict]:
    root = root_path(root)
    files, _ = scan(root, settings or Settings())
    sizes = defaultdict(list)
    for path in files:
        sizes[path.stat().st_size].append(path)
    groups = []
    for size, candidates in sorted(sizes.items()):
        if len(candidates) < 2:
            continue
        hashes = defaultdict(list)
        for path in candidates:
            hashes[Identity.read(path).sha256].append(path.relative_to(root).as_posix())
        for digest, names in sorted(hashes.items()):
            if len(names) > 1:
                groups.append(
                    {
                        "sha256": digest,
                        "size": size,
                        "files": sorted(names),
                        "redundant_bytes": size * (len(names) - 1),
                    }
                )
    return groups
