"""Qt appearance editor.  The owning window renders the actual application theme."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, Signal, QSignalBlocker, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QFont, QImageReader
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QToolButton, QLineEdit, QComboBox,
    QSlider, QDoubleSpinBox, QCheckBox, QVBoxLayout, QHBoxLayout, QScrollArea,
    QColorDialog, QFileDialog, QSizePolicy, QMenu, QApplication,
)

from .theme import ThemeStore, ThemeError, NUMBER_RANGES, normalize_color


def _muted(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("muted", True)
    label.setWordWrap(True)
    return label


def _button(text: str, callback: Callable, *, primary: bool = False) -> QPushButton:
    result = QPushButton(text)
    if primary:
        result.setProperty("primary", True)
    result.clicked.connect(callback)
    return result


class _ColorField(QWidget):
    edited = Signal(str)
    invalid = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._value = "#FFFFFF"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.swatch = QPushButton()
        self.swatch.setObjectName("colorSwatch")
        self.swatch.setFixedSize(34, 30)
        self.swatch.setToolTip("打开颜色选择器")
        self.swatch.setAccessibleName("选择颜色")
        self.hex = QLineEdit()
        self.hex.setMaxLength(12)
        self.hex.setMaximumWidth(140)
        self.hex.setMinimumWidth(98)
        self.hex.setPlaceholderText("#RRGGBB")
        self.hex.setAccessibleName("HEX 色值")
        layout.addStretch(1)
        layout.addWidget(self.swatch)
        layout.addWidget(self.hex)
        self.swatch.clicked.connect(self._choose)
        self.hex.editingFinished.connect(self._typed)
        self.set_value(self._value)

    def set_value(self, value: str) -> None:
        self._value = value
        with QSignalBlocker(self.hex):
            self.hex.setText(value)
        # This tiny swatch intentionally renders the selected color even while
        # the application-wide stylesheet is changing around the editor.
        self.swatch.setStyleSheet(
            "QPushButton#colorSwatch { background: " + value + "; border: 1px solid #8A93A5; border-radius: 6px; padding: 0px; }"
        )
        self.swatch.setToolTip("选择颜色 · " + value)

    def _typed(self) -> None:
        try:
            value = normalize_color(self.hex.text())
        except ThemeError as error:
            self.invalid.emit(str(error))
            self.set_value(self._value)
            return
        if value != self._value:
            self.set_value(value)
            self.edited.emit(value)
        else:
            self.set_value(value)

    def _choose(self) -> None:
        color = QColorDialog.getColor(QColor(self._value), self, "选择颜色")
        if color.isValid():
            value = color.name(QColor.NameFormat.HexRgb).upper()
            if value != self._value:
                self.set_value(value)
                self.edited.emit(value)


class _SliderField(QWidget):
    edited = Signal(float)
    edit_started = Signal()
    edit_finished = Signal()

    def __init__(self, low: float, high: float, *, percentage: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.percentage = percentage
        self.factor = 100 if percentage else 10
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(low * self.factor), round(high * self.factor))
        self.slider.setSingleStep(1 if percentage else 5)
        self.slider.setPageStep(10 if percentage else 10)
        self.slider.setMinimumWidth(120)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(0 if percentage else 1)
        self.spin.setRange(low * (100 if percentage else 1), high * (100 if percentage else 1))
        self.spin.setSingleStep(1 if percentage else 0.5)
        self.spin.setSuffix(" %" if percentage else " px")
        self.spin.setFixedWidth(96)
        self.spin.setKeyboardTracking(False)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)
        self.slider.valueChanged.connect(self._slid)
        self.spin.valueChanged.connect(self._spun)
        self.slider.sliderPressed.connect(self.edit_started)
        self.slider.sliderReleased.connect(self.edit_finished)
        self.spin.editingFinished.connect(self.edit_finished)

    def set_value(self, value: float) -> None:
        with QSignalBlocker(self.slider), QSignalBlocker(self.spin):
            self.slider.setValue(round(value * self.factor))
            self.spin.setValue(value * (100 if self.percentage else 1))

    def _slid(self, value: int) -> None:
        result = value / self.factor
        with QSignalBlocker(self.spin):
            self.spin.setValue(result * (100 if self.percentage else 1))
        self.edited.emit(result)

    def _spun(self, value: float) -> None:
        result = value / (100 if self.percentage else 1)
        with QSignalBlocker(self.slider):
            self.slider.setValue(round(result * self.factor))
        self.edited.emit(result)


class _Section(QFrame):
    """One-level disclosure card; keeps all field widgets alive while folded."""

    def __init__(self, title: str, description: str, *, expanded: bool = False, reset: Callable | None = None):
        super().__init__()
        self.setProperty("card", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.setProperty("sectionHeader", True)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toggle.setAccessibleName(title + "，展开或收起")
        header.addWidget(self.toggle, 1)
        if reset is not None:
            reset_button = _button("重置本组", reset)
            reset_button.setProperty("subtle", True)
            reset_button.setToolTip("恢复本组初始设置，可撤销")
            header.addWidget(reset_button)
        layout.addLayout(header)
        self.description = _muted(description)
        layout.addWidget(self.description)
        self.body = QWidget()
        self.fields = QVBoxLayout(self.body)
        self.fields.setContentsMargins(0, 6, 0, 2)
        self.fields.setSpacing(16)
        layout.addWidget(self.body)
        self.body.setVisible(expanded)
        self.toggle.toggled.connect(self._toggle)

    def _toggle(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


class _ThemePreview(QWidget):
    """A small schematic preview; the full window remains the live preview."""

    def __init__(self, store: ThemeStore):
        super().__init__()
        self.store = store
        self._image_key = None
        self._image = QPixmap()
        self.setMinimumSize(190, 132)
        self.setMaximumWidth(270)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("当前主题界面示意预览")

    @staticmethod
    def _color(value: str, opacity: float = 1.0) -> QColor:
        color = QColor(value)
        color.setAlphaF(max(0.0, min(1.0, opacity)))
        return color

    def paintEvent(self, event) -> None:
        theme = self.store.active
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(1, 1, self.width() - 2, self.height() - 2)
        radius = min(12.0, float(theme["radius"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0F1420" if theme["base"] == "dark" else "#F3F5F9"))
        painter.drawRoundedRect(outer, radius, radius)
        painter.setBrush(self._color(theme["background_color"], theme["background_opacity"]))
        painter.drawRoundedRect(outer, radius, radius)
        image_path = self.store.background_path(theme)
        image_key = str(image_path) if image_path else None
        if image_key != self._image_key:
            self._image_key = image_key
            self._image = QPixmap()
            if image_key:
                reader = QImageReader(image_key)
                reader.setAutoTransform(True)
                reader.setScaledSize(reader.size().scaled(540, 360, Qt.AspectRatioMode.KeepAspectRatio))
                self._image = QPixmap.fromImage(reader.read())
        if not self._image.isNull():
            # Keep this thumbnail inexpensive during slider changes; the owner
            # renders fit and blur precisely on the full application backdrop.
            from PySide6.QtGui import QPainterPath
            clip = QPainterPath()
            clip.addRoundedRect(outer, radius, radius)
            painter.save()
            painter.setClipPath(clip)
            painter.setOpacity(theme["image_opacity"])
            scaled = self._image.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
            painter.restore()
        painter.setBrush(self._color(theme["overlay_color"], theme["overlay_opacity"]))
        painter.drawRoundedRect(outer, radius, radius)
        nav = QRectF(12, 12, self.width() - 24, 26)
        painter.setBrush(self._color(theme["nav_background"], theme["nav_opacity"]))
        painter.drawRoundedRect(nav, 5, 5)
        tab_width = (self.width() - 36) / 4
        selected = QRectF(17, 16, tab_width, 18)
        small_font = QFont(self.font())
        small_font.setPixelSize(min(12, max(9, round(self.width() / 22))))
        painter.setFont(small_font)
        painter.setBrush(self._color(theme["nav_active_background"], theme["nav_active_opacity"]))
        painter.drawRoundedRect(selected, 4, 4)
        painter.setPen(self._color(theme["nav_active_text"]))
        painter.drawText(selected, Qt.AlignmentFlag.AlignCenter, "运行")
        painter.setPen(self._color(theme["nav_text"]))
        for index, label in enumerate(("存储", "设置", "外观"), start=1):
            painter.drawText(QRectF(17 + index * tab_width, 16, tab_width, 18), Qt.AlignmentFlag.AlignCenter, label)
        card = QRectF(12, 48, self.width() - 24, self.height() - 61)
        painter.setPen(QPen(self._color(theme["border_color"], theme["border_opacity"]), min(3, theme["border_width"])))
        painter.setBrush(self._color(theme["card_background"], theme["card_opacity"]))
        painter.drawRoundedRect(card, min(8, radius), min(8, radius))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme["text_primary"]))
        painter.drawRoundedRect(QRectF(24, 62, 80, 5), 2, 2)
        painter.setBrush(QColor(theme["text_secondary"]))
        painter.drawRoundedRect(QRectF(24, 74, self.width() - 78, 3), 1, 1)
        painter.drawRoundedRect(QRectF(24, 82, self.width() - 98, 3), 1, 1)
        painter.setBrush(QColor(theme["accent"]))
        painter.drawRoundedRect(QRectF(24, self.height() - 34, 52, 10), 3, 3)
        painter.end()


class AppearancePage(QWidget):
    """Theme editor with stable widgets and a signal for application-wide repaint."""

    changed = Signal()

    def __init__(self, store: ThemeStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self._refreshing = False
        self._fields: dict[str, Any] = {}
        self._preference_fields: dict[str, Any] = {}
        self._sections: list[_Section] = []
        self._metrics_key = None
        self._pending: dict[str, Any] = {}
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.setInterval(60)
        self._pending_timer.timeout.connect(self._flush_pending)
        if QApplication.instance() is not None:
            QApplication.instance().aboutToQuit.connect(self._finish_edit)
        self.setObjectName("appearancePage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        labels = QVBoxLayout()
        labels.setSpacing(4)
        title = QLabel("外观")
        title.setProperty("pageTitle", True)
        labels.addWidget(title)
        labels.addWidget(_muted("配色、背景和阅读习惯，都按你的喜好调整。"))
        heading.addLayout(labels, 1)
        self.undo_button = _button("撤销", self._undo)
        self.undo_button.setToolTip("撤销最近一次外观更改；拖动一次滑块算一步")
        heading.addWidget(self.undo_button)
        self.reset_button = QPushButton("重置")
        reset_menu = QMenu(self.reset_button)
        reset_menu.addAction("重置当前主题", self._reset_current)
        reset_menu.addAction("重置阅读偏好", self._reset_preferences)
        self.reset_button.setMenu(reset_menu)
        heading.addWidget(self.reset_button)
        layout.addLayout(heading)

        self.status = _muted("更改会自动保存。")
        self.status.setObjectName("appearanceStatus")
        self.status.setMinimumHeight(22)
        layout.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setObjectName("appearanceScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll = scroll
        content = QWidget()
        content.setObjectName("appearanceContent")
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 8, 12)
        self.content_layout.setSpacing(14)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self._build_theme_card()
        self._build_background()
        self._build_colors()
        self._build_navigation()
        self._build_cards()
        self._build_text()
        self._build_preferences()
        self.content_layout.addStretch(1)
        self.refresh()
        if self.store.warnings:
            self._error(self.store.warnings[0])

    def _build_theme_card(self) -> None:
        card = QFrame()
        card.setProperty("card", True)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(22)
        self.preview = _ThemePreview(self.store)
        layout.addWidget(self.preview, 1)
        right = QVBoxLayout()
        right.setSpacing(10)
        top = QHBoxLayout()
        label = QLabel("当前主题")
        label.setProperty("strong", True)
        top.addWidget(label)
        top.addStretch(1)
        self.theme_kind = _muted("")
        top.addWidget(self.theme_kind)
        right.addLayout(top)
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("选择主题")
        self.theme_combo.currentIndexChanged.connect(self._select_theme)
        right.addWidget(self.theme_combo)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(60)
        self.name_edit.setAccessibleName("主题名称")
        self.name_edit.setPlaceholderText("为主题取个名字")
        self.name_edit.editingFinished.connect(self._rename)
        name_row.addWidget(self.name_edit, 1)
        right.addLayout(name_row)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(_button("新建主题", self._create, primary=True))
        actions.addWidget(_button("复制当前", self._copy))
        self.delete_button = _button("删除", self._delete)
        self.delete_button.setProperty("danger", True)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        right.addLayout(actions)
        right.addWidget(_muted("浅色与深色可分别编辑。复制主题可保留当前方案。"))
        layout.addLayout(right, 2)
        self.content_layout.addWidget(card)

    def _section(self, title: str, description: str, group: str, *, expanded: bool = False) -> _Section:
        section = _Section(title, description, expanded=expanded, reset=lambda: self._reset_group(group))
        self._sections.append(section)
        self.content_layout.addWidget(section)
        return section

    @staticmethod
    def _row(section: _Section, title: str, control: QWidget, hint: str = "") -> None:
        row = QWidget()
        row.setProperty("settingRow", True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        description = QWidget()
        labels = QVBoxLayout(description)
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(3)
        label = QLabel(title)
        label.setProperty("strong", True)
        label.setWordWrap(True)
        labels.addWidget(label)
        if hint:
            labels.addWidget(_muted(hint))
        description.setMinimumWidth(160)
        layout.addWidget(description, 2)
        layout.addWidget(control, 3)
        section.fields.addWidget(row)

    def _color(self, section: _Section, key: str, title: str, hint: str = "") -> None:
        field = _ColorField()
        field.hex.setAccessibleName(title + " HEX 色值")
        field.swatch.setAccessibleName("选择" + title)
        field.edited.connect(lambda value: self._change({key: value}))
        field.invalid.connect(self._error)
        self._fields[key] = field
        self._row(section, title, field, hint)

    def _slider(self, section: _Section, key: str, title: str, hint: str = "") -> None:
        low, high = NUMBER_RANGES[key]
        field = _SliderField(low, high, percentage=key.endswith("opacity"))
        field.slider.setAccessibleName(title)
        field.spin.setAccessibleName(title + "数值")
        field.edited.connect(lambda value: self._queue_change(key, value))
        field.edit_started.connect(self.store.begin_edit)
        field.edit_finished.connect(self._finish_edit)
        self._fields[key] = field
        self._row(section, title, field, hint)

    def _build_background(self) -> None:
        section = self._section("背景", "使用纯色或自己的图片；所有变化都会即时应用到窗口。", "background", expanded=True)
        self._color(section, "background_color", "背景底色")
        self._slider(section, "background_opacity", "底色不透明度", "调节底色与主题基础底色的混合程度")
        images = QWidget()
        image_layout = QVBoxLayout(images)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(8)
        image_actions = QHBoxLayout()
        image_actions.addStretch(1)
        image_actions.addWidget(_button("导入图片", self._import_background))
        self.clear_image = _button("移除图片", self._remove_background)
        image_actions.addWidget(self.clear_image)
        image_layout.addLayout(image_actions)
        self.image_status = _muted("")
        self.image_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        image_layout.addWidget(self.image_status)
        self._row(section, "背景图片", images, "PNG / JPG / WEBP，最大 20 MB")
        fit = QComboBox()
        for label, value in (("填满 · 等比裁切", "cover"), ("完整显示 · 等比", "contain"), ("拉伸铺满", "stretch"), ("居中原尺寸", "center"), ("平铺", "tile")):
            fit.addItem(label, value)
        fit.setAccessibleName("图片适应方式")
        fit.currentIndexChanged.connect(lambda index: self._change({"image_fit": fit.itemData(index)}) if index >= 0 else None)
        self._fields["image_fit"] = fit
        self._row(section, "图片适应", fit)
        self._slider(section, "image_opacity", "图片不透明度")
        self._slider(section, "image_blur", "图片模糊", "背景细节过多时，可以适当增加模糊")
        self._color(section, "overlay_color", "遮罩颜色")
        self._slider(section, "overlay_opacity", "遮罩不透明度", "给背景叠加颜色，帮助内容保持清晰")

    def _build_colors(self) -> None:
        section = self._section("常用颜色", "先调整强调色与文字，建立清晰一致的阅读层级。", "colors", expanded=True)
        self._color(section, "accent", "强调色", "主要操作、进度与选中状态")
        self._color(section, "text_primary", "主要文字")
        self._color(section, "text_secondary", "辅助文字", "说明、单位和次要信息")

    def _build_navigation(self) -> None:
        section = self._section("导航", "分别控制导航底色、文字以及选中项。", "navigation")
        self._color(section, "nav_background", "导航背景")
        self._slider(section, "nav_opacity", "导航不透明度")
        self._color(section, "nav_text", "导航文字")
        self._color(section, "nav_active_background", "选中项背景")
        self._slider(section, "nav_active_opacity", "选中项不透明度")
        self._color(section, "nav_active_text", "选中项文字")

    def _build_cards(self) -> None:
        section = self._section("卡片与边框", "调节内容面板的层次、透明程度和轮廓。", "cards")
        self._color(section, "card_background", "卡片背景")
        self._slider(section, "card_opacity", "卡片不透明度")
        self._color(section, "border_color", "边框颜色")
        self._slider(section, "border_opacity", "边框不透明度")
        self._slider(section, "border_width", "边框粗细", "设为 0 可以隐藏边框")
        self._slider(section, "radius", "圆角大小")

    def _build_text(self) -> None:
        section = self._section("文字效果", "为当前歌曲标题增加柔和轮廓，改善复杂背景上的辨识度。", "text")
        self._color(section, "text_shadow_color", "阴影颜色")
        self._slider(section, "text_shadow_opacity", "阴影不透明度", "设为 0 可以关闭文字阴影")
        self._slider(section, "text_shadow_size", "扩展距离", "标题文字周围轮廓的扩展程度")
        self._slider(section, "text_shadow_blur", "阴影模糊")

    def _build_preferences(self) -> None:
        section = _Section("阅读偏好", "独立于主题保存；切换主题时会继续保留。", reset=self._reset_preferences)
        self._sections.append(section)
        self.content_layout.addWidget(section)
        scale = QComboBox()
        for value in (100, 110, 125, 150):
            scale.addItem(str(value) + "%", value)
        scale.setAccessibleName("界面缩放")
        scale.currentIndexChanged.connect(lambda index: self._change_preferences({"scale": scale.itemData(index)}) if index >= 0 else None)
        self._preference_fields["scale"] = scale
        self._row(section, "界面缩放", scale, "统一调整文字、控件和间距")
        density = QComboBox()
        for label, value in (("舒适", "comfortable"), ("标准", "standard"), ("紧凑", "compact")):
            density.addItem(label, value)
        density.setAccessibleName("信息密度")
        density.currentIndexChanged.connect(lambda index: self._change_preferences({"density": density.itemData(index)}) if index >= 0 else None)
        self._preference_fields["density"] = density
        self._row(section, "信息密度", density, "选择列表与面板的留白程度")
        logs = QCheckBox("默认展开")
        logs.setAccessibleName("诊断日志默认展开")
        logs.toggled.connect(lambda checked: self._change_preferences({"logs_expanded": checked}))
        self._preference_fields["logs_expanded"] = logs
        self._row(section, "诊断日志", logs, "进入运行页时的日志面板状态")

    def _queue_change(self, key: str, value: Any) -> None:
        if not self._refreshing:
            self._pending[key] = value
            self._pending_timer.start()

    def _flush_pending(self) -> bool:
        self._pending_timer.stop()
        if not self._pending:
            return True
        values, self._pending = self._pending, {}
        return self._perform(lambda: self.store.change(values))

    def _finish_edit(self) -> None:
        self._flush_pending()
        self.store.end_edit()

    def _change(self, values: dict[str, Any]) -> None:
        if self._refreshing:
            return
        if self._flush_pending():
            self._perform(lambda: self.store.change(values))

    def _change_preferences(self, values: dict[str, Any]) -> None:
        if self._refreshing:
            return
        if self._flush_pending():
            self._perform(lambda: self.store.change_preferences(values))

    def _perform(self, operation: Callable, *, refresh: bool = False, message: str = "已保存。") -> bool:
        try:
            result = operation()
        except (OSError, ValueError) as error:
            # Revert only values, keeping focus, expanded sections, and the page
            # instance intact.  A failed write never changes ThemeStore state.
            if refresh:
                self.refresh()
            else:
                self._sync_fields()
                self._update_summary()
            self._error("未能保存：" + str(error))
            return False
        if refresh:
            self.refresh()
        else:
            self._update_summary()
        self._set_status(message)
        if result is not False:
            self.changed.emit()
        return True

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status.setText(message)
        self.status.setProperty("danger", error)
        self.status.setProperty("muted", not error)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _error(self, message: str) -> None:
        self._set_status(message, error=True)

    def _update_summary(self) -> None:
        self._apply_metrics()
        active = self.store.active
        self.undo_button.setEnabled(self.store.can_undo)
        self.delete_button.setEnabled(not active["builtin"])
        self.delete_button.setToolTip("内置主题可以编辑和重置，需要保留。" if active["builtin"] else "删除此主题，可通过撤销恢复。")
        self.theme_kind.setText("内置主题 · 可编辑" if active["builtin"] else "自定义主题")
        image = self.store.background_path(active)
        if image is not None:
            self.image_status.setText(image.suffix.lstrip(".").upper() + " 背景 · 已保存在应用内")
            self.image_status.setToolTip(str(image))
        elif active["background_image"]:
            self.image_status.setText("图片已丢失，当前使用背景底色")
            self.image_status.setToolTip("重新导入图片即可恢复背景")
        else:
            self.image_status.setText("当前使用纯色背景")
            self.image_status.setToolTip("")
        self.clear_image.setEnabled(bool(active["background_image"]))
        for key in ("image_fit", "image_opacity", "image_blur"):
            self._fields[key].setEnabled(image is not None)
            self._fields[key].setToolTip("" if image is not None else "请先导入背景图片")
        self.preview.update()

    def _apply_metrics(self) -> None:
        preferences = self.store.preferences
        key = (preferences["scale"], preferences["density"])
        if key == self._metrics_key:
            return
        self._metrics_key = key
        scale = preferences["scale"] / 100
        spacing = {"comfortable": 20, "standard": 16, "compact": 10}[preferences["density"]]
        for field in self._fields.values():
            if isinstance(field, _ColorField):
                field.swatch.setFixedSize(round(34 * scale), round(30 * scale))
                field.hex.setMaximumWidth(round(150 * scale))
                field.hex.setMinimumWidth(round(110 * scale))
            elif isinstance(field, _SliderField):
                field.spin.setFixedWidth(round(100 * scale))
                field.slider.setMinimumWidth(round(120 * scale))
        for section in self._sections:
            section.fields.setSpacing(round(spacing * scale))
        self.content_layout.setSpacing(round(14 * scale))

    def _sync_fields(self) -> None:
        self._refreshing = True
        try:
            active = self.store.active
            for key, field in self._fields.items():
                if isinstance(field, (_ColorField, _SliderField)):
                    field.set_value(active[key])
                elif isinstance(field, QComboBox):
                    with QSignalBlocker(field):
                        field.setCurrentIndex(field.findData(active[key]))
            preferences = self.store.preferences
            for key, field in self._preference_fields.items():
                with QSignalBlocker(field):
                    if isinstance(field, QComboBox):
                        field.setCurrentIndex(field.findData(preferences[key]))
                    elif isinstance(field, QCheckBox):
                        field.setChecked(preferences[key])
        finally:
            self._refreshing = False

    def refresh(self) -> None:
        """Synchronize after external theme changes without rebuilding the editor."""
        self._refreshing = True
        try:
            active = self.store.active
            with QSignalBlocker(self.theme_combo):
                self.theme_combo.clear()
                for theme in self.store.themes:
                    self.theme_combo.addItem(theme["name"], theme["id"])
                self.theme_combo.setCurrentIndex(self.theme_combo.findData(active["id"]))
            with QSignalBlocker(self.name_edit):
                self.name_edit.setText(active["name"])
        finally:
            self._refreshing = False
        self._sync_fields()
        self._update_summary()

    sync_from_store = refresh

    def _select_theme(self, index: int) -> None:
        if self._refreshing or index < 0:
            return
        theme_id = self.theme_combo.itemData(index)
        if self._flush_pending():
            self._perform(lambda: self.store.select(theme_id), refresh=True, message="已切换主题。")

    def _rename(self) -> None:
        if self._refreshing:
            return
        name = self.name_edit.text()
        if name == self.store.active["name"]:
            return
        if self._flush_pending():
            self._perform(lambda: self.store.rename(self.store.active_id, name), refresh=True, message="主题名称已保存。")
            with QSignalBlocker(self.name_edit):
                self.name_edit.setText(self.store.active["name"])

    def _create(self) -> None:
        if self._flush_pending():
            if self._perform(lambda: self.store.create(), refresh=True, message="新主题已创建，可以修改名称和配色。"):
                self.name_edit.setFocus()
                self.name_edit.selectAll()

    def _copy(self) -> None:
        if self._flush_pending():
            if self._perform(lambda: self.store.create(copy_current=True), refresh=True, message="已复制当前主题。"):
                self.name_edit.setFocus()
                self.name_edit.selectAll()

    def _delete(self) -> None:
        if self._flush_pending():
            self._perform(lambda: self.store.delete(self.store.active_id), refresh=True, message="主题已删除，可撤销恢复。")

    def _undo(self) -> None:
        if self._flush_pending():
            self._perform(self.store.undo, refresh=True, message="已撤销最近一次更改。")

    def _reset_current(self) -> None:
        if self._flush_pending():
            self._perform(self.store.reset_current, refresh=True, message="已恢复当前主题的基础配色，可撤销。")

    def _reset_group(self, group: str) -> None:
        if self._flush_pending():
            self._perform(lambda: self.store.reset_group(group), refresh=True, message="已恢复本组初始设置，可撤销。")

    def _reset_preferences(self) -> None:
        if self._flush_pending():
            self._perform(self.store.reset_preferences, refresh=True, message="阅读偏好已恢复初始设置，可撤销。")

    def _import_background(self) -> None:
        if not self._flush_pending():
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择背景图片", str(Path.home()), "背景图片 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._perform(lambda: self.store.import_background(path), refresh=True, message="背景图片已导入；原图片不会被修改。")

    def _remove_background(self) -> None:
        if self._flush_pending():
            self._perform(lambda: self.store.change({"background_image": ""}), refresh=True, message="已移除背景，恢复纯色显示。")

    def closeEvent(self, event) -> None:
        self._finish_edit()
        super().closeEvent(event)
