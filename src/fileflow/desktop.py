"""Optional native Qt desktop application; all filesystem logic is shared with the CLI."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fileflow.errors import FileFlowError
from fileflow.journal import apply, history, resume
from fileflow.models import Plan, Settings
from fileflow.planner import duplicates, make_plan
from fileflow.watcher import Watcher

STYLE = """
QWidget { background: #11151d; color: #e6ebf3; font-size: 14px; }
QMainWindow { background: #11151d; }
QFrame#sidebar { background: #171d28; border-right: 1px solid #2a3343; }
QFrame#sidebar QLabel, QFrame#sidebar QCheckBox { background: transparent; }
QLineEdit { font-family: monospace; font-size: 12px; }
QLabel#brand { font-size: 26px; font-weight: 700; color: #f5f8fc; }
QLabel#title { font-size: 27px; font-weight: 650; }
QLabel#muted { color: #a3b0c3; font-size: 13px; }
QLabel#section { color: #8ba1bb; font-size: 12px; font-weight: 650; }
QFrame#card { background: #1a2230; border: 1px solid #2a3649; border-radius: 8px; }
QLabel#number { background: transparent; font-size: 26px; font-weight: 650; color: #f5f8fc; }
QFrame#card QLabel { background: transparent; }
QPushButton { background: #263247; border: 1px solid #3d4d65; border-radius: 6px; padding: 10px 15px; }
QPushButton:hover { background: #30405a; border-color: #7792b3; }
QPushButton:focus { border: 2px solid #79dfcf; }
QPushButton:disabled { color: #738197; background: #1c2635; border-color: #2b3648; }
QPushButton#primary { background: #73ddcb; border-color: #73ddcb; color: #122823; font-weight: 700; }
QPushButton#primary:hover { background: #91e9db; }
QPushButton#primary:disabled { background: #294e4b; border-color: #294e4b; color: #8baca6; }
QLineEdit, QComboBox { background: #101722; border: 1px solid #42506a; border-radius: 5px; padding: 9px; }
QLineEdit:focus, QComboBox:focus { border-color: #79dfcf; }
QComboBox QAbstractItemView { background: #1c2635; selection-background-color: #304d59; }
QCheckBox { spacing: 8px; padding: 6px 0; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #63738a; border-radius: 3px; background: #111722; }
QCheckBox::indicator:checked { background: #73ddcb; border-color: #73ddcb; }
QTableWidget { background: #151c27; alternate-background-color: #192230; border: 1px solid #2b374a; gridline-color: #273245; selection-background-color: #28464d; }
QTableWidget::item { padding: 9px; }
QHeaderView::section { background: #202b3c; color: #bbcadb; padding: 10px; border: none; border-bottom: 1px solid #3a4a62; font-weight: 600; }
QTabWidget::pane { border: none; padding-top: 12px; }
QTabBar::tab { background: #17202d; color: #abb9ca; padding: 11px 18px; border-bottom: 2px solid #2c3a50; }
QTabBar::tab:selected { color: #83e3d3; border-bottom: 2px solid #83e3d3; }
QLabel#status { color: #bdc9d9; padding: 12px; border: 1px solid #334055; border-radius: 6px; background: #1a2331; }
QToolTip { color: #e9f2fc; background: #25334a; border: 1px solid #71869c; }
"""


def readable_size(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


class Signals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function, self.signals = function, Signals()

    def run(self):
        try:
            self.signals.result.emit(self.function())
        except Exception as exc:  # report worker failures to the user; never silently succeed
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self, folder: Path | None = None):
        super().__init__()
        self.setWindowTitle("FileFlow — preview-first file organization")
        self.resize(1280, 820)
        self.setMinimumSize(940, 650)
        self.folder = folder.resolve() if folder else None
        self.plan: Plan | None = None
        self.runs = []
        self.busy, self.watcher = False, None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.watch_tick)
        self.setStyleSheet(STYLE)
        canvas = QWidget()
        shell = QHBoxLayout(canvas)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.setCentralWidget(canvas)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(264)
        controls = QVBoxLayout(sidebar)
        controls.setContentsMargins(22, 28, 22, 24)
        controls.setSpacing(13)
        self.label(controls, "FileFlow", "brand")
        self.label(controls, "A little order. Full visibility.", "muted")
        controls.addSpacing(24)
        self.label(controls, "SOURCE FOLDER", "section")
        self.folder_label = self.label(
            controls, str(self.folder) if self.folder else "Choose a folder to begin", "muted"
        )
        self.folder_label.setWordWrap(True)
        self.browse = QPushButton("Choose folder…")
        self.browse.clicked.connect(self.choose_folder)
        controls.addWidget(self.browse)
        controls.addSpacing(14)
        self.label(controls, "ORGANIZATION", "section")
        self.rule = QComboBox()
        self.rule.addItems(["Group by file type", "Group by modified date", "Rename only"])
        self.rule.setAccessibleName("Organization rule")
        controls.addWidget(self.rule)
        self.recursive = QCheckBox("Include subfolders")
        controls.addWidget(self.recursive)
        self.rename = QCheckBox("Rename files")
        controls.addWidget(self.rename)
        self.template = QLineEdit("{index:03d}_{stem}{suffix}")
        self.template.setAccessibleName("Rename template")
        self.template.setToolTip(
            "Fields: {index:03d}, {stem}, {suffix}, {date}. No directory paths."
        )
        self.template.setEnabled(False)
        controls.addWidget(self.template)
        self.label(
            controls, "Names and destinations are always shown before applying.", "muted"
        ).setWordWrap(True)
        controls.addSpacing(12)
        self.watch_button = QPushButton("Watch new files")
        self.watch_button.setToolTip(
            "Desktop watcher creates previews only. It never applies changes automatically."
        )
        self.watch_button.clicked.connect(self.toggle_watch)
        controls.addWidget(self.watch_button)
        controls.addStretch()
        self.label(controls, "LOCAL BY DESIGN", "section")
        self.label(controls, "No account. No uploads.\nDuplicates are never deleted.", "muted")
        shell.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(28, 28, 28, 24)
        main.setSpacing(18)
        shell.addLayout(main, 1)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        self.label(heading, "Organize with confidence", "title")
        self.label(heading, "Review the plan. Apply it. Keep a way back.", "muted")
        header.addLayout(heading)
        header.addStretch()
        self.preview_button = QPushButton("Create preview")
        self.preview_button.clicked.connect(self.preview)
        header.addWidget(self.preview_button)
        main.addLayout(header)
        cards = QHBoxLayout()
        self.numbers = []
        for name in ["FILES TO MOVE", "DUPLICATE GROUPS", "LEFT UNCHANGED"]:
            card = QFrame()
            card.setObjectName("card")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(18, 15, 18, 15)
            self.label(layout, name, "section")
            self.numbers.append(self.label(layout, "—", "number"))
            cards.addWidget(card)
        main.addLayout(cards)
        self.tabs = QTabWidget()
        self.plan_table = self.table(["Current name", "Planned destination", "Size"])
        for column in [0, 2]:
            self.plan_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.duplicate_table = self.table(["Content group", "Copies", "Bytes each", "Files"])
        for column in [0, 1, 2]:
            self.duplicate_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.history_table = self.table(["Run ID", "State", "Files", "UTC timestamp"])
        self.skipped_table = self.table(["File", "Why it stays unchanged"])
        for title, widget in [
            ("Preview", self.plan_table),
            ("Duplicates", self.duplicate_table),
            ("History", self.history_table),
            ("Unchanged", self.skipped_table),
        ]:
            self.tabs.addTab(widget, title)
        self.history_table.itemSelectionChanged.connect(self.update_buttons)
        main.addWidget(self.tabs, 1)
        actions = QHBoxLayout()
        self.export_button = QPushButton("Export plan…")
        self.export_button.clicked.connect(self.export_plan)
        self.undo_button = QPushButton("Undo selected run")
        self.undo_button.clicked.connect(lambda: self.reverse_run(True))
        self.recover_button = QPushButton("Recover run")
        self.recover_button.clicked.connect(lambda: self.reverse_run(False))
        self.apply_button = QPushButton("Apply reviewed plan")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.apply_plan)
        for button in [self.export_button, self.undo_button, self.recover_button]:
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.apply_button)
        main.addLayout(actions)
        self.status = self.label(
            main, "Choose a folder and create a preview. No files have been changed.", "status"
        )
        self.status.setWordWrap(True)
        for widget in [self.rule, self.recursive, self.rename]:
            signal = widget.currentIndexChanged if isinstance(widget, QComboBox) else widget.toggled
            signal.connect(self.invalidate)
        self.rename.toggled.connect(
            lambda checked: self.template.setEnabled(checked and not self.busy)
        )
        self.template.textChanged.connect(self.invalidate)
        refresh = QAction("Create preview", self)
        refresh.setShortcut("Ctrl+R")
        refresh.triggered.connect(self.preview)
        self.addAction(refresh)
        self.update_buttons()

    @staticmethod
    def label(layout, text, name):
        widget = QLabel(text)
        widget.setObjectName(name)
        layout.addWidget(widget)
        return widget

    @staticmethod
    def table(columns):
        widget = QTableWidget(0, len(columns))
        widget.setHorizontalHeaderLabels(columns)
        widget.setAlternatingRowColors(True)
        widget.verticalHeader().hide()
        widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        widget.verticalHeader().setDefaultSectionSize(48)
        widget.setWordWrap(False)
        return widget

    @staticmethod
    def populate(table, rows):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                item = QTableWidgetItem(str(text))
                item.setToolTip(str(text))
                table.setItem(i, j, item)

    def settings(self):
        return Settings(
            group_by=["type", "date", "none"][self.rule.currentIndex()],
            rename_template=self.template.text() if self.rename.isChecked() else None,
            recursive=self.recursive.isChecked(),
        )

    def invalidate(self, *_):
        self.stop_watch()
        self.plan = None
        self.plan_table.setRowCount(0)
        self.numbers[0].setText("—")
        self.status.setText("Rules changed. Create a fresh preview before applying.")
        self.update_buttons()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a folder to organize", str(self.folder or Path.home())
        )
        if folder:
            self.folder = Path(folder)
            self.folder_label.setText(folder)
            self.invalidate()
            self.runs = []
            for table in [self.history_table, self.duplicate_table, self.skipped_table]:
                table.setRowCount(0)
            self.update_buttons()

    def selected_run(self):
        index = self.history_table.currentRow()
        return self.runs[index] if 0 <= index < len(self.runs) else None

    def update_buttons(self):
        if not hasattr(self, "apply_button"):
            return
        self.preview_button.setEnabled(bool(self.folder) and not self.busy)
        self.apply_button.setEnabled(bool(self.plan and self.plan.moves) and not self.busy)
        self.export_button.setEnabled(self.plan is not None and not self.busy)
        row = self.selected_run()
        self.undo_button.setEnabled(bool(row and row["state"] != "undone") and not self.busy)
        self.recover_button.setEnabled(
            bool(row and row["state"] in {"partial", "applying", "undoing"}) and not self.busy
        )
        self.watch_button.setEnabled(
            bool(self.folder) and self.rule.currentIndex() != 2 and not self.busy
        )
        for widget in [self.browse, self.rule, self.recursive, self.rename]:
            widget.setEnabled(not self.busy)
        self.template.setEnabled(self.rename.isChecked() and not self.busy)

    def run_task(self, function, completed):
        if self.busy:
            return
        self.busy = True
        self.update_buttons()
        task = Task(function)
        self._task = task
        task.signals.result.connect(completed)
        task.signals.error.connect(self.show_error)
        task.signals.finished.connect(self.finished)
        self.pool.start(task)

    def finished(self):
        self.busy = False
        self.update_buttons()

    def show_error(self, text):
        self.status.setText("Could not complete this action: " + text)
        if self.folder:
            try:
                self.show_history(history(self.folder))
            except Exception:
                pass  # preserve the original error rather than replacing it with a second one

    def show_history(self, rows):
        self.runs = rows
        self.populate(
            self.history_table,
            [(r["id"], r["state"], r["count"], r["created"][:19].replace("T", " ")) for r in rows],
        )
        if rows:
            self.history_table.selectRow(0)

    def display_preview(self, result):
        plan, groups, rows = result
        self.plan = plan
        self.populate(
            self.plan_table,
            [(m.source, m.destination, readable_size(m.identity.size)) for m in plan.moves],
        )
        self.populate(self.skipped_table, plan.skipped)
        self.populate(
            self.duplicate_table,
            [
                (
                    g["sha256"][:12],
                    len(g["files"]),
                    readable_size(g["size"]),
                    " · ".join(g["files"]),
                )
                for g in groups
            ],
        )
        self.show_history(rows)
        for label, number in zip(
            self.numbers, [len(plan.moves), len(groups), len(plan.skipped)], strict=True
        ):
            label.setText(str(number))
        self.status.setText(
            f"Preview ready: {len(plan.moves)} changes. No files have moved. Existing destinations will not be overwritten."
        )
        self.tabs.setCurrentIndex(0)

    def preview(self):
        if not self.folder or self.busy:
            return
        try:
            settings = self.settings()
        except FileFlowError as exc:
            self.show_error(str(exc))
            return
        folder = self.folder
        self.status.setText("Reading file identities and checking duplicates…")
        self.run_task(
            lambda: (make_plan(folder, settings), duplicates(folder, settings), history(folder)),
            self.display_preview,
        )

    def export_plan(self):
        if not self.plan:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save a new plan file", "fileflow-plan.json", "JSON (*.json)"
        )
        if name:
            try:
                self.plan.save(Path(name))
                self.status.setText(
                    "Plan exported. Use FileFlow CLI apply to execute this exact plan."
                )
            except (OSError, FileFlowError) as exc:
                self.show_error(str(exc))

    def apply_plan(self):
        if not self.plan or not self.plan.moves or self.busy:
            return
        plan = self.plan
        answer = QMessageBox.question(
            self,
            "Apply reviewed plan?",
            f"Move {len(plan.moves)} files inside:\n{plan.root}\n\nFileFlow will record the run for undo and stop on a conflict.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def applied(run):
            self.plan = None
            self.plan_table.setRowCount(0)
            self.numbers[0].setText("0")
            self.show_history(history(self.folder))
            self.status.setText(
                f"Applied run {run}. Review History to undo. No duplicate files were deleted."
            )

        self.run_task(lambda: apply(plan), applied)

    def reverse_run(self, undo: bool):
        row = self.selected_run()
        if not row or not self.folder or self.busy:
            return
        verb = "Undo" if undo else "Recover"
        answer = QMessageBox.question(
            self,
            f"{verb} selected run?",
            f"{verb} run {row['id']}?\nModified files and conflicting paths will be left intact.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def completed(run):
            self.plan = None
            self.plan_table.setRowCount(0)
            self.show_history(history(self.folder))
            self.status.setText(
                f"{verb} completed for run {run}. Create a new preview to continue."
            )

        self.run_task(lambda: resume(self.folder, row["id"], undo=undo), completed)

    def stop_watch(self):
        self.timer.stop()
        self.watcher = None
        if hasattr(self, "watch_button"):
            self.watch_button.setText("Watch new files")

    def toggle_watch(self):
        if self.watcher:
            self.stop_watch()
            self.status.setText("Watcher stopped. No automatic moves were enabled.")
        elif self.folder and not self.busy:

            def started(watcher):
                self.watcher = watcher
                self.watch_button.setText("Stop watching")
                self.timer.start()
                self.status.setText(
                    "Watching new stable files. Desktop mode creates previews only; Apply still requires confirmation."
                )

            settings = self.settings()
            self.run_task(lambda: Watcher(self.folder, settings), started)

    def watch_tick(self):
        if self.busy or not self.watcher:
            return
        watcher = self.watcher

        def poll():
            ready = watcher.poll(time.monotonic())
            if not ready:
                return None
            result = (
                make_plan(watcher.root, watcher.settings, only=ready),
                duplicates(watcher.root, watcher.settings),
                history(watcher.root),
            )
            watcher.acknowledge(ready)
            return result

        self.run_task(poll, lambda result: self.display_preview(result) if result else None)

    def closeEvent(self, event):
        self.stop_watch()
        if self.busy:
            event.ignore()
            self.status.setText(
                "Wait for the current operation before closing. Its journal records progress."
            )
        else:
            event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description="FileFlow native desktop application")
    parser.add_argument("folder", nargs="?", type=Path)
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("FileFlow")
    app.setFont(QFont("DejaVu Sans", 10))
    window = MainWindow(args.folder)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
