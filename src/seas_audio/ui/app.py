"""Drag-and-drop window for the Seas audio tools (Qt/PySide6 port).

One window, two tabs. Each tab exposes a :class:`DropZone` that accepts files
*and* folders from Explorer and hands back real absolute paths, which is the
whole reason this is a native toolkit rather than a webview.

Long work runs on a :class:`QThread` and talks to the UI only through Qt
signals, so no widget is ever touched off the main thread. Compared with the
Tk version this redraws smoothly on resize (hardware-accelerated rendering)
and has no manual ``after()`` polling loop - Qt's queued signal delivery does
that job.

The ``core`` and ``tools`` packages are reused unchanged.
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from seas_audio import __version__
from seas_audio.core.cache import FingerprintCache
from seas_audio.core.config import (
    APP_TITLE,
    DEFAULT_DURATION_TOLERANCE,
    DEFAULT_MAX_OFFSET_FRAMES,
    DEFAULT_SIMILARITY_THRESHOLD,
    AppPaths,
    Settings,
)
from seas_audio.core.fpcalc import FpcalcNotFoundError, probe
from seas_audio.core.library import build_library
from seas_audio.core.scanner import collect_audio_files
from seas_audio.tools import duplicates, sanitizer

MAX_LOG_LINES = 3000

COLOUR_DROP = "#f4f7fb"
COLOUR_DROP_ACTIVE = "#dbe9ff"
COLOUR_BORDER = "#b8c4d6"
COLOUR_BORDER_ACTIVE = "#4a86e8"
COLOUR_TEXT = "#22303f"
COLOUR_MUTED = "#5a6b7d"
COLOUR_ERROR = "#b3261e"
COLOUR_OK = "#1b6b35"

_LEVEL_COLOURS = {
    "error": COLOUR_ERROR,
    "ok": COLOUR_OK,
    "muted": COLOUR_MUTED,
}


def _asset(name: str) -> Path | None:
    """Find a bundled asset both in development and inside a PyInstaller app."""
    bundle = getattr(sys, "_MEIPASS", None)
    candidates: list[Path] = []
    if bundle:
        candidates.append(Path(bundle) / name)
    here = Path(__file__).resolve()
    candidates.append(here.parents[3] / "assets" / name)
    candidates.append(here.parents[2] / "assets" / name)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _reveal_in_explorer(path: str) -> None:
    """Open Explorer with the file selected."""
    target = os.path.normpath(path)
    try:
        if os.name == "nt":
            subprocess.run(["explorer", "/select,", target], check=False)
        else:  # pragma: no cover - Windows is the target platform
            subprocess.run(["xdg-open", os.path.dirname(target)], check=False)
    except OSError:
        pass


class LogPane(QTextEdit):
    """Read-only scrolling log with colour-coded lines."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setStyleSheet(
            f"background: #fbfcfd; color: {COLOUR_TEXT}; border: 1px solid {COLOUR_BORDER};"
        )

    def append(self, message: str, level: str | None = None) -> None:
        self.setTextColor(QColor(_LEVEL_COLOURS.get(level or "", COLOUR_TEXT)))
        super().append(message)
        self.setTextColor(QColor(COLOUR_TEXT))
        self._trim()

    def clear(self) -> None:
        super().clear()

    def _trim(self) -> None:
        document = self.document()
        while document.blockCount() > MAX_LOG_LINES:
            from PySide6.QtGui import QTextCursor

            block = document.firstBlock()
            cursor = QTextCursor(block)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()


