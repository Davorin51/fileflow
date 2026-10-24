"""Capture real offscreen Qt widgets backed by original synthetic files."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from fileflow.demo import create_demo
from fileflow.desktop import MainWindow
from fileflow.journal import history


def wait(window):
    deadline = time.monotonic() + 20
    while window.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()
    if window.busy:
        raise RuntimeError("Demo worker did not finish")


def main():
    app = QApplication.instance() or QApplication([])
    output = Path("docs/screenshots")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fileflow-demo-") as temporary:
        folder = create_demo(Path(temporary) / "Downloads")
        window = MainWindow(folder)
        window.show()
        app.processEvents()
        window.preview()
        wait(window)
        if window.plan is None or len(window.plan.moves) != 7:
            raise RuntimeError("Demo preview did not match the generated fixture")
        counts = {
            "planned_moves": len(window.plan.moves),
            "duplicate_groups": int(window.numbers[1].text()),
            "skipped_files": len(window.plan.skipped),
        }
        if not window.grab().save(str(output / "preview.png")):
            raise RuntimeError("Could not save preview screenshot")
        window.tabs.setCurrentIndex(1)
        app.processEvents()
        window.grab().save(str(output / "duplicates.png"))
        # This capture harness explicitly authorizes moves only inside its disposable fixture.
        original = QMessageBox.question
        QMessageBox.question = lambda *args: QMessageBox.StandardButton.Yes
        try:
            window.apply_plan()
            wait(window)
            window.tabs.setCurrentIndex(2)
            app.processEvents()
            window.grab().save(str(output / "history.png"))
            if history(folder)[0]["state"] != "applied":
                raise RuntimeError("Real demo apply did not finish")
            window.reverse_run(True)
            wait(window)
            counts["final_run_state"] = history(folder)[0]["state"]
        finally:
            QMessageBox.question = original
        (output.parent / "demo-evidence.json").write_text(
            json.dumps(
                {
                    "mode": "actual_qt_widgets_offscreen",
                    "data": "original_synthetic_fixture",
                    **counts,
                },
                indent=2,
            )
            + "\n"
        )
        window.close()


if __name__ == "__main__":
    main()
