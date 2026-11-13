"""Headless smoke tests for the Qt UI (theme handling and log colouring)."""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette, QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from seas_audio.ui.app import (
        MAX_LOG_LINES,
        DropZone,
        LogPane,
        _table_css,
        _theme_colors,
    )
except ImportError:  # pragma: no cover - Qt is a runtime dependency
    QApplication = None

QT_AVAILABLE = QApplication is not None and sys.platform == "win32"


def light_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0078d7"))
    return palette


def dark_palette() -> QPalette:
    palette = QPalette(light_palette())
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#1e1e1e"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2b2b2b"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0078d4"))
    return palette


@unittest.skipUnless(QT_AVAILABLE, "Qt UI tests need Windows + PySide6")
class LogPaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._original = QPalette(self.app.palette())
        self.app.setPalette(light_palette())
        self.app.processEvents()

    def tearDown(self) -> None:
        self.app.setPalette(self._original)
        self.app.processEvents()

    def colour_at(self, pane: LogPane, position: int) -> str:
        cursor = QTextCursor(pane.document())
        cursor.setPosition(position)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
        return cursor.charFormat().foreground().color().name()

    def test_existing_lines_follow_theme_changes(self) -> None:
        pane = LogPane()
        pane.append("logged under the light theme", "error")
        self.assertEqual(self.colour_at(pane, 0), "#b3261e")

        self.app.setPalette(dark_palette())
        self.app.processEvents()
        self.assertEqual(self.colour_at(pane, 0), "#ff6b6b")

    def test_append_keeps_and_recolours_the_selection(self) -> None:
        pane = LogPane()
        pane.append("error line", "error")
        cursor = pane.textCursor()
        cursor.setPosition(0)
        cursor.movePosition(
            QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 5
        )
        pane.setTextCursor(cursor)

        pane.append("next line", "normal")

        self.assertTrue(pane.textCursor().hasSelection())
        self.assertEqual(self.colour_at(pane, 0), "#b3261e")

    def test_log_is_capped_and_follows_the_bottom(self) -> None:
        pane = LogPane()
        pane.resize(400, 120)
        pane.show()
        for index in range(MAX_LOG_LINES + 12):
            pane.append(f"line {index}", "normal")
        self.app.processEvents()

        self.assertEqual(pane.document().blockCount(), MAX_LOG_LINES)
        self.assertEqual(pane.document().lastBlock().text(), f"line {MAX_LOG_LINES + 11}")
        bar = pane.verticalScrollBar()
        self.assertEqual(bar.value(), bar.maximum())


class ThemeColourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not QT_AVAILABLE:
            raise unittest.SkipTest("Qt UI tests need Windows + PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def test_text_border_and_highlight_come_from_the_palette(self) -> None:
        for palette in (light_palette(), dark_palette()):
            theme = _theme_colors(palette)
            self.assertEqual(theme["text"].name(), palette.color(QPalette.ColorRole.Text).name())
            self.assertEqual(
                theme["border_active"].name(),
                palette.color(QPalette.ColorRole.Highlight).name(),
            )
            self.assertNotEqual(theme["border"].name(), theme["drop_bg"].name())

    def test_table_alternate_rows_are_theme_aware(self) -> None:
        css = _table_css(dark_palette())
        self.assertIn("alternate-background-color", css)
        self.assertNotIn("f7f7f7", css.lower())


class DropZoneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not QT_AVAILABLE:
            raise unittest.SkipTest("Qt UI tests need Windows + PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def test_clicking_the_target_opens_browse(self) -> None:
        zone = DropZone("target")
        zone.show()
        calls: list[bool] = []
        zone.browse = lambda: calls.append(True)  # type: ignore[method-assign]
        QTest.mouseClick(zone._target, Qt.MouseButton.LeftButton)
        self.assertTrue(calls)
        zone.close()


if __name__ == "__main__":
    unittest.main()