class DropZone(QWidget):
    """A target that accepts dropped files and folders and keeps their paths."""

    def __init__(
        self,
        title: str,
        *,
        hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths: list[Path] = []
        self._enabled = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(title_label)

        self._target = QLabel("Drop files or folders here")
        self._target.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._target.setMinimumHeight(56)
        self._target.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paint(active=False)
        layout.addWidget(self._target)

        self._listbox = QListWidget()
        self._listbox.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._listbox, 1)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.browse_button = QPushButton("Browse...")
        self.remove_button = QPushButton("Remove selected")
        self.clear_button = QPushButton("Clear")
        self.browse_button.clicked.connect(self.browse)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear)
        row.addWidget(self.browse_button)
        row.addWidget(self.remove_button)
        row.addWidget(self.clear_button)
        row.addStretch(1)
        layout.addWidget(buttons)

        self._hint = QLabel(hint)
        self._hint.setStyleSheet(f"color: {COLOUR_MUTED};")
        layout.addWidget(self._hint)

        self.setAcceptDrops(True)
        self._refresh()

    def _paint(self, *, active: bool) -> None:
        background = COLOUR_DROP_ACTIVE if active else COLOUR_DROP
        border = COLOUR_BORDER_ACTIVE if active else COLOUR_BORDER
        self._target.setStyleSheet(
            f"background: {background}; color: {COLOUR_MUTED};"
            f" border: 1px solid {border}; padding: 12px;"
        )

    @property
    def paths(self) -> list[Path]:
        return list(self._paths)

    def add_paths(self, raw_paths) -> int:
        """Add paths, keeping the dropped order and skipping duplicates."""
        added = 0
        seen = {_norm(path) for path in self._paths}
        for raw in raw_paths:
            if not raw:
                continue
            path = Path(str(raw).strip().strip('"'))
            key = _norm(path)
            if key in seen:
                continue
            seen.add(key)
            self._paths.append(path)
            added += 1
        if added:
            self._refresh()
        return added

    def clear(self) -> None:
        if not self._paths:
            return
        self._paths.clear()
        self._refresh()

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self._listbox.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            del self._paths[row]
        self._refresh()

    def browse(self) -> None:
        if not self._enabled:
            return
        chosen = QFileDialog.getExistingDirectory(self, "Choose a music folder")
        if chosen:
            self.add_paths([chosen])

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.browse_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self._listbox.setEnabled(enabled)
        self._target.setCursor(
            Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        )

    def _refresh(self) -> None:
        self._listbox.clear()
        for path in self._paths:
            suffix = "\\" if path.is_dir() else ""
            self._listbox.addItem(f"{path}{suffix}")
        if not self._paths:
            self._hint.setText("Nothing queued yet.")
        else:
            self._hint.setText(f"{len(self._paths)} path(s) queued.")

    def dragEnterEvent(self, event) -> None:
        if self._enabled and (event.mimeData().hasUrls() or event.mimeData().hasText()):
            event.acceptProposedAction()
            self._paint(active=True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._paint(active=False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._paint(active=False)
        if not self._enabled:
            event.ignore()
            return
        paths: list[str] = []
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if local:
                    paths.append(local)
        elif event.mimeData().hasText():
            for part in event.mimeData().text().splitlines():
                part = part.strip().strip('"')
                if part:
                    paths.append(part)
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class JobThread(QThread):
    """Run one worker function off the UI thread, relaying events via signals."""

    log = Signal(str, str)          # message, level
    progress = Signal(int, int, str)  # done, total, current name (may be "")
    phase = Signal(str)
    results = Signal(object)
    failed = Signal(str)
    done = Signal()

    def __init__(self, fn, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._cancel = threading.Event()

    def run(self) -> None:
        try:
            self._fn(self)
        except FpcalcNotFoundError as exc:
            self.failed.emit(f"fpcalc is missing.\n{exc}")
        except Exception as exc:  # keep the window alive whatever happens
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.done.emit()

    def request_cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()


class JobTab(QWidget):
    """A tab that runs one background job at a time."""

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.app = app
        self._thread: JobThread | None = None

    def start_job(self, fn) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        thread = JobThread(fn, self)
        thread.log.connect(self._handle_log)
        thread.progress.connect(self._handle_progress)
        thread.phase.connect(self._handle_phase)
        thread.results.connect(self._handle_results)
        thread.failed.connect(self._handle_error)
        thread.done.connect(self._handle_done)
        self._thread = thread
        self.on_job_start()
        thread.start()

    def request_cancel(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.request_cancel()
        self.on_cancel_requested()

    def shutdown(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.request_cancel()
            self._thread.wait(1500)

    # --- hooks (overridden per tab) ---
    def on_job_start(self) -> None: ...
    def on_job_finish(self) -> None: ...
    def on_cancel_requested(self) -> None: ...

    def _handle_log(self, message: str, level: str) -> None: ...
    def _handle_progress(self, done: int, total: int, name: str) -> None: ...
    def _handle_phase(self, text: str) -> None: ...
    def _handle_results(self, payload) -> None: ...

    def _handle_error(self, message: str) -> None:
        QMessageBox.critical(self, APP_TITLE, message)

    def _handle_done(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.deleteLater()
        self.on_job_finish()


class SanitizeTab(JobTab):
    """Drop a folder, fix the tags."""

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.drop = DropZone("Files or folders to sanitize", hint="MP3 files only. Other audio formats are skipped.")
        layout.addWidget(self.drop, 1)

        options = QGroupBox("Options")
        opt_row = QHBoxLayout(options)
        self.dry_run = QCheckBox("Dry run (report only, change nothing)")
        self.backup = QCheckBox("Keep a .bak copy before writing")
        opt_row.addWidget(self.dry_run)
        opt_row.addWidget(self.backup)
        opt_row.addStretch(1)
        layout.addWidget(options)

        controls = QWidget()
        ctrl = QHBoxLayout(controls)
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.setSpacing(6)
        self.run_button = QPushButton("Sanitize")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress_label = QLabel("idle")
        self.progress_label.setStyleSheet(f"color: {COLOUR_MUTED};")
        self.run_button.clicked.connect(self.run)
        self.cancel_button.clicked.connect(self.request_cancel)
        ctrl.addWidget(self.run_button)
        ctrl.addWidget(self.cancel_button)
        ctrl.addWidget(self.progress, 1)
        ctrl.addWidget(self.progress_label)
        layout.addWidget(controls)

        self.log = LogPane()
        layout.addWidget(self.log, 2)

    def run(self) -> None:
        if not self.drop.paths:
            QMessageBox.information(self, APP_TITLE, "Drop a file or folder first.")
            return
        self.log.clear()
        dry_run = self.dry_run.isChecked()
        backup = self.backup.isChecked()
        self.start_job(lambda job: self._work(job, dry_run, backup))

    def _work(self, job: JobThread, dry_run: bool, backup: bool) -> None:
        targets = self.drop.paths
        job.log.emit(f"Scanning {len(targets)} target(s)...", "normal")

        def warn(message: str) -> None:
            job.log.emit(message, "muted")

        files = collect_audio_files(targets, warn=warn)
        if not files:
            job.log.emit("No supported audio files found.", "error")
            return

        job.log.emit(f"Found {len(files)} audio file(s).", "normal")
        results = sanitizer.sanitize_many(
            files,
            dry_run=dry_run,
            backup=backup,
            on_progress=lambda done, total, name: job.progress.emit(done, total, name),
            should_cancel=job.is_cancelled,
        )

        counts: dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
            if result.status == sanitizer.STATUS_ERROR:
                job.log.emit(f"error  {result.path.name}: {result.message}", "error")
            elif result.status == sanitizer.STATUS_SKIPPED:
                continue
            elif result.status == sanitizer.STATUS_CLEAN:
                job.log.emit(f"clean  {result.path.name}", "muted")
            else:
                job.log.emit(f"{result.status}  {result.path.name}: {result.message}", "ok")

        if job.is_cancelled():
            job.log.emit("Cancelled - stopped early.", "error")
        job.log.emit(
            "Summary: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())),
            "ok",
        )
        if dry_run:
            job.log.emit("Dry run - no files were modified.", "muted")

    def on_job_start(self) -> None:
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.drop.set_enabled(False)
        self.progress.setValue(0)
        self.progress_label.setText("starting...")

    def on_job_finish(self) -> None:
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.drop.set_enabled(True)
        self.progress_label.setText("finished")
        self.app.refresh_status()

    def on_cancel_requested(self) -> None:
        self.progress_label.setText("cancelling...")

    def _handle_log(self, message: str, level: str) -> None:
        self.log.append(message, level)

    def _handle_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(int(done / total * 100) if total else 0)
        text = f"{done:,}/{total:,}  {name}"[:64] if name else f"{done:,}/{total:,}"
        self.progress_label.setText(text)


class DuplicatesTab(JobTab):
    """Drop a library, find the near-duplicates."""

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self._pairs: list[duplicates.DuplicatePair] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        zones = QWidget()
        zones_row = QHBoxLayout(zones)
        zones_row.setContentsMargins(0, 0, 0, 0)
        zones_row.setSpacing(6)
        self.primary = DropZone("Library / batch to scan", hint="Fingerprints are cached, so repeat runs are fast.")
        self.reference = DropZone("Reference library (optional)", hint="Leave empty to scan the batch against itself.")
        zones_row.addWidget(self.primary, 1)
        zones_row.addWidget(self.reference, 1)
        layout.addWidget(zones, 1)

        tuning = QGroupBox("Matching")
        tune = QHBoxLayout(tuning)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.5, 1.0)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.threshold.setValue(DEFAULT_SIMILARITY_THRESHOLD)
        self.tolerance = QSpinBox()
        self.tolerance.setRange(0, 120)
        self.tolerance.setValue(int(DEFAULT_DURATION_TOLERANCE))
        self.max_offset = QSpinBox()
        self.max_offset.setRange(0, 500)
        self.max_offset.setSingleStep(10)
        self.max_offset.setValue(DEFAULT_MAX_OFFSET_FRAMES)
        self.use_cache = QCheckBox("Use fingerprint cache")
        self.use_cache.setChecked(True)
        tune.addWidget(QLabel("Similarity threshold"))
        tune.addWidget(self.threshold)
        tune.addWidget(QLabel("Duration window (s)"))
        tune.addWidget(self.tolerance)
        tune.addWidget(QLabel("Offset search"))
        tune.addWidget(self.max_offset)
        tune.addWidget(self.use_cache)
        tune.addStretch(1)
        layout.addWidget(tuning)

        controls = QWidget()
        ctrl = QHBoxLayout(controls)
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.setSpacing(6)
        self.run_button = QPushButton("Find duplicates")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.export_button = QPushButton("Export CSV...")
        self.export_button.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress_label = QLabel("idle")
        self.progress_label.setStyleSheet(f"color: {COLOUR_MUTED};")
        self.run_button.clicked.connect(self.run)
        self.cancel_button.clicked.connect(self.request_cancel)
        self.export_button.clicked.connect(self.export_csv)
        ctrl.addWidget(self.run_button)
        ctrl.addWidget(self.cancel_button)
        ctrl.addWidget(self.export_button)
        ctrl.addWidget(self.progress, 1)
        ctrl.addWidget(self.progress_label)
        layout.addWidget(controls)

        self.results = QTableWidget(0, 5)
        self.results.setHorizontalHeaderLabels(["Match", "Scanned file", "kbps", "Matches", "kbps"])
        header = self.results.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setAlternatingRowColors(True)
        self.results.verticalHeader().setVisible(False)
        self.results.cellDoubleClicked.connect(self._reveal_selected)
        layout.addWidget(self.results, 2)

        self.log = LogPane()
        layout.addWidget(self.log, 1)

    def run(self) -> None:
        if not self.primary.paths:
            QMessageBox.information(self, APP_TITLE, "Drop the library or batch you want to scan.")
            return
        self.log.clear()
        self.results.setRowCount(0)
        self._pairs = []
        self.export_button.setEnabled(False)
        threshold = self.threshold.value()
        tolerance = self.tolerance.value()
        max_offset = self.max_offset.value()
        use_cache = self.use_cache.isChecked()
        self.start_job(lambda job: self._work(job, threshold, tolerance, max_offset, use_cache))

    def _work(
        self,
        job: JobThread,
        threshold: float,
        tolerance: int,
        max_offset: int,
        use_cache: bool,
    ) -> None:
        paths = self.app.paths
        cache = FingerprintCache(paths.fingerprint_cache)
        skip = [paths.quarantine_dir]

        def warn(message: str) -> None:
            job.log.emit(message, "muted")

        job.phase.emit("Scanning folders...")
        primary_files = collect_audio_files(self.primary.paths, skip_dirs=skip, warn=warn)
        if not primary_files:
            job.log.emit("No supported audio files in the scanned paths.", "error")
            return
        job.log.emit(f"Primary set: {len(primary_files)} file(s).", "normal")

        reference_files = (
            collect_audio_files(self.reference.paths, skip_dirs=skip, warn=warn)
            if self.reference.paths
            else []
        )
        if reference_files:
            job.log.emit(f"Reference set: {len(reference_files)} file(s).", "normal")

        job.phase.emit("Fingerprinting primary...")
        primary_build = build_library(
            primary_files,
            cache,
            use_cache=use_cache,
            on_progress=lambda done, total: job.progress.emit(done, total, ""),
            should_cancel=job.is_cancelled,
        )
        if primary_build.cache_note:
            job.log.emit(primary_build.cache_note, "muted")
        if primary_build.added:
            job.log.emit(f"Fingerprinted {primary_build.added} new file(s).", "normal")
        else:
            job.log.emit("All primary fingerprints came from the cache.", "ok")
        for path, reason in primary_build.failures:
            job.log.emit(f"unreadable  {Path(path).name}: {reason}", "error")

        if job.is_cancelled():
            return

        reference_records = None
        if reference_files:
            job.phase.emit("Fingerprinting reference...")
            reference_build = build_library(
                reference_files,
                cache,
                use_cache=use_cache,
                on_progress=lambda done, total: job.progress.emit(done, total, ""),
                should_cancel=job.is_cancelled,
            )
            for path, reason in reference_build.failures:
                job.log.emit(f"unreadable  {Path(path).name}: {reason}", "error")
            reference_records = reference_build.records

        if job.is_cancelled():
            return

        params = duplicates.SimilarityParams(
            duration_tolerance=float(tolerance),
            threshold=threshold,
            max_offset_frames=max_offset,
        )
        total = duplicates.count_candidate_pairs(
            primary_build.records, reference_records, params.duration_tolerance
        )
        job.log.emit(f"Comparing {total:,} candidate pair(s).", "normal")
        job.phase.emit("Comparing fingerprints...")

        pairs = duplicates.find_duplicates(
            primary_build.records,
            reference_records,
            params,
            on_progress=lambda done, total_pairs: job.progress.emit(done, total_pairs, ""),
            should_cancel=job.is_cancelled,
        )

        if job.is_cancelled():
            job.log.emit("Cancelled - partial results discarded.", "error")
            return
        if not pairs:
            job.log.emit("No duplicates above the threshold.", "ok")
            return

        job.results.emit(pairs)
        job.log.emit(f"Found {len(pairs)} duplicate pair(s).", "ok")
        job.log.emit("Double-click a row to reveal the file. Export writes the full report.", "muted")

    def export_csv(self) -> None:
        if not self._pairs:
            return
        default = self.app.paths.report_path("duplicate_report.csv")
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Save duplicate report",
            str(default),
            "CSV file (*.csv);;All files (*.*)",
        )
        if not target:
            return
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(duplicates.ROW_FIELDS))
                writer.writeheader()
                for pair in self._pairs:
                    writer.writerow(pair.as_row())
        except OSError as exc:
            QMessageBox.critical(self, APP_TITLE, f"Could not write report: {exc}")
            return
        self.log.append(f"Report written to {target}", "ok")

    def _reveal_selected(self, row: int, _column: int) -> None:
        try:
            pair = self._pairs[row]
        except (IndexError, KeyError):
            return
        _reveal_in_explorer(pair.path_a)

    def on_job_start(self) -> None:
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.primary.set_enabled(False)
        self.reference.set_enabled(False)
        self.progress.setValue(0)
        self.progress_label.setText("starting...")

    def on_job_finish(self) -> None:
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.primary.set_enabled(True)
        self.reference.set_enabled(True)
        self.app.refresh_status()

    def on_cancel_requested(self) -> None:
        self.progress_label.setText("cancelling...")

    def _handle_log(self, message: str, level: str) -> None:
        self.log.append(message, level)

    def _handle_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(int(done / total * 100) if total else 0)
        self.progress_label.setText(f"{done:,}/{total:,}" if total else "")

    def _handle_phase(self, text: str) -> None:
        self.progress_label.setText(text)
        self.progress.setValue(0)

    def _handle_results(self, payload) -> None:
        self._show_pairs(payload)

    def _show_pairs(self, pairs: list) -> None:
        self._pairs = pairs
        self.results.setRowCount(len(pairs))
        for row, pair in enumerate(pairs):
            self.results.setItem(row, 0, QTableWidgetItem(f"{pair.similarity * 100:.1f}%"))
            self.results.setItem(row, 1, QTableWidgetItem(pair.filename_a))
            self.results.setItem(row, 2, QTableWidgetItem(str(pair.kbps_a)))
            self.results.setItem(row, 3, QTableWidgetItem(pair.filename_b))
            self.results.setItem(row, 4, QTableWidgetItem(str(pair.kbps_b)))
        self.export_button.setEnabled(bool(pairs))


class App(QMainWindow):
    """The main window: header, notebook, status bar."""

    def __init__(self, initial_paths: list[str] | None = None) -> None:
        super().__init__()
        self.paths = AppPaths.default().ensure()
        self.settings = Settings.load(self.paths.settings_file)

        self.setWindowTitle(f"{APP_TITLE} {__version__}")
        self.resize(1060, 800)
        self.setMinimumSize(860, 620)

        icon = _asset("icon.ico")
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 6)
        root.setSpacing(6)

        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)
        title = QLabel("Seas Audio Tools")
        title.setStyleSheet("font-weight: bold; font-size: 14pt;")
        subtitle = QLabel("drop folders in, pick a tool")
        subtitle.setStyleSheet(f"color: {COLOUR_MUTED};")
        header_row.addWidget(title)
        header_row.addWidget(subtitle, alignment=Qt.AlignmentFlag.AlignBottom)
        header_row.addStretch(1)
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.sanitize_tab = SanitizeTab(self)
        self.duplicates_tab = DuplicatesTab(self)
        self.tabs.addTab(self.sanitize_tab, "Sanitize tags")
        self.tabs.addTab(self.duplicates_tab, "Find duplicates")
        root.addWidget(self.tabs, 1)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {COLOUR_MUTED}; padding: 4px;")
        root.addWidget(self.status)

        self._apply_initial_paths(initial_paths)
        QTimer.singleShot(120, self.refresh_status)

    def _apply_initial_paths(self, initial_paths: list[str] | None) -> None:
        if initial_paths:
            self.sanitize_tab.drop.add_paths(initial_paths)
            self.duplicates_tab.primary.add_paths(initial_paths)
        elif self.settings.library_paths:
            # Restore the library remembered from the previous session.
            self.duplicates_tab.primary.add_paths(self.settings.library_paths)

    def refresh_status(self) -> None:
        status = probe()
        if status.lower().startswith("fpcalc version"):
            self.status.setStyleSheet(f"color: {COLOUR_MUTED}; padding: 4px;")
            self.status.setText(
                f"{status.splitlines()[0]}  |  cache: {self.paths.cache_dir}"
            )
        else:
            self.status.setStyleSheet(f"color: {COLOUR_ERROR}; padding: 4px;")
            self.status.setText(f"fpcalc problem: {status}")

    def closeEvent(self, event) -> None:
        self.settings.library_paths = [str(path) for path in self.duplicates_tab.primary.paths]
        try:
            self.settings.save(self.paths.settings_file)
        except OSError:
            pass
        self.sanitize_tab.shutdown()
        self.duplicates_tab.shutdown()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``seas-audio gui`` and the ``seas-audio-gui`` script."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    initial = [item for item in arguments if not item.startswith("-")]

    qt_args = [sys.argv[0]] + arguments
    app = QApplication.instance() or QApplication(qt_args)
    app.setApplicationName("seas-audio-tools")
    app.setApplicationDisplayName(APP_TITLE)

    window = App(initial_paths=initial)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
