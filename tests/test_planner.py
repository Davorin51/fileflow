import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fileflow.errors import FileFlowError
from fileflow.journal import apply, resume
from fileflow.models import Plan, Settings
from fileflow.planner import duplicates, make_plan
from fileflow.safety import Identity, checked_path, component, root_path


def test_preview_and_duplicate_scan_never_mutate_source(folder):
    (folder / "note.txt").write_text("same content")
    (folder / "copy.txt").write_text("same content")
    (folder / "other.txt").write_text("not the same")
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    plan = make_plan(folder)
    groups = duplicates(folder)
    assert len(plan.moves) == 3
    assert [g["files"] for g in groups] == [["copy.txt", "note.txt"]]
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert not (folder / ".fileflow").exists()


def test_collision_suffix_is_previewed_and_casefold_aware(folder):
    (folder / "photo.PNG").write_text("new")
    target = folder / "Organized/Images"
    target.mkdir(parents=True)
    (target / "PHOTO.png").write_text("existing")
    plan = make_plan(folder)
    assert plan.moves[0].destination == "Organized/Images/photo (2).PNG"
    run = apply(plan)
    assert (target / "PHOTO.png").read_text() == "existing"
    resume(folder, run, undo=True)
    assert (folder / "photo.PNG").read_text() == "new"


def test_date_means_modified_date_in_utc(folder):
    file = folder / "notes.txt"
    file.write_text("dated")
    os.utime(file, (1767225600, 1767225600))
    plan = make_plan(folder, Settings(group_by="date"))
    assert plan.moves[0].destination == "Organized/2026/01/notes.txt"


def test_recursive_and_hidden_policy(folder):
    (folder / "sub").mkdir()
    (folder / "sub/a.txt").write_text("a")
    (folder / ".secret").write_text("hidden")
    (folder / "unfinished.part").write_text("in progress")
    assert make_plan(folder).moves == ()
    recursive = make_plan(folder, Settings(recursive=True, include_hidden=True))
    assert {m.source for m in recursive.moves} == {"sub/a.txt", ".secret"}
    assert any("Temporary" in reason for _, reason in recursive.skipped)


def test_custom_category_and_rename_rules(folder):
    (folder / "sales.csv").write_text("a,b\n1,2")
    config = Settings(
        categories=(("Finance", (".csv",)),), rename_template="{index:03d}_{stem}{suffix}"
    )
    assert make_plan(folder, config).moves[0].destination == "Organized/Finance/001_sales.csv"
    loaded = Settings.from_dict(json.loads(json.dumps(config.to_dict())))
    assert loaded == config


def test_rename_only_and_noop(folder):
    (folder / "notes.txt").write_text("a")
    assert make_plan(folder, Settings(group_by="none")).moves == ()
    assert (
        make_plan(folder, Settings(group_by="none", rename_template="final_{stem}{suffix}"))
        .moves[0]
        .destination
        == "final_notes.txt"
    )


def test_empty_files_are_real_duplicate_groups(folder):
    for name in ["one.txt", "two.csv"]:
        (folder / name).touch()
    group = duplicates(folder)[0]
    assert group["size"] == 0
    assert group["redundant_bytes"] == 0


