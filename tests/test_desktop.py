import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
# ruff: noqa: E402 -- optional dependency must be checked before importing Qt
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from fileflow.desktop import MainWindow
from fileflow.journal import history


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(window):
    deadline = time.monotonic() + 15
    while window.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(5)
    QApplication.processEvents()
    assert not window.busy, "Desktop worker did not complete"


def test_real_desktop_preview_apply_and_undo(app, folder, monkeypatch):
    (folder / "notes.txt").write_text("original")
    window = MainWindow(folder)
    window.show()
    QTest.mouseClick(window.preview_button, Qt.MouseButton.LeftButton)
    wait(window)
    assert window.plan_table.rowCount() == 1
    assert window.numbers[0].text() == "1"
    assert window.apply_button.isEnabled()
    assert not (folder / ".fileflow").exists()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    QTest.mouseClick(window.apply_button, Qt.MouseButton.LeftButton)
    wait(window)
    assert (folder / "Organized/Documents/notes.txt").read_text() == "original"
    assert history(folder)[0]["state"] == "applied"
    assert window.undo_button.isEnabled()
    QTest.mouseClick(window.undo_button, Qt.MouseButton.LeftButton)
    wait(window)
    assert (folder / "notes.txt").read_text() == "original"
    assert history(folder)[0]["state"] == "undone"
    window.close()


def test_rule_change_invalidates_preview_and_cancel_does_not_move(app, folder, monkeypatch):
    (folder / "a.txt").write_text("a")
    window = MainWindow(folder)
    window.preview()
    wait(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    window.apply_plan()
    assert (folder / "a.txt").exists()
    window.rename.setChecked(True)
    assert window.plan is None
    assert not window.apply_button.isEnabled()
    window.close()


def test_empty_state_and_invalid_template_errors(app, folder):
    window = MainWindow()
    assert not window.preview_button.isEnabled()
    window.close()
    window = MainWindow(folder)
    window.rename.setChecked(True)
    window.template.setText("../{stem}")
    window.preview()
    assert "Could not complete" in window.status.text()
    assert not window.busy
    window.close()


def test_desktop_watcher_only_creates_previews(app, folder):
    window = MainWindow(folder)
    window.toggle_watch()
    wait(window)
    assert window.watcher is not None
    (folder / "new.txt").write_text("new")
    window.watcher.settle_seconds = 0
    window.watch_tick()
    wait(window)
    window.watch_tick()
    wait(window)
    assert window.plan and window.plan.moves
    assert (folder / "new.txt").exists()
    assert not (folder / ".fileflow").exists()
    window.toggle_watch()
    assert window.watcher is None
    window.close()
