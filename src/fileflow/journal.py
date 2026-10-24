"""SQLite write-ahead operation journal and explicit recovery.

The filesystem batch is not atomic. Every intended operation is durable before it runs;
crashes can be resumed without overwriting unrelated files. This is not a backup system.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from fileflow.errors import FileFlowError
from fileflow.models import Plan, now
from fileflow.safety import Identity, checked_path, exclusive_move, root_path


@contextmanager
def folder_lock(root: Path):
    directory = checked_path(root, ".fileflow", private=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    path = checked_path(root, ".fileflow/lock", private=True)
    if path.exists() and path.stat().st_nlink != 1:
        raise FileFlowError("Journal lock must not be hard-linked")
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise FileFlowError("Another FileFlow operation is using this folder") from exc
        try:
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def database(root: Path):
    path = checked_path(root, ".fileflow/history.sqlite3", private=True)
    if path.exists() and path.stat().st_nlink != 1:
        raise FileFlowError("Journal database must not be hard-linked")
    # SQLite's companion paths must not be redirectable through existing symlinks.
    for suffix in ("-journal", "-wal", "-shm"):
        checked_path(root, f".fileflow/history.sqlite3{suffix}", private=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL,
                state TEXT NOT NULL, direction TEXT NOT NULL, digest TEXT NOT NULL,
                error TEXT, count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
                run_id TEXT NOT NULL, position INTEGER NOT NULL,
                source TEXT NOT NULL, destination TEXT NOT NULL,
                identity TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY (run_id, position)
            );
        """)
        yield connection
    finally:
        connection.close()


def _update(db, run_id: str, *, state: str, direction: str, error: str | None = None):
    db.execute(
        "UPDATE runs SET state=?,direction=?,error=?,updated=? WHERE id=?",
        (state, direction, error, now(), run_id),
    )
    db.commit()


def _execute(root: Path, db, run_id: str, direction: str) -> str:
    reverse = direction == "undo"
    rows = db.execute(
        "SELECT * FROM operations WHERE run_id=? ORDER BY position "
        + ("DESC" if reverse else "ASC"),
        (run_id,),
    ).fetchall()
    _update(db, run_id, state="undoing" if reverse else "applying", direction=direction)
    try:
        for row in rows:
            if row["state"] == ("undone" if reverse else "moved"):
                continue
            identity = Identity(**json.loads(row["identity"]))
            src, dst = row["source"], row["destination"]
            if reverse:
                original = checked_path(root, src)
                target = checked_path(root, dst)
                if row["state"] == "pending" and original.exists():
                    identity.check(original)
                    # An external collision may have prevented this operation entirely.
                    # Do not touch that external file while reversing earlier operations.
                    if not target.exists() or not os.path.samefile(original, target):
                        db.execute(
                            "UPDATE operations SET state='undone' WHERE run_id=? AND position=?",
                            (run_id, row["position"]),
                        )
                        db.commit()
                        continue
                src, dst = dst, src
            exclusive_move(root, src, dst, identity)
            db.execute(
                "UPDATE operations SET state=? WHERE run_id=? AND position=?",
                ("undone" if reverse else "moved", run_id, row["position"]),
            )
            db.commit()
    except (FileFlowError, OSError, sqlite3.Error) as exc:
        _update(db, run_id, state="partial", direction=direction, error=str(exc))
        raise FileFlowError(
            f"Run {run_id} stopped safely: {exc}. Recover or undo this run before applying a new plan."
        ) from exc
    _update(db, run_id, state="undone" if reverse else "applied", direction=direction)
    return run_id


def apply(plan: Plan) -> str:
    root = root_path(plan.root)
    if not plan.moves:
        raise FileFlowError("Plan has no file changes")
    with folder_lock(root), database(root) as db:
        unfinished = db.execute(
            "SELECT id FROM runs WHERE state IN ('applying','undoing','partial') LIMIT 1"
        ).fetchone()
        if unfinished:
            raise FileFlowError(f"Run {unfinished['id']} needs recovery or undo first")
        source_keys, dest_keys = set(), set()
        for move in plan.moves:
            source = checked_path(root, move.source)
            target = checked_path(root, move.destination)
            if (
                move.source == move.destination
                or move.source.casefold() in source_keys
                or move.destination.casefold() in dest_keys
            ):
                raise FileFlowError("Plan contains duplicate or equal source/destination paths")
            source_keys.add(move.source.casefold())
            dest_keys.add(move.destination.casefold())
            move.identity.check(source)
            if target.exists():
                raise FileFlowError(f"Destination appeared since preview: {move.destination}")
        if source_keys & dest_keys:
            raise FileFlowError("Rename chains/cycles are not accepted")
        run_id = uuid.uuid4().hex[:12]
        timestamp = now()
        db.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
            (run_id, timestamp, timestamp, "applying", "apply", plan.digest, None, len(plan.moves)),
        )
        db.executemany(
            "INSERT INTO operations VALUES (?,?,?,?,?,?)",
            (
                (
                    run_id,
                    i,
                    move.source,
                    move.destination,
                    json.dumps(move.identity.to_dict()),
                    "pending",
                )
                for i, move in enumerate(plan.moves)
            ),
        )
        db.commit()
        return _execute(root, db, run_id, "apply")


def history(root: str | Path) -> list[dict]:
    root = root_path(root)
    # Opening History must not create state in a previously untouched folder.
    path = checked_path(root, ".fileflow/history.sqlite3", private=True)
    if not path.exists():
        return []
    with folder_lock(root), database(root) as db:
        rows = db.execute(
            "SELECT r.*, SUM(o.state='moved') AS moved, SUM(o.state='undone') AS undone FROM runs r LEFT JOIN operations o ON r.id=o.run_id GROUP BY r.id ORDER BY r.created DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def resume(root: str | Path, run_id: str = "latest", *, undo: bool = False) -> str:
    root = root_path(root)
    if not checked_path(root, ".fileflow/history.sqlite3", private=True).exists():
        raise FileFlowError("This folder has no FileFlow history")
    with folder_lock(root), database(root) as db:
        if run_id == "latest":
            row = db.execute(
                "SELECT * FROM runs WHERE state != 'undone' ORDER BY created DESC LIMIT 1"
            ).fetchone()
        else:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise FileFlowError("No matching run to recover or undo")
        if row["state"] == "undone":
            raise FileFlowError("This run was already undone")
        if not undo and row["state"] == "applied":
            raise FileFlowError("Run is already applied; use undo to reverse it")
        direction = "undo" if undo else row["direction"]
        return _execute(root, db, row["id"], direction)
