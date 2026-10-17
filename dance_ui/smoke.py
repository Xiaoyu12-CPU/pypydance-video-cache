"""Bounded demo-only checks for the executable produced by Windows CI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import traceback

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import qVersion
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from PIL import __version__ as pillow_version

from . import __version__
from .theme import BUNDLED_PRESET_DIR, THEME_FIELDS


def inspect_demo(window) -> dict:
    """Exercise real Qt resources/rendering, without starting the cache core."""
    report = {
        "status": "failed", "frozen": bool(getattr(sys, "frozen", False)),
        "platform": sys.platform, "qt_platform": QApplication.platformName(),
        "application_version": __version__, "qt_version": qVersion(),
        "pyside_version": pyside_version, "pillow_version": pillow_version,
        "checks": [], "real_service_tested": False, "vrchat_tested": False,
    }
    try:
        if not window.controller.demo:
            raise ValueError("Executable smoke checks require an isolated demo.")
        report["checks"].append("demo_mode")
        if window.store.warnings:
            raise ValueError("; ".join(window.store.warnings))
        snapshot = json.loads((BUNDLED_PRESET_DIR / "theme.json").read_text(encoding="utf-8"))
        expected = snapshot["theme"]
        if window.store.active_id != expected["id"]:
            raise ValueError("Use a fresh state directory to check the shipped default theme.")
        for field in THEME_FIELDS | {"id", "name", "base"}:
            if window.store.active[field] != expected[field]:
                raise ValueError("Shipped theme differs from the snapshot: " + field)
        if window.store.preferences != snapshot["preferences"]:
            raise ValueError("Fresh reading preferences differ from the shipped snapshot.")
        image = window.store.background_path()
        if image is None or hashlib.sha256(image.read_bytes()).hexdigest() != snapshot["background_sha256"]:
            raise ValueError("The shipped background is missing or changed.")
        if window.backdrop.image is None:
            raise ValueError("Pillow could not decode the shipped background.")
        report["theme_name"] = window.store.active["name"]
        report["background_sha256"] = snapshot["background_sha256"]
        report["checks"].append("shipped_theme_and_pillow_background")
        for name in ("check-light", "check-dark", "up", "down"):
            if QPixmap(":/controls/" + name + ".svg").isNull():
                raise ValueError("Qt could not decode the embedded SVG: " + name)
        report["checks"].append("embedded_svg_icons")
        titles = [button.text() for button in window.nav_buttons]
        if titles != ["运行", "存储", "设置", "外观"] or window.stack.count() != 4:
            raise ValueError("The expected four pages were not created.")
        for index, button in enumerate(window.nav_buttons):
            button.click()
            QApplication.processEvents()
            if window.stack.currentIndex() != index or not window.stack.currentWidget().isVisible():
                raise ValueError("Navigation did not show page " + titles[index])
            if window.grab().isNull():
                raise ValueError("Qt could not render page " + titles[index])
        if window.backdrop._render is None or window.backdrop._render.isNull():
            raise ValueError("The Pillow-to-Qt background rendering failed.")
        report["checks"].append("four_page_navigation_and_rendering")
        report["status"] = "passed"
    except Exception:
        report["error"] = traceback.format_exc()
    return report


def save_report(path: Path, report: dict) -> int:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        if sys.stderr is not None:
            print("Cannot write smoke report: " + str(error), file=sys.stderr)
        return 1
    return 0 if report.get("status") == "passed" else 1