def test_symlinks_are_never_followed(folder, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    try:
        (folder / "shortcut.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation not available")
    plan = make_plan(folder, Settings(recursive=True))
    assert not plan.moves
    assert "Symbolic link" in plan.skipped[0][1]
    assert outside.read_text() == "keep"


def test_symlink_destination_is_rejected(folder, tmp_path):
    (folder / "a.txt").write_text("a")
    other = tmp_path / "outside"
    other.mkdir()
    try:
        (folder / "Organized").symlink_to(other, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not available")
    with pytest.raises(FileFlowError, match="symbolic link|Symbolic"):
        make_plan(folder)
    assert list(other.iterdir()) == []


def test_plan_json_roundtrip_and_checksum(folder, tmp_path):
    (folder / "a.txt").write_text("abc")
    plan = make_plan(folder)
    saved = tmp_path / "plan.json"
    plan.save(saved)
    assert Plan.load(saved) == plan
    with pytest.raises(FileExistsError):
        plan.save(saved)
    value = json.loads(saved.read_text())
    value["moves"][0]["destination"] = "Organized/Other/change.txt"
    saved.write_text(json.dumps(value))
    with pytest.raises(FileFlowError, match="checksum"):
        Plan.load(saved)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "../escape.txt"),
        ("destination", "/tmp/outside.txt"),
        ("destination", ".fileflow/history.sqlite3"),
        ("destination", "a\\b.txt"),
    ],
)
def test_malformed_plan_paths_are_rejected(folder, tmp_path, field, value):
    (folder / "a.txt").write_text("a")
    plan = make_plan(folder)
    saved = tmp_path / "bad.json"
    plan.save(saved)
    raw = json.loads(saved.read_text())
    raw["moves"][0][field] = value
    saved.write_text(json.dumps(raw))
    with pytest.raises(FileFlowError):
        Plan.load(saved)


@pytest.mark.parametrize(
    "name",
    ["../x", "a/b", "a\\b", "CON.txt", "NUL", "x:", "", "..", "a.", "a ", "\0", "COM1", "x" * 241],
)
def test_portable_path_components(name):
    with pytest.raises(FileFlowError):
        component(name)


@pytest.mark.parametrize(
    "template",
    [
        "{unknown}",
        "../{stem}",
        "{stem.__class__}",
        "{stem!r}",
        "{index:100000000d}",
        "{",
        "{stem:>10}",
        "",
    ],
)
def test_invalid_templates_fail_before_scanning(template):
    with pytest.raises(FileFlowError):
        Settings(rename_template=template)


def test_invalid_config_and_input(folder, tmp_path):
    with pytest.raises(FileFlowError):
        Settings(group_by="magic")
    with pytest.raises(FileFlowError):
        Settings(destination=".fileflow")
    with pytest.raises(FileFlowError):
        Settings(recursive="yes")
    with pytest.raises(FileFlowError):
        Settings(categories=(("A", (".txt",)), ("B", (".TXT",))))
    with pytest.raises(FileFlowError):
        Settings(categories=(("CON", (".x",)),))
    with pytest.raises(FileFlowError):
        Settings.from_dict({"unexpected": True})
    with pytest.raises(FileFlowError):
        root_path(tmp_path / "missing")
    file = folder / "a.txt"
    file.touch()
    with pytest.raises(FileFlowError):
        root_path(file)
    with pytest.raises(FileFlowError):
        Identity.read(folder)
    with pytest.raises(FileFlowError):
        checked_path(folder, "a/../b")


def test_plan_duplicate_sources_and_cycles_are_rejected(folder, tmp_path):
    (folder / "a.txt").write_text("a")
    plan = make_plan(folder)
    saved = tmp_path / "plan.json"
    plan.save(saved)
    raw = json.loads(saved.read_text())
    raw["moves"].append(raw["moves"][0])
    saved.write_text(json.dumps(raw))
    with pytest.raises(FileFlowError, match="Duplicate"):
        Plan.load(saved)
    invalid = replace(plan, moves=(plan.moves[0], plan.moves[0]))
    with pytest.raises(FileFlowError, match="duplicate"):
        apply(invalid)


@given(
    st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=12),
        min_size=1,
        max_size=15,
        unique=True,
    )
)
def test_generated_destinations_are_unique_and_relative(names):
    # Pure path invariants need no destructive operation or user directory access.
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name in names:
            (root / f"{name}.txt").write_text(name)
        plan = make_plan(root, Settings(rename_template="{index:03d}{suffix}"))
        targets = [move.destination.casefold() for move in plan.moves]
        assert len(targets) == len(set(targets))
        assert all(
            not Path(move.destination).is_absolute() and ".." not in Path(move.destination).parts
            for move in plan.moves
        )
