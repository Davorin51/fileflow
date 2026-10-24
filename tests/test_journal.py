import errno
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from fileflow.errors import FileFlowError
from fileflow.journal import apply, folder_lock, history, resume
from fileflow.models import Settings
from fileflow.planner import make_plan
from fileflow.safety import Identity, exclusive_move


def sample(folder):
    for name in ["alpha.txt", "beta.csv", "gamma.md"]:
        (folder / name).write_text(f"original {name}")
    return make_plan(folder)


def test_apply_history_and_undo_preserve_every_byte(folder):
    plan = sample(folder)
    before = {m.source: (folder / m.source).read_bytes() for m in plan.moves}
    run = apply(plan)
    assert history(folder)[0]["state"] == "applied"
    assert history(folder)[0]["moved"] == 3
    for move in plan.moves:
        assert not (folder / move.source).exists()
        assert (folder / move.destination).read_bytes() == before[move.source]
    assert resume(folder, run, undo=True) == run
    assert history(folder)[0]["state"] == "undone"
    for name, data in before.items():
        assert (folder / name).read_bytes() == data
    with pytest.raises(FileFlowError, match="already undone"):
        resume(folder, run, undo=True)


def test_history_does_not_create_metadata(folder):
    assert history(folder) == []
    assert list(folder.iterdir()) == []
    with pytest.raises(FileFlowError, match="no FileFlow history"):
        resume(folder)


def test_changed_source_rejects_entire_plan_before_any_move(folder):
    plan = sample(folder)
    (folder / "beta.csv").write_text("changed after preview")
    with pytest.raises(FileFlowError, match="changed since preview"):
        apply(plan)
    assert all((folder / move.source).exists() for move in plan.moves)
    assert history(folder) == []


def test_new_destination_is_never_overwritten(folder):
    plan = sample(folder)
    target = folder / plan.moves[0].destination
    target.parent.mkdir(parents=True)
    target.write_text("an external file")
    with pytest.raises(FileFlowError, match="appeared"):
        apply(plan)
    assert target.read_text() == "an external file"
    assert (folder / plan.moves[0].source).exists()


def test_undo_refuses_modified_files(folder):
    plan = sample(folder)
    run = apply(plan)
    changed = folder / plan.moves[-1].destination
    changed.write_text("keep my new edits")
    with pytest.raises(FileFlowError, match="changed since preview"):
        resume(folder, run, undo=True)
    assert changed.read_text() == "keep my new edits"
    assert history(folder)[0]["state"] == "partial"
    with pytest.raises(FileFlowError, match="recovery or undo"):
        apply(make_plan(folder, Settings(recursive=True))) if make_plan(
            folder, Settings(recursive=True)
        ).moves else apply(plan)


def test_undo_does_not_overwrite_new_file_at_original_name(folder):
    plan = sample(folder)
    run = apply(plan)
    source = folder / plan.moves[-1].source
    source.write_text("new user file")
    with pytest.raises(FileFlowError):
        resume(folder, run, undo=True)
    assert source.read_text() == "new user file"
    assert (folder / plan.moves[-1].destination).exists()


@pytest.mark.parametrize("when", ["after_link", "after_unlink"])
def test_recovery_after_interruption_in_link_unlink_window(folder, monkeypatch, when):
    plan = sample(folder)
    original_unlink = Path.unlink
    fired = False

    def interrupted(path, *args, **kwargs):
        nonlocal fired
        if path == folder / plan.moves[0].source and not fired:
            fired = True
            if when == "after_unlink":
                original_unlink(path, *args, **kwargs)
            raise OSError("simulated process interruption")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupted)
    with pytest.raises(FileFlowError, match="stopped safely"):
        apply(plan)
    rows = history(folder)
    assert rows[0]["state"] == "partial"
    run = rows[0]["id"]
    monkeypatch.setattr(Path, "unlink", original_unlink)
    resume(folder, run)
    assert history(folder)[0]["state"] == "applied"
    assert all(
        (folder / m.destination).exists() and not (folder / m.source).exists() for m in plan.moves
    )
    resume(folder, run, undo=True)
    assert all((folder / m.source).exists() for m in plan.moves)


