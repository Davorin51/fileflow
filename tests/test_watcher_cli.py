import json

import pytest

from fileflow.cli import main
from fileflow.models import Settings
from fileflow.watcher import Watcher


def test_watcher_only_emits_stable_new_files_and_handles_reused_names(folder):
    (folder / "existing.txt").write_text("existing")
    watcher = Watcher(folder, Settings(), settle_seconds=2)
    assert watcher.poll(0) == set()
    file = folder / "new.txt"
    file.write_text("new")
    assert watcher.poll(1) == set()
    file.write_text("still downloading")
    assert watcher.poll(2) == set()
    assert watcher.poll(3) == set()
    assert watcher.poll(4) == {"new.txt"}
    watcher.acknowledge({"new.txt"})
    assert watcher.poll(5) == set()
    file.unlink()
    assert watcher.poll(6) == set()
    file.write_text("another download with the same name")
    assert watcher.poll(7) == set()
    assert watcher.poll(9) == {"new.txt"}


def test_watcher_vanished_and_changed_files(folder):
    watcher = Watcher(folder, Settings(), settle_seconds=0)
    file = folder / "temp.txt"
    file.write_text("a")
    watcher.poll(0)
    file.unlink()
    assert watcher.poll(1) == set()
    assert not watcher.pending
    for value in [-1, float("inf"), float("nan")]:
        with pytest.raises(ValueError):
            Watcher(folder, Settings(), value)
    with pytest.raises(ValueError):
        Watcher(folder, Settings(group_by="none"))


def test_cli_demo_preview_apply_history_undo(tmp_path, capsys):
    root = tmp_path / "demo"
    saved = tmp_path / "preview.json"
    assert main(["demo", str(root)]) == 0
    capsys.readouterr()
    assert main(["preview", str(root), "--save", str(saved)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["moves"]) == 7
    assert not (root / ".fileflow").exists()
    assert main(["duplicates", str(root)]) == 0
    assert len(json.loads(capsys.readouterr().out)["groups"]) == 1
    assert main(["apply", str(saved), "--yes"]) == 0
    run = json.loads(capsys.readouterr().out)["run"]
    assert main(["history", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["state"] == "applied"
    assert main(["undo", str(root), "--run", run, "--yes"]) == 0
    assert (root / "meeting_notes.txt").exists()
    assert (root / "download.crdownload").exists()
    capsys.readouterr()
    assert main(["demo", str(root)]) == 2
    assert "exists" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["watch", "--interval", "0"],
        ["watch", "--interval", "inf"],
        ["watch", "--settle", "nan"],
        ["watch", "--settle", "-1"],
        ["watch", "--iterations", "0"],
        ["watch", "--execute"],
    ],
)
def test_cli_watch_rejects_unsafe_options(folder, capsys, arguments):
    assert main([arguments[0], str(folder), *arguments[1:]]) == 2
    assert capsys.readouterr().err


def test_cli_watch_bounded_preview_and_explicit_auto_apply(folder, monkeypatch, capsys):
    original = Watcher.poll
    calls = 0

    def new_file(self, instant):
        nonlocal calls
        calls += 1
        if calls % 2 == 1:
            (folder / "added.txt").write_text("new file")
        return original(self, instant)

    monkeypatch.setattr(Watcher, "poll", new_file)
    monkeypatch.setattr("fileflow.cli.time.sleep", lambda _: None)
    assert main(["watch", str(folder), "--settle", "0", "--iterations", "2"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "preview_only"
    assert (folder / "added.txt").exists()
    (folder / "added.txt").unlink()
    calls = 0
    assert (
        main(["watch", str(folder), "--settle", "0", "--iterations", "2", "--execute", "--yes"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "automatic_apply"
    assert (folder / "Organized/Documents/added.txt").exists()


def test_cli_config_and_errors(folder, tmp_path, capsys):
    (folder / "a.txt").write_text("a")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"group_by": "none", "rename_template": "renamed_{stem}{suffix}"}))
    assert main(["preview", str(folder), "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["moves"][0]["destination"] == "renamed_a.txt"
    missing = tmp_path / "missing.json"
    assert main(["apply", str(missing), "--yes"]) == 2
    config.write_text("{bad json")
    assert main(["preview", str(folder), "--config", str(config)]) == 2
    assert main(["recover", str(folder), "--yes"]) == 2
    with pytest.raises(SystemExit):
        main(["apply", str(missing)])  # confirmation cannot be silently omitted


def test_cli_interrupt_is_explicit(folder, monkeypatch):
    monkeypatch.setattr(
        "fileflow.cli.make_plan", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    assert main(["preview", str(folder)]) == 130
