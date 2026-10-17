"""Shared widgets and the application-wide visual system."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps
from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsEffect, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QTableWidget, QHeaderView, QAbstractItemView, QVBoxLayout, QWidget,
)

from . import resources_rc  # noqa: F401 -- registers the bundled control icons
from .theme import BUILTIN_THEMES


def rgba(hex_color: str, alpha: float = 1.0) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {round(alpha * 255)})"


def on_color(value: str) -> str:
    color = QColor(value)
    channels = [c / 255 for c in (color.red(), color.green(), color.blue())]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    luminance = sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    return "#182132" if luminance > .179 else "#FFFFFF"


def stylesheet(theme: dict[str, Any], preferences: dict[str, Any]) -> str:
    scale = preferences.get("scale", 100) / 100
    px = lambda n: max(1, round(n * scale))
    density = preferences.get("density", "standard")
    padding = {"comfortable": 11, "standard": 8, "compact": 5}.get(density, 8)
    accent = theme["accent"]
    text = theme["text_primary"]
    muted = theme["text_secondary"]
    card = rgba(theme["card_background"], theme["card_opacity"])
    border = rgba(theme["border_color"], theme["border_opacity"])
    radius = round(theme["radius"])
    is_dark = QColor(theme["background_color"]).lightnessF() < .45
    subtle = "rgba(255,255,255,12)" if is_dark else "rgba(24,43,73,7)"
    input_bg = rgba(theme["card_background"], max(.85, theme["card_opacity"]))
    check_icon = "check-dark.svg" if on_color(accent) != "#FFFFFF" else "check-light.svg"
    tones = {
        "success": ("#7CDBA5" if is_dark else "#20824E", "rgba(43,166,92,24)"),
        "warning": ("#E7BF78" if is_dark else "#9B6819", "rgba(211,157,41,25)"),
        "error": ("#F3A0AD" if is_dark else "#BD4355", "rgba(203,64,89,22)"),
        "info": ("#9CBDFF" if is_dark else "#326FD4", "rgba(56,119,232,23)"),
        "muted": ("#B1BDD1" if is_dark else "#687A94", "rgba(125,145,174,18)"),
    }
    status_rules = "\n".join(
        f'QLabel[statusTone="{tone}"] {{ color:{colors[0]}; background:{colors[1]}; border:none; border-radius:6px; padding:5px 9px; }}'
        for tone, colors in tones.items()
    )
    return f"""
    QWidget {{ color: {text}; font-size: {px(14)}px; }}
    QMainWindow {{ background: {theme['background_color']}; }}
    QLabel, QCheckBox, QRadioButton {{ background: transparent; border: none; }}
    QLabel[muted="true"] {{ color: {muted}; font-size: {px(12)}px; }}
    QLabel[heading="true"], QLabel[pageTitle="true"] {{ font-size: {px(21)}px; font-weight: 650; }}
    QLabel[subheading="true"] {{ font-size: {px(16)}px; font-weight: 600; }}
    QLabel[strong="true"] {{ font-weight: 600; }}
    QLabel[danger="true"] {{ color: #c64d5c; }}
    QToolButton[sectionHeader="true"] {{ background: transparent; border: none; text-align: left; font-size: {px(16)}px; font-weight: 600; padding: 4px 0; }}
    QLabel[heroTitle="true"] {{ font-size: {px(27)}px; font-weight: 700; }}
    QLabel[number="true"] {{ font-size: {px(27)}px; font-weight: 650; }}
    QFrame[card="true"] {{
        background: {card}; border: {theme['border_width']}px solid {border};
        border-radius: {radius}px;
    }}
    QFrame[hero="true"] {{ background: {rgba(accent, .10)}; }}
    QWidget#header {{
        background: {rgba(theme['nav_background'], theme['nav_opacity'])};
        border-bottom: 1px solid {border};
    }}
    QWidget#footer {{ background: {rgba(theme['nav_background'], theme['nav_opacity'])}; border-top: 1px solid {border}; }}
    QPushButton {{
        background: {input_bg}; border: 1px solid {border}; border-radius: {min(radius, 8)}px;
        padding: {px(padding)}px {px(14)}px; min-height: {px(18)}px;
    }}
    QPushButton:hover {{ background: {rgba(accent, .12)}; border-color: {rgba(accent, .50)}; }}
    QPushButton:pressed {{ background: {rgba(accent, .20)}; }}
    QPushButton:focus {{ border-color: {accent}; }}
    QPushButton:disabled {{ color: {muted}; background: {subtle}; border-color: {border}; }}
    QPushButton[primary="true"] {{ background: {accent}; color: {on_color(accent)}; border-color: {accent}; font-weight: 600; }}
    QPushButton[primary="true"]:hover {{ background: {QColor(accent).lighter(110).name()}; }}
    QPushButton[primary="true"]:disabled {{ background: {rgba(accent, .30)}; color: {muted}; border-color: transparent; }}
    QPushButton[nav="true"] {{
        color: {theme['nav_text']}; background: transparent; border: none;
        border-bottom: 3px solid transparent; border-radius: 0;
        padding: {px(17)}px {px(19)}px; font-size: {px(16)}px;
    }}
    QPushButton[nav="true"]:checked {{
        color: {theme['nav_active_text']};
        background: {rgba(theme['nav_active_background'], theme['nav_active_opacity'])};
        border-bottom-color: {accent}; font-weight: 650;
    }}
    QPushButton[link="true"], QPushButton[subtle="true"] {{ color: {accent}; background: transparent; border: none; padding: {px(3)}px; }}
    QPushButton[danger="true"] {{ color: #c04c5b; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {input_bg}; border: 1px solid {border}; border-radius: {min(radius, 7)}px;
        padding: {px(padding)}px {px(10)}px; min-height: {px(19)}px;
        selection-background-color: {accent}; selection-color: {on_color(accent)};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}
    QSpinBox, QDoubleSpinBox {{ padding-right: {px(28)}px; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: {px(25)}px; border: none; background: transparent; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: {px(25)}px; border: none; background: transparent; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url(:/controls/up.svg); width: {px(10)}px; height: {px(7)}px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url(:/controls/down.svg); width: {px(10)}px; height: {px(7)}px; }}
    QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: top right; width: {px(28)}px; border: none; background: transparent; }}
    QComboBox::down-arrow {{ image: url(:/controls/down.svg); width: {px(10)}px; height: {px(7)}px; }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {muted}; background: {subtle}; }}
    QComboBox QAbstractItemView {{ background: {theme['card_background']}; color: {text}; selection-background-color: {accent}; }}
    QCheckBox {{ spacing: {px(9)}px; padding: {px(3)}px 0; }}
    QCheckBox::indicator {{ width: {px(16)}px; height: {px(16)}px; border: 1px solid {rgba(muted, .6)}; border-radius: 4px; background: {input_bg}; }}
    QCheckBox::indicator:checked {{ image: url(:/controls/{check_icon}); border-color: {accent}; background: {accent}; }}
    QCheckBox::indicator:disabled {{ border-color: {border}; background: {subtle}; }}
    QScrollArea, QWidget#page, QWidget#pageViewport, QWidget#appearancePage, QWidget#appearanceContent, QWidget#appearanceScroll {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {rgba(theme['text_secondary'], .30)}; min-height: 30px; border-radius: 3px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 9px; }}
    QScrollBar::handle:horizontal {{ background: {rgba(theme['text_secondary'], .30)}; min-width: 30px; border-radius: 3px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QTableWidget {{
        background: transparent; alternate-background-color: {subtle}; border: none;
        gridline-color: {border}; selection-background-color: {rgba(accent, .13)};
        selection-color: {text}; outline: none;
    }}
    QTableWidget::item {{ padding: {px(8)}px; border-bottom: 1px solid {border}; }}
    QHeaderView, QHeaderView::section {{ background: {subtle}; color: {muted}; border: none; }}
    QHeaderView::section {{ padding: {px(7)}px {px(8)}px; font-weight: 500; }}
    QTableCornerButton::section {{ background: transparent; border: none; }}
    QProgressBar {{
        background: {rgba(theme['text_secondary'], .16)}; border: none; border-radius: 4px;
        min-height: 8px; max-height: 8px;
    }}
    QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
    QPlainTextEdit {{ background: {input_bg}; border: 1px solid {border}; border-radius: {min(radius, 8)}px; padding: 8px; }}
    QSlider::groove:horizontal {{ height: 5px; background: {rgba(theme['text_secondary'], .20)}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 15px; margin: -5px 0; background: {accent}; border: 2px solid {theme['card_background']}; border-radius: 8px; }}
    QGroupBox {{ border: 1px solid {border}; border-radius: {radius}px; margin-top: 14px; padding: 14px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 5px; }}
    QToolTip {{ background: {theme['card_background']}; color: {text}; border: 1px solid {border}; padding: 6px; }}
    QMessageBox, QDialog {{ background: {theme['background_color']}; }}
    {status_rules}
    """


class Backdrop(QWidget):
    """Paint only the background; descendants retain their own opacity."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme: dict[str, Any] = {}
        self.image: Image.Image | None = None
        self._image_key = None
        self._render_key = None
        self._render: QPixmap | None = None

    def set_appearance(self, theme: dict, image_path: Path | None):
        self.theme = dict(theme)
        key = (str(image_path), theme.get("image_blur", 0))
        if key != self._image_key:
            self._image_key = key
            self.image = None
            if image_path:
                try:
                    with Image.open(image_path) as source:
                        source = ImageOps.exif_transpose(source).convert("RGBA")
                        source.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
                        if theme.get("image_blur", 0):
                            source = source.filter(ImageFilter.GaussianBlur(theme["image_blur"]))
                        self.image = source.copy()
                except (OSError, ValueError):
                    self.image = None
            self._render_key = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        t = self.theme
        foundation = BUILTIN_THEMES.get(t.get("base", "light"), BUILTIN_THEMES["light"])["background_color"]
        painter.fillRect(self.rect(), QColor(foundation))
        base = QColor(t.get("background_color", "#f4f7fb"))
        base.setAlphaF(t.get("background_opacity", 1.0))
        painter.fillRect(self.rect(), base)
        if self.image is not None and self.width() and self.height():
            fit = t.get("image_fit", "cover")
            key = (self.width(), self.height(), fit, self._image_key)
            if key != self._render_key:
                size = (self.width(), self.height())
                if fit == "cover":
                    prepared = ImageOps.fit(self.image, size, Image.Resampling.LANCZOS)
                elif fit == "contain":
                    prepared = ImageOps.contain(self.image, size, Image.Resampling.LANCZOS)
                elif fit == "stretch":
                    prepared = self.image.resize(size, Image.Resampling.LANCZOS)
                else:
                    prepared = self.image
                self._render = QPixmap.fromImage(ImageQt(prepared))
                self._render_key = key
            if self._render:
                painter.setOpacity(t.get("image_opacity", 1.0))
                if fit == "tile":
                    painter.drawTiledPixmap(self.rect(), self._render)
                else:
                    x = (self.width() - self._render.width()) // 2
                    y = (self.height() - self._render.height()) // 2
                    painter.drawPixmap(x, y, self._render)
                painter.setOpacity(1)
        overlay = QColor(t.get("overlay_color", "#ffffff"))
        overlay.setAlphaF(t.get("overlay_opacity", 0))
        painter.fillRect(self.rect(), overlay)
        painter.end()


class TextHalo(QGraphicsEffect):
    """A four-direction text halo, used only on selected text labels."""

    def __init__(self, color: str, opacity: float, size: float, blur: float, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.opacity = opacity
        self.size = size
        self.blur = blur

    def boundingRectFor(self, rect):
        margin = self.size + self.blur * 2 + 1
        return rect.adjusted(-margin, -margin, margin, margin)

    def draw(self, painter):
        offset = QPoint()
        pixmap = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates, offset, QGraphicsEffect.PixmapPadMode.NoPad)
        if pixmap.isNull():
            return
        dpr = pixmap.devicePixelRatio()
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        raw = bytes(image.constBits())
        source_image = Image.frombytes("RGBA", (image.width(), image.height()), raw, "raw", "RGBA", image.bytesPerLine())
        alpha = source_image.getchannel("A")
        margin = round((self.blur * 2 + self.size + 2) * dpr)
        mask = Image.new("L", (alpha.width + margin * 2, alpha.height + margin * 2))
        size = self.size * dpr
        offsets = [(0, 0)] if size == 0 else [(size, 0), (-size, 0), (0, size), (0, -size)]
        from PIL import ImageChops
        for dx, dy in offsets:
            layer = Image.new("L", mask.size)
            layer.paste(alpha, (margin + round(dx), margin + round(dy)))
            mask = ImageChops.lighter(mask, layer)
        if self.blur:
            mask = mask.filter(ImageFilter.GaussianBlur(self.blur * dpr / 2))
        mask = mask.point(lambda value: round(value * self.opacity))
        shadow = Image.new("RGBA", mask.size, self.color.name())
        shadow.putalpha(mask)
        shadow_pixmap = QPixmap.fromImage(ImageQt(shadow))
        shadow_pixmap.setDevicePixelRatio(dpr)
        painter.drawPixmap(QPointF(offset.x() - margin / dpr, offset.y() - margin / dpr), shadow_pixmap)
        painter.drawPixmap(offset, pixmap)


class Card(QFrame):
    def __init__(self, parent=None, *, hero=False):
        super().__init__(parent)
        self.setProperty("card", True)
        if hero:
            self.setProperty("hero", True)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(20, 14, 20, 14)
        self.box.setSpacing(10)


def label(text="", *, muted=False, heading=False, subheading=False, wrap=False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    widget.setProperty("muted", muted)
    widget.setProperty("heading", heading)
    widget.setProperty("subheading", subheading)
    return widget


def page() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.viewport().setObjectName("pageViewport")
    area.viewport().setAutoFillBackground(False)
    body = QWidget()
    body.setObjectName("page")
    body.setAutoFillBackground(False)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(26, 24, 26, 18)
    layout.setSpacing(16)
    area.setWidget(body)
    return area, body, layout


def section_title(title: str, description: str = "") -> QWidget:
    row = QWidget()
    box = QHBoxLayout(row)
    box.setContentsMargins(0, 0, 0, 0)
    box.addWidget(label(title, heading=True))
    box.addStretch()
    if description:
        box.addWidget(label(description, muted=True))
    return row


def table(headers: list[str], stretch_column: int = 0) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setShowGrid(False)
    widget.setAlternatingRowColors(False)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setTextElideMode(Qt.TextElideMode.ElideRight)
    header = widget.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for index in range(len(headers)):
        header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    header.setMinimumSectionSize(48)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return widget


def status_label(text: str, state: str = "muted") -> QLabel:
    widget = label(text)
    set_status(widget, text, state)
    return widget


def set_status(widget: QLabel, text: str, state: str = "muted"):
    widget.setText(text)
    widget.setProperty("statusTone", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def app_icon() -> QIcon:
    # PyInstaller preserves this module under _internal/dance_ui and bundles
    # the artwork under _internal/assets, matching the source checkout layout.
    path = Path(__file__).resolve().parents[1] / "assets" / "app-icon.png"
    if not path.is_file():
        raise FileNotFoundError(f"程序图标文件不存在：{path}")
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        raise ValueError(f"无法读取程序图标：{path}")
    return QIcon(pixmap)


def bytes_text(value: float) -> str:
    value = max(0, float(value or 0))
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or suffix == "TB":
            return f"{value:.1f} {suffix}" if suffix != "B" else f"{value:.0f} B"
        value /= 1024
    return ""