def test_undo_partial_run_leaves_external_collision_intact(folder, monkeypatch):
    plan = sample(folder)
    link = os.link
    collision = folder / plan.moves[1].destination

    def injected(src, dst, **kwargs):
        if Path(dst) == collision:
            collision.write_text("external collision")
        return link(src, dst, **kwargs)

    monkeypatch.setattr(os, "link", injected)
    with pytest.raises(FileFlowError):
        apply(plan)
    run = history(folder)[0]["id"]
    monkeypatch.setattr(os, "link", link)
    resume(folder, run, undo=True)
    assert history(folder)[0]["state"] == "undone"
    assert collision.read_text() == "external collision"
    assert all((folder / m.source).exists() for m in plan.moves)


def test_recovery_when_both_names_exist_checks_same_inode(folder):
    source = folder / "a.txt"
    source.write_text("original")
    identity = Identity.read(source)
    target = folder / "b.txt"
    os.link(source, target)
    exclusive_move(folder, "a.txt", "b.txt", identity)
    assert not source.exists()
    assert target.read_text() == "original"
    exclusive_move(folder, "a.txt", "b.txt", identity)  # idempotent completion
    target.unlink()
    with pytest.raises(FileFlowError, match="Both"):
        exclusive_move(folder, "a.txt", "b.txt", identity)


def test_cross_device_or_unsupported_hardlinks_fail_without_removing_source(folder, monkeypatch):
    plan = sample(folder)

    def unavailable(*args, **kwargs):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", unavailable)
    with pytest.raises(FileFlowError, match="hard links"):
        apply(plan)
    assert all((folder / m.source).exists() for m in plan.moves)


def test_duplicate_lock_prevents_concurrent_application(folder):
    program = """
import sys
from fileflow.journal import folder_lock
from fileflow.errors import FileFlowError
from pathlib import Path
try:
    with folder_lock(Path(sys.argv[1])):
        sys.exit(3)
except FileFlowError:
    sys.exit(0)
"""
    with folder_lock(folder):
        result = subprocess.run([sys.executable, "-c", program, str(folder)], timeout=15)
        assert result.returncode == 0


def test_noop_and_unknown_run_errors(folder):
    with pytest.raises(FileFlowError, match="no file changes"):
        apply(make_plan(folder))
    plan = sample(folder)
    run = apply(plan)
    with pytest.raises(FileFlowError, match="already applied"):
        resume(folder, run)
    with pytest.raises(FileFlowError, match="No matching"):
        resume(folder, "does-not-exist", undo=True)
    resume(folder, undo=True)
    with pytest.raises(FileFlowError, match="No matching"):
        resume(folder, undo=True)


def test_malicious_private_path_is_blocked_even_for_in_memory_plan(folder):
    plan = sample(folder)
    move = replace(plan.moves[0], destination=".fileflow/lock")
    with pytest.raises(FileFlowError):
        apply(replace(plan, moves=(move,)))
    assert (folder / plan.moves[0].source).exists()


@pytest.mark.parametrize("phase", ["after_link", "after_unlink"])
def test_actual_process_exit_releases_lock_and_preserves_recovery(folder, tmp_path, phase):
    plan = sample(folder)
    saved = tmp_path / "plan.json"
    plan.save(saved)
    program = """
import os, sys
from pathlib import Path
from fileflow.models import Plan
from fileflow.journal import apply
plan = Plan.load(Path(sys.argv[1]))
original = Path.unlink
def abrupt(path, *args, **kwargs):
    if path == Path(plan.root) / plan.moves[0].source:
        if sys.argv[2] == 'after_unlink':
            original(path, *args, **kwargs)
        os._exit(77)
    return original(path, *args, **kwargs)
Path.unlink = abrupt
apply(plan)
"""
    result = subprocess.run([sys.executable, "-c", program, str(saved), phase], timeout=15)
    assert result.returncode == 77
    assert history(folder)[0]["state"] == "applying"
    run = resume(folder)
    assert history(folder)[0]["state"] == "applied"
    resume(folder, run, undo=True)
    assert all((folder / m.source).read_text() == f"original {m.source}" for m in plan.moves)
