"""Native Qt Widgets workbench for HWCDQ inspection and control."""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from hwcdq import DiagnosticLogger, PIDZOOM_HW178P
from hwcdq.profile import (
    APP_DISPLAY_NAME,
    MODEL_MIN_CURRENT_A,
    MODEL_MIN_VOLTAGE_V,
)

from .contracts import ControllerProtocol
from .dialogs import SetpointConfirmationDialog, StartConfirmationDialog
from .theme import mono_font, stylesheet
from .widgets import ReadingWidget, StatusBadge


_SECRET_KEYS = {"password", "passcode", "secret", "token", "credential"}
_LAST_DEVICE_KEY = "ble/lastDeviceIdentifier"


def _field(payload: Any, key: str, default: Any = None) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).lower() if raw is not None else ""


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _effective_limits(config: Any) -> Any:
    """Delegate all HW178P/device-envelope math to the shared library."""

    return PIDZOOM_HW178P.effective_limits(
        config if isinstance(config, Mapping) else None
    )


def _canonical_float32(value: Any) -> bytes | None:
    """Return the exact HWCDQ setpoint representation or fail closed."""

    converted = _finite_number(value)
    if converted is None or converted <= 0:
        return None
    try:
        encoded = struct.pack("<f", converted)
        rounded = struct.unpack("<f", encoded)[0]
    except (OverflowError, struct.error):
        return None
    if not math.isfinite(rounded) or rounded <= 0:
        return None
    return encoded


def _redact(value: Any, *, key: str = "") -> Any:
    """Defensively redact common secret fields from log UI and exports."""

    if any(marker in key.lower() for marker in _SECRET_KEYS):
        return "<СКРЫТО>"
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {str(k): _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, bytes):
        return value.hex(" ").upper()
    return value


def _stringify(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, bytes):
        return value.hex(" ").upper()
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(_redact(value), ensure_ascii=False, sort_keys=True)
    return str(value)


def _identity_text(value: Any) -> str:
    """Render identity fields as bytes because their encoding is unproven."""

    if isinstance(value, bytes):
        return value.hex(" ").upper() if value else "—"
    return _stringify(value)


class MainWindow(QMainWindow):
    """Bench-control window driven entirely by an injected controller QObject."""

    def __init__(
        self,
        controller: ControllerProtocol,
        *,
        initial_mode: str = "monitoring",
        diagnostics: DiagnosticLogger | None = None,
        settings: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._diagnostics = diagnostics
        self._mode_kind = initial_mode
        self._snapshot: Any = {}
        self._operation_busy = False
        self._start_pending = False
        self._stop_pending = False
        self._output_action: str | None = None
        self._output_allowed = False
        self._output_reason = "состояние выхода ещё не определено"
        self._scanning = False
        self._devices: list[Any] = []
        self._settings = (
            None
            if initial_mode in {"simulation", "simulator"}
            else settings
        )
        self._last_device_identifier = self._read_last_device_identifier()
        self._pending_connection_identifier: str | None = None
        self._manual_device_selection = False
        self._log_entries: list[dict[str, Any]] = []
        self._setting_spinboxes = False
        self._last_config_targets: tuple[float | None, float | None] | None = None
        self._last_rendered_state: tuple[str, bool, bool] | None = None

        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1220, 800)
        # The workbench intentionally favours exact, arm's-length readings over
        # collapsing into a cramped dashboard.  Smaller windows would make
        # engineering units collide with their values.
        self.setMinimumSize(1100, 720)
        self.setStyleSheet(stylesheet())

        self._build_actions()
        self._build_ui()
        self._connect_controller()
        self._apply_mode({"kind": initial_mode})
        self._apply_snapshot({})
        self._sync_debug_badge()
        self._diag("window_opened", mode=self._mode_kind)

    # --------------------------------------------------------- diagnostics
    def _diag(self, event: str, **details: Any) -> None:
        """Best-effort semantic diagnostics which can never affect UI control."""

        logger = self._diagnostics
        if logger is None:
            return
        try:
            logger.emit("ui", event, **details)
        except BaseException:
            # A third-party/fake logger may not honour DiagnosticLogger's
            # non-throwing contract.  Charger controls must still work.
            pass
        self._sync_debug_badge()

    def _sync_debug_badge(self) -> None:
        badge = getattr(self, "debug_badge", None)
        if badge is None:
            return
        logger = self._diagnostics
        try:
            enabled = bool(logger is not None and logger.enabled)
            badge.setVisible(enabled)
            if not enabled:
                badge.set_status("выключен", "neutral")
                badge.setToolTip("Диагностический журнал выключен")
                return

            path = logger.path
            path_text = str(path) if path is not None else "путь не задан"
            if not logger.healthy or not logger.active:
                badge.set_status("ошибка журнала", "danger")
                error = logger.error or "журнал недоступен"
                badge.setToolTip(f"{path_text}\n{error}")
                return
            badge.set_status(path.name if path is not None else "активен", "ok")
            badge.setToolTip(f"Диагностический журнал:\n{path_text}")
        except BaseException:
            # Even property access on an injected logger is treated as
            # diagnostic-only.  Surface the failure without blocking the UI.
            badge.setVisible(True)
            badge.set_status("ошибка журнала", "danger")
            badge.setToolTip("Не удалось прочитать состояние диагностического журнала")

    # ------------------------------------------------------------- preferences
    def _read_last_device_identifier(self) -> str | None:
        settings = self._settings
        if settings is None:
            return None
        try:
            value = settings.value(_LAST_DEVICE_KEY, "")
        except BaseException:
            return None
        if not isinstance(value, str):
            return None
        identifier = value.strip()
        return identifier or None

    def _remember_authenticated_device(self, identifier: str) -> None:
        settings = self._settings
        if settings is None or not identifier:
            return
        try:
            # This is intentionally the only preference written by the UI.
            settings.setValue(_LAST_DEVICE_KEY, identifier)
        except BaseException:
            return
        self._last_device_identifier = identifier
        self._diag("last_device_remembered", identifier=identifier)

    # ------------------------------------------------------------------ build
    def _build_actions(self) -> None:
        self.refresh_action = QAction("Обновить состояние", self)
        self.refresh_action.setObjectName("refreshAction")
        self.refresh_action.setShortcut(QKeySequence.Refresh)
        self.refresh_action.triggered.connect(self._request_refresh_shortcut)
        self.addAction(self.refresh_action)

        self.stop_action = QAction("Остановить выход", self)
        self.stop_action.setObjectName("stopAction")
        self.stop_action.setShortcut(QKeySequence("Ctrl+Shift+."))
        self.stop_action.triggered.connect(self._request_stop_shortcut)
        self.addAction(self.stop_action)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("rootSurface")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_header())

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 16)
        body_layout.setSpacing(10)

        self.mode_banner = QLabel()
        self.mode_banner.setObjectName("modeBanner")
        self.mode_banner.setAccessibleName("Текущий режим работы")
        body_layout.addWidget(self.mode_banner)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.addTab(self._build_workbench_tab(), "Рабочая панель")
        self.tabs.addTab(self._build_gatt_tab(), "GATT и транспорт")
        self.tabs.addTab(self._build_log_tab(), "Журнал пакетов")
        self.tabs.currentChanged.connect(self._tab_selected)
        body_layout.addWidget(self.tabs, 1)

        root_layout.addWidget(body, 1)
        self.setCentralWidget(root)

        status = QStatusBar()
        status.setObjectName("applicationStatusBar")
        self.operation_label = QLabel("Готово")
        self.operation_label.setObjectName("operationStatus")
        self.operation_label.setProperty("muted", True)
        status.addWidget(self.operation_label, 1)
        self.shortcut_hint = QLabel("F5 — обновить")
        self.shortcut_hint.setObjectName("shortcutHint")
        status.addPermanentWidget(self.shortcut_hint)
        self.setStatusBar(status)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("instrumentHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel(APP_DISPLAY_NAME)
        title.setObjectName("productTitle")
        subtitle = QLabel("Локальный BLE-инструмент · протокол на основе проверяемых данных")
        subtitle.setObjectName("productSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box)
        title_row.addStretch(1)

        self.output_button = QPushButton("■  Управление отключено")
        self.output_button.setObjectName("outputControlButton")
        self.output_button.setProperty("variant", "neutral")
        self.output_button.setMinimumHeight(38)
        self.output_button.setMinimumWidth(230)
        self.output_button.setAccessibleName("Управление выходом зарядного устройства")
        self.output_button.clicked.connect(self._request_output_button)
        # Compatibility name for the Stop-only safety tests and shortcut path;
        # both names point to the same single visible widget.
        self.stop_button = self.output_button
        title_row.addWidget(self.output_button)
        layout.addLayout(title_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.connection_badge = StatusBadge("BLE", object_name="connectionStatus")
        self.auth_badge = StatusBadge("Доступ", object_name="authenticationStatus")
        self.freshness_badge = StatusBadge("Данные", object_name="freshnessStatus")
        self.outcome_badge = StatusBadge("Команда", object_name="outcomeStatus")
        self.debug_badge = StatusBadge("DEBUG", object_name="debugStatus")
        for badge in (
            self.connection_badge,
            self.auth_badge,
            self.freshness_badge,
            self.outcome_badge,
            self.debug_badge,
        ):
            status_row.addWidget(badge, 1)
        self.debug_badge.setVisible(False)
        layout.addLayout(status_row)
        return header

    def _build_workbench_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("workbenchTab")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(self._build_connection_panel())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("workbenchSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_telemetry_panel())
        splitter.addWidget(self._build_control_column())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([700, 440])
        layout.addWidget(splitter, 1)
        return page

    def _build_connection_panel(self) -> QWidget:
        box = QGroupBox("Подключение")
        box.setObjectName("connectionPanel")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(12, 15, 12, 10)
        layout.setSpacing(8)

        self.scan_button = QPushButton("Сканировать")
        self.scan_button.setObjectName("scanButton")
        self.scan_button.clicked.connect(self._toggle_scan)
        layout.addWidget(self.scan_button)

        self.device_combo = QComboBox()
        self.device_combo.setObjectName("deviceSelector")
        self.device_combo.setMinimumWidth(330)
        self.device_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.device_combo.setAccessibleName("Найденные BLE-устройства")
        self.device_combo.activated.connect(self._device_selected)
        layout.addWidget(self.device_combo, 1)

        self.connect_button = QPushButton("Подключить…")
        self.connect_button.setObjectName("connectButton")
        self.connect_button.setProperty("variant", "primary")
        self.connect_button.clicked.connect(self._connect_selected_device)
        layout.addWidget(self.connect_button)

        self.disconnect_button = QPushButton("Отключить")
        self.disconnect_button.setObjectName("disconnectButton")
        self.disconnect_button.clicked.connect(self._request_disconnect)
        layout.addWidget(self.disconnect_button)

        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.clicked.connect(self._request_refresh_button)
        layout.addWidget(self.refresh_button)
        return box

    def _build_telemetry_panel(self) -> QWidget:
        box = QGroupBox("Измерения")
        box.setObjectName("telemetryPanel")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 16, 10, 10)
        layout.setSpacing(8)

        self.output_state_label = QLabel("○ Выход: состояние неизвестно")
        self.output_state_label.setObjectName("outputState")
        self.output_state_label.setProperty("role", "status")
        layout.addWidget(self.output_state_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        specs = (
            ("input_voltage", "Входное напряжение", "V", 2),
            ("input_current", "Входной ток", "A", 2),
            ("input_power_w", "Входная мощность", "W", 1),
            ("output_voltage", "Выходное напряжение", "V", 2),
            ("output_current", "Выходной ток", "A", 2),
            ("output_power_w", "Выходная мощность", "W", 1),
            ("temperature_1", "Температура 1", "°C", 1),
            ("temperature_2", "Температура 2", "°C", 1),
            ("input_frequency", "Частота сети", "Hz", 1),
            ("accumulated_capacity_ah", "Накопленная ёмкость", "Ah", 3),
            ("accumulated_energy_wh", "Накопленная энергия", "Wh", 2),
            ("module_count", "Модули", "шт.", 0),
        )
        self.readings: dict[str, ReadingWidget] = {}
        for index, (key, label, unit, decimals) in enumerate(specs):
            widget = ReadingWidget(
                label,
                unit,
                decimals=decimals,
                object_name=f"reading_{key}",
            )
            self.readings[key] = widget
            grid.addWidget(widget, index // 3, index % 3)
        layout.addLayout(grid)
        layout.addStretch(1)

        self.telemetry_meta = QLabel("Источник: — · возраст: —")
        self.telemetry_meta.setObjectName("telemetryMetadata")
        self.telemetry_meta.setProperty("muted", True)
        layout.addWidget(self.telemetry_meta)
        return box

    def _build_control_column(self) -> QWidget:
        column = QWidget()
        column.setObjectName("controlColumn")
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_targets_panel())
        layout.addWidget(self._build_config_panel(), 1)
        return column

    def _build_targets_panel(self) -> QWidget:
        box = QGroupBox("Целевые параметры")
        box.setObjectName("targetsPanel")
        box.setMinimumHeight(190)
        self.targets_panel = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 15, 12, 10)
        layout.setSpacing(6)

        voltage_row = QHBoxLayout()
        voltage_label = QLabel("Напряжение")
        voltage_label.setMinimumWidth(96)
        self.voltage_input = QDoubleSpinBox()
        self.voltage_input.setObjectName("targetVoltageInput")
        self.voltage_input.setDecimals(2)
        self.voltage_input.setRange(MODEL_MIN_VOLTAGE_V, MODEL_MIN_VOLTAGE_V)
        self.voltage_input.setSuffix(" V")
        self.voltage_input.setKeyboardTracking(False)
        self.voltage_range = QLabel("Лимит: неизвестен")
        self.voltage_range.setObjectName("voltageRangeLabel")
        self.voltage_range.setProperty("muted", True)
        self.set_voltage_button = QPushButton("Записать V")
        self.set_voltage_button.setObjectName("setVoltageButton")
        self.set_voltage_button.clicked.connect(self._request_set_voltage)
        voltage_row.addWidget(voltage_label)
        voltage_row.addWidget(self.voltage_input)
        voltage_row.addWidget(self.voltage_range, 1)
        voltage_row.addWidget(self.set_voltage_button)
        layout.addLayout(voltage_row)

        current_row = QHBoxLayout()
        current_label = QLabel("Ток")
        current_label.setMinimumWidth(96)
        self.current_input = QDoubleSpinBox()
        self.current_input.setObjectName("targetCurrentInput")
        self.current_input.setDecimals(2)
        self.current_input.setRange(MODEL_MIN_CURRENT_A, MODEL_MIN_CURRENT_A)
        self.current_input.setSuffix(" A")
        self.current_input.setKeyboardTracking(False)
        self.current_range = QLabel("Лимит: неизвестен")
        self.current_range.setObjectName("currentRangeLabel")
        self.current_range.setProperty("muted", True)
        self.set_current_button = QPushButton("Записать A")
        self.set_current_button.setObjectName("setCurrentButton")
        self.set_current_button.clicked.connect(self._request_set_current)
        current_row.addWidget(current_label)
        current_row.addWidget(self.current_input)
        current_row.addWidget(self.current_range, 1)
        current_row.addWidget(self.set_current_button)
        layout.addLayout(current_row)

        self.interlock_reason = QLabel("Управление заблокировано: нет подключения")
        self.interlock_reason.setObjectName("interlockReason")
        self.interlock_reason.setWordWrap(True)
        self.interlock_reason.setMinimumHeight(40)
        self.interlock_reason.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.interlock_reason)

        self.voltage_input.valueChanged.connect(self._target_input_changed)
        self.current_input.valueChanged.connect(self._target_input_changed)
        return box

    def _build_config_panel(self) -> QWidget:
        box = QGroupBox("Конфигурация устройства")
        box.setObjectName("configPanel")
        self.config_panel = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 16, 10, 10)

        identity = QGridLayout()
        identity.setHorizontalSpacing(10)
        identity.setVerticalSpacing(3)
        identity.setColumnStretch(1, 1)
        firmware_caption = QLabel("Прошивка:")
        self.firmware_label = QLabel("—")
        self.firmware_label.setObjectName("firmwareValue")
        self.firmware_label.setToolTip("Сырые байты; кодировка ответа не подтверждена")
        self.firmware_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.firmware_label.setWordWrap(True)
        serial_caption = QLabel("Серийный номер:")
        self.serial_label = QLabel("—")
        self.serial_label.setObjectName("serialValue")
        self.serial_label.setToolTip("Сырые бинарные байты серийного номера")
        self.serial_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.serial_label.setWordWrap(True)
        identity.addWidget(firmware_caption, 0, 0, Qt.AlignTop)
        identity.addWidget(self.firmware_label, 0, 1)
        identity.addWidget(serial_caption, 1, 0, Qt.AlignTop)
        identity.addWidget(self.serial_label, 1, 1)
        layout.addLayout(identity)

        self.config_table = QTableWidget(0, 2)
        self.config_table.setObjectName("configTable")
        self.config_table.setHorizontalHeaderLabels(["Поле", "Значение"])
        self.config_table.verticalHeader().setVisible(False)
        self.config_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.config_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.config_table.setAlternatingRowColors(True)
        self.config_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        config_header = self.config_table.horizontalHeader()
        config_header.setMinimumSectionSize(96)
        config_header.setSectionResizeMode(0, QHeaderView.Stretch)
        config_header.setSectionResizeMode(1, QHeaderView.Interactive)
        config_header.resizeSection(1, 180)
        layout.addWidget(self.config_table, 1)
        return box

    def _build_gatt_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("gattTab")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.gatt_status = QLabel("○ Топология ещё не получена")
        self.gatt_status.setObjectName("gattStatus")
        self.gatt_status.setProperty("role", "status")
        layout.addWidget(self.gatt_status)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.gatt_tree = QTreeWidget()
        self.gatt_tree.setObjectName("gattTree")
        self.gatt_tree.setHeaderLabels(["Сервис / характеристика", "Свойства"])
        self.gatt_tree.setAlternatingRowColors(True)
        self.gatt_tree.setFont(mono_font(11))
        self.gatt_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.gatt_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        splitter.addWidget(self.gatt_tree)

        roles_box = QGroupBox("Выбранный транспорт")
        roles_box.setObjectName("selectedGattRoles")
        roles_form = QFormLayout(roles_box)
        roles_form.setContentsMargins(13, 18, 13, 13)
        roles_form.setSpacing(9)
        self.gatt_role_labels: dict[str, QLabel] = {}
        for key, title in (
            ("service_uuid", "Родительский сервис"),
            ("rx_uuid", "RX · уведомления"),
            ("tx_uuid", "TX · команды"),
            ("write_mode", "Тип записи"),
            ("chunk_size", "Размер WNR-фрагмента"),
        ):
            label = QLabel("—")
            label.setObjectName(f"gattRole_{key}")
            label.setFont(mono_font(11))
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setWordWrap(True)
            self.gatt_role_labels[key] = label
            roles_form.addRow(title, label)
        splitter.addWidget(roles_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        note = QLabel(
            "Интерфейс выбирает только единственную пару FFE2 (notify) и FFE3 "
            "(write/WNR) внутри сервиса FFE1. Неоднозначность блокирует управление."
        )
        note.setObjectName("gattSafetyNote")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _build_log_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("packetLogTab")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)

        toolbar = QHBoxLayout()
        self.log_source_label = QLabel("Источник журнала: —")
        self.log_source_label.setObjectName("logSourceLabel")
        self.log_source_label.setProperty("muted", True)
        toolbar.addWidget(self.log_source_label)
        toolbar.addStretch(1)

        self.copy_log_button = QPushButton("Копировать выбранное")
        self.copy_log_button.setObjectName("copyLogButton")
        self.copy_log_button.setEnabled(False)
        self.copy_log_button.clicked.connect(self._copy_selected_log)
        toolbar.addWidget(self.copy_log_button)

        self.export_log_button = QPushButton("Экспортировать…")
        self.export_log_button.setObjectName("exportLogButton")
        self.export_log_button.setEnabled(False)
        self.export_log_button.clicked.connect(self._export_log)
        toolbar.addWidget(self.export_log_button)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        self.log_table = QTableWidget(0, 5)
        self.log_table.setObjectName("packetLogTable")
        self.log_table.setHorizontalHeaderLabels(
            ["Время", "Направление", "Opcode", "Сводка", "Пакет (скрытые данные удалены)"]
        )
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.log_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.log_table.setAlternatingRowColors(True)
        self.log_table.setFont(mono_font(10))
        header = self.log_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.log_table.itemSelectionChanged.connect(self._show_log_details)
        splitter.addWidget(self.log_table)

        self.decoded_log = QPlainTextEdit()
        self.decoded_log.setObjectName("decodedPacketDetails")
        self.decoded_log.setReadOnly(True)
        self.decoded_log.setPlaceholderText("Выберите пакет, чтобы увидеть декодированные поля.")
        self.decoded_log.setFont(mono_font(11))
        splitter.addWidget(self.decoded_log)
        splitter.setSizes([430, 170])
        layout.addWidget(splitter, 1)

        note = QLabel(
            "Пароль и поля с секретами не отображаются и не попадают в буфер обмена "
            "или экспорт. Копирование и сохранение выполняются только явными кнопками."
        )
        note.setObjectName("logRedactionNotice")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _connect_controller(self) -> None:
        connections = (
            ("mode_changed", self._apply_mode),
            ("devices_changed", self._apply_devices),
            ("snapshot_changed", self._apply_snapshot),
            ("gatt_changed", self._apply_gatt),
            ("packet_logged", self._append_log),
            ("operation_changed", self._apply_operation),
        )
        missing: list[str] = []
        for name, handler in connections:
            signal = getattr(self.controller, name, None)
            if signal is None or not hasattr(signal, "connect"):
                missing.append(name)
            else:
                signal.connect(handler)
        if missing:
            raise TypeError(
                "controller is missing required Qt signals: " + ", ".join(missing)
            )

    # ------------------------------------------------------------ UI requests
    @Slot()
    def _toggle_scan(self) -> None:
        if self._scanning:
            self._diag("button_clicked", control="scan", action="stop_scan")
            self.controller.stop_scan()
            self._scanning = False
            self.scan_button.setText("Сканировать")
        else:
            self._diag("button_clicked", control="scan", action="start_scan")
            self._manual_device_selection = False
            self.controller.start_scan()
            self._scanning = True
            self.scan_button.setText("Остановить поиск")

    @Slot(int)
    def _device_selected(self, index: int) -> None:
        self._manual_device_selection = True
        identifier = self.device_combo.itemData(index, Qt.UserRole)
        device = self._devices[index] if 0 <= index < len(self._devices) else None
        self._diag(
            "device_selected",
            identifier=str(identifier) if identifier else None,
            name=str(_field(device, "name") or "Без имени"),
        )

    @Slot(int)
    def _tab_selected(self, index: int) -> None:
        if index < 0:
            return
        page = self.tabs.widget(index)
        self._diag(
            "tab_selected",
            index=index,
            tab=self.tabs.tabText(index),
            object_name=page.objectName() if page is not None else "",
        )

    @Slot()
    def _connect_selected_device(self) -> None:
        index = self.device_combo.currentIndex()
        identifier = self.device_combo.itemData(index, Qt.UserRole)
        self._diag(
            "button_clicked",
            control="connect",
            identifier=str(identifier) if identifier else None,
        )
        if not identifier:
            self._diag("action_blocked", action="connect", reason="no_device_selected")
            return
        self._diag("dialog_opened", dialog="device_authentication")
        dialog = self._create_auth_dialog()
        accepted = dialog.exec() == QDialog.Accepted
        if not accepted:
            self._diag("dialog_rejected", dialog="device_authentication")
            dialog.deleteLater()
            return
        password = dialog.textValue()
        dialog.deleteLater()
        # Deliberately record no password-derived value, length, focus, or
        # contents.  The controller owns the short-lived secret context.
        self._diag("dialog_submitted", dialog="device_authentication")
        self._pending_connection_identifier = str(identifier)
        try:
            self.controller.connect_device(str(identifier), password)
        except BaseException:
            self._pending_connection_identifier = None
            raise
        finally:
            password = ""  # Best effort: do not retain the returned reference.
        if self._scanning:
            self._scanning = False
            self.scan_button.setText("Сканировать")

    def _create_auth_dialog(self) -> QInputDialog:
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Доступ к HWCDQ")
        dialog.setLabelText(
            "Пароль приложения (не Bluetooth PIN). "
            "Пусто — ключ из APK. Не сохраняется."
        )
        dialog.setTextEchoMode(QLineEdit.Password)
        dialog.setOkButtonText("Подключиться")
        dialog.setCancelButtonText("Отмена")
        return dialog

    @Slot()
    def _request_disconnect(self) -> None:
        self._diag("button_clicked", control="disconnect", action="disconnect")
        self._pending_connection_identifier = None
        self.controller.disconnect_device()

    @Slot()
    def _request_refresh_button(self) -> None:
        self._diag("button_clicked", control="refresh", action="refresh")
        self._request_refresh()

    @Slot()
    def _request_refresh_shortcut(self) -> None:
        self._diag("shortcut_triggered", action="refresh", shortcut="F5")
        self._request_refresh()

    def _request_refresh(self) -> None:
        self.controller.refresh()

    @Slot()
    def _request_set_voltage(self) -> None:
        self._diag("button_clicked", control="set_voltage", action="set_voltage")
        config = _field(self._snapshot, "config") or {}
        limits = _effective_limits(config)
        maximum = None if limits is None else limits.voltage.maximum
        value = self.voltage_input.value()
        if limits is None or not limits.voltage.contains(value):
            self._diag("action_blocked", action="set_voltage", reason="invalid_limit_or_value")
            return
        if self._confirm_setpoint("Целевое напряжение", value, maximum, "V"):
            self._diag(
                "command_submitted",
                action="set_voltage",
                value=value,
                maximum=maximum,
                unit="V",
            )
            self.controller.set_voltage(value)

    @Slot()
    def _request_set_current(self) -> None:
        self._diag("button_clicked", control="set_current", action="set_current")
        config = _field(self._snapshot, "config") or {}
        limits = _effective_limits(config)
        maximum = None if limits is None else limits.current.maximum
        value = self.current_input.value()
        if limits is None or not limits.current.contains(value):
            self._diag("action_blocked", action="set_current", reason="invalid_limit_or_value")
            return
        if self._confirm_setpoint("Ограничение тока", value, maximum, "A"):
            self._diag(
                "command_submitted",
                action="set_current",
                value=value,
                maximum=maximum,
                unit="A",
            )
            self.controller.set_current(value)

    def _confirm_setpoint(
        self, title: str, value: float, maximum: float, unit: str
    ) -> bool:
        kind = "voltage" if unit == "V" else "current"
        self._diag("dialog_opened", dialog="setpoint_confirmation", kind=kind)
        dialog = SetpointConfirmationDialog(
            title=title,
            value=value,
            maximum=maximum,
            unit=unit,
            simulated=self._mode_kind == "simulation",
            parent=self,
        )
        accepted = dialog.exec() == QDialog.Accepted
        if accepted:
            self._diag(
                "dialog_submitted",
                dialog="setpoint_confirmation",
                kind=kind,
                value=value,
                maximum=maximum,
                unit=unit,
            )
        else:
            self._diag(
                "dialog_rejected", dialog="setpoint_confirmation", kind=kind
            )
        return accepted

    @Slot(float)
    def _target_input_changed(self, _value: float) -> None:
        if not self._setting_spinboxes:
            self._update_interlocks()

    @Slot()
    def _request_output_button(self) -> None:
        # Recompute before dispatch so a queued signal or input edit cannot use
        # a stale visual state as authorization.
        self._update_interlocks()
        action = self._output_action
        if not self._output_allowed or action not in {"start", "stop"}:
            self._diag(
                "action_blocked",
                action="output_control",
                reason=self._output_reason,
            )
            return
        if action == "stop":
            self._diag("button_clicked", control="output", action="stop_output")
            self._request_stop()
            return

        self._diag("button_clicked", control="output", action="start_output")
        volts = self.voltage_input.value()
        amps = self.current_input.value()
        confirmed_voltage = _canonical_float32(volts)
        confirmed_current = _canonical_float32(amps)
        if not self._confirm_start(volts, amps):
            return

        # The modal runs a nested Qt event loop.  A notification can establish
        # ON, stale data, changed limits, or different displayed values while
        # it is open, so confirmation is never a one-time capability token.
        self._update_interlocks()
        values_unchanged = (
            confirmed_voltage is not None
            and confirmed_current is not None
            and _canonical_float32(self.voltage_input.value()) == confirmed_voltage
            and _canonical_float32(self.current_input.value()) == confirmed_current
        )
        if (
            not values_unchanged
            or not self._output_allowed
            or self._output_action != "start"
        ):
            reason = (
                "показанные V/I изменились во время подтверждения"
                if not values_unchanged
                else self._output_reason
            )
            self._diag("action_blocked", action="start_output", reason=reason)
            return
        self._diag(
            "command_submitted", action="start_output", volts=volts, amps=amps
        )
        self.controller.start_output(volts, amps)

    def _confirm_start(self, volts: float, amps: float) -> bool:
        self._diag("dialog_opened", dialog="start_confirmation")
        dialog = StartConfirmationDialog(
            volts,
            amps,
            simulated=self._mode_kind == "simulation",
            parent=self,
        )
        dialog.acknowledge.toggled.connect(self._safe_load_acknowledgement_changed)
        accepted = dialog.exec() == QDialog.Accepted
        if accepted:
            self._diag(
                "dialog_submitted",
                dialog="start_confirmation",
                volts=volts,
                amps=amps,
            )
        else:
            self._diag("dialog_rejected", dialog="start_confirmation")
        return accepted

    @Slot(bool)
    def _safe_load_acknowledgement_changed(self, acknowledged: bool) -> None:
        self._diag(
            "safe_load_acknowledgement_changed", acknowledged=bool(acknowledged)
        )

    @Slot()
    def _request_stop_shortcut(self) -> None:
        self._diag(
            "shortcut_triggered", action="stop_output", shortcut="Ctrl+Shift+."
        )
        self._request_stop()

    def _request_stop(self) -> None:
        allowed, _label, reason = self._stop_availability()
        if not allowed:
            self._diag("action_blocked", action="stop_output", reason=reason)
            return
        # Intentionally no modal confirmation once every fail-closed gate is
        # satisfied.  De-energizing then wins the next transaction slot.
        self.controller.stop_output()

    # ----------------------------------------------------------- signal slots
    @Slot(object)
    def _apply_mode(self, payload: Any) -> None:
        kind = _enum_text(_field(payload, "kind", payload or "monitoring"))
        aliases = {
            "live-monitoring": "monitoring",
            "live-control": "control",
            "simulator": "simulation",
        }
        self._mode_kind = aliases.get(kind, kind)
        provided_label = _field(payload, "label")
        if provided_label:
            label = str(provided_label)
        elif self._mode_kind == "simulation":
            label = "СИМУЛЯТОР · данные и команды не относятся к реальному устройству"
        elif self._mode_kind == "control":
            label = "РЕАЛЬНОЕ УСТРОЙСТВО · управление выходом явно разрешено при запуске"
        else:
            label = "МОНИТОРИНГ · опрос разрешён; изменяющие команды заблокированы"
        self.mode_banner.setText(label)
        self.mode_banner.setProperty("simulation", self._mode_kind == "simulation")
        self.mode_banner.style().unpolish(self.mode_banner)
        self.mode_banner.style().polish(self.mode_banner)
        source = "СИМУЛЯЦИЯ" if self._mode_kind == "simulation" else "РЕАЛЬНОЕ УСТРОЙСТВО"
        self.log_source_label.setText(f"Источник журнала: {source}")
        self._diag("mode_rendered", mode=self._mode_kind)
        self._update_interlocks()

    @Slot(object)
    def _apply_devices(self, payload: Any) -> None:
        devices = list(payload or [])
        current_id = self.device_combo.currentData(Qt.UserRole)
        incoming_by_id: dict[str, Any] = {}
        for device in devices:
            identifier = str(_field(device, "identifier", ""))
            incoming_by_id[identifier] = device

        def find_row(identifier: object) -> int:
            for row in range(self.device_combo.count()):
                if self.device_combo.itemData(row, Qt.UserRole) == identifier:
                    return row
            return -1

        previous_signal_state = self.device_combo.blockSignals(True)
        added_count = 0
        removed_count = 0
        try:
            for row in range(self.device_combo.count() - 1, -1, -1):
                identifier = self.device_combo.itemData(row, Qt.UserRole)
                if identifier not in incoming_by_id:
                    self.device_combo.removeItem(row)
                    removed_count += 1

            for identifier, device in incoming_by_id.items():
                row = find_row(identifier)
                if row < 0:
                    self.device_combo.addItem("", identifier)
                    row = self.device_combo.count() - 1
                    added_count += 1
                name = _field(device, "name") or "Без имени"
                rssi = _field(device, "rssi")
                rssi_text = f"{rssi} dBm" if isinstance(rssi, int) else "RSSI —"
                self.device_combo.setItemText(
                    row, f"{name}  ·  {rssi_text}  ·  {identifier}"
                )
                services = _field(device, "service_uuids", ()) or ()
                self.device_combo.setItemData(
                    row,
                    "Advertised services: " + ", ".join(str(x) for x in services),
                    Qt.ToolTipRole,
                )

            preferred_id = (
                None
                if self._manual_device_selection
                else self._last_device_identifier
            )
            preferred_row = find_row(preferred_id) if preferred_id else -1
            current_row = find_row(current_id) if current_id else -1
            if preferred_row >= 0:
                self.device_combo.setCurrentIndex(preferred_row)
            elif current_row >= 0:
                self.device_combo.setCurrentIndex(current_row)

            self._devices = [
                incoming_by_id[self.device_combo.itemData(row, Qt.UserRole)]
                for row in range(self.device_combo.count())
            ]
        finally:
            self.device_combo.blockSignals(previous_signal_state)
        state = _enum_text(_field(self._snapshot, "state", "disconnected"))
        transport_connected = bool(
            _field(self._snapshot, "transport_connected", False)
        )
        self.connect_button.setEnabled(
            bool(self._devices)
            and not transport_connected
            and state != "connecting"
            and not self._operation_busy
        )
        self._diag(
            "device_list_rendered",
            count=len(self._devices),
            added=added_count,
            removed=removed_count,
        )
        self._update_interlocks()

    @Slot(object)
    def _apply_snapshot(self, payload: Any) -> None:
        self._snapshot = payload or {}
        state = _enum_text(_field(self._snapshot, "state", "disconnected"))
        transport_connected = bool(
            _field(self._snapshot, "transport_connected", False)
        )
        authenticated = bool(_field(self._snapshot, "authenticated", False))
        unknown = bool(_field(self._snapshot, "control_outcome_unknown", False))
        telemetry_fresh = _field(self._snapshot, "telemetry_fresh", False) is True
        config_fresh = _field(self._snapshot, "config_fresh", False) is True
        telemetry = _field(self._snapshot, "telemetry") or {}
        config = _field(self._snapshot, "config") or {}

        if authenticated and self._pending_connection_identifier is not None:
            identifier = self._pending_connection_identifier
            self._pending_connection_identifier = None
            self._remember_authenticated_device(identifier)

        rendered_state = (state, transport_connected, authenticated)
        if rendered_state != self._last_rendered_state:
            self._last_rendered_state = rendered_state
            self._diag(
                "state_rendered",
                state=state,
                transport_connected=transport_connected,
                authenticated=authenticated,
            )

        state_labels = {
            "disconnected": ("отключено", "neutral"),
            "connecting": ("подключение", "attention"),
            "discovering": ("поиск GATT", "attention"),
            "authenticating": ("аутентификация", "attention"),
            "loading": ("чтение данных", "attention"),
            "ready": ("готово", "ok"),
            "disconnecting": ("отключение", "attention"),
            "error": ("ошибка", "danger"),
        }
        state_text, state_severity = state_labels.get(state, (state or "—", "neutral"))
        if state == "error" and transport_connected:
            state_text = "ошибка · BLE подключён"
        elif state == "disconnected" and transport_connected:
            state_text = "BLE подключён · требуется отключение"
            state_severity = "danger"
        self.connection_badge.set_status(state_text, state_severity)
        self.auth_badge.set_status(
            "подтверждён" if authenticated else "не выполнен",
            "ok" if authenticated else "neutral",
        )
        if telemetry_fresh and config_fresh:
            age = _field(self._snapshot, "telemetry_age_s")
            age_text = f"T+C свежие · {float(age):.1f} s" if _finite_number(age) is not None else "T+C свежие"
            self.freshness_badge.set_status(age_text, "ok")
        elif telemetry_fresh:
            self.freshness_badge.set_status("конфиг устарел", "attention")
        elif config_fresh:
            self.freshness_badge.set_status("телеметрия устарела", "attention")
        elif telemetry or config:
            self.freshness_badge.set_status("T+C устарели", "attention")
        else:
            self.freshness_badge.set_status("нет", "neutral")
        if unknown:
            self.outcome_badge.set_status("исход неизвестен", "danger")
        elif self._operation_busy:
            self.outcome_badge.set_status("ожидание", "attention")
        else:
            self.outcome_badge.set_status("определён", "ok")

        self.connect_button.setEnabled(
            bool(self._devices)
            and not transport_connected
            and state != "connecting"
            and not self._operation_busy
        )
        self.disconnect_button.setEnabled(
            (transport_connected or state == "connecting")
            and state != "disconnecting"
        )
        self.scan_button.setEnabled(
            not transport_connected and state not in {"connecting", "disconnecting"}
        )
        self.refresh_button.setEnabled(authenticated and state == "ready" and not self._operation_busy)
        self.refresh_action.setEnabled(self.refresh_button.isEnabled())
        for key, reading in self.readings.items():
            reading.set_value(_field(telemetry, key))
        output_enabled = _field(telemetry, "output_enabled")
        if output_enabled is True:
            self.output_state_label.setText("● Выход: ВКЛЮЧЁН")
            self.output_state_label.setProperty("severity", "attention")
        elif output_enabled is False:
            self.output_state_label.setText("○ Выход: выключен")
            self.output_state_label.setProperty("severity", "ok")
        else:
            self.output_state_label.setText("? Выход: состояние неизвестно")
            self.output_state_label.setProperty("severity", "neutral")
        self.output_state_label.style().unpolish(self.output_state_label)
        self.output_state_label.style().polish(self.output_state_label)

        age = _finite_number(_field(self._snapshot, "telemetry_age_s"))
        age_text = f"{age:.1f} s" if age is not None else "—"
        source = "симулятор" if self._mode_kind == "simulation" else "уведомление FFE2"
        self.telemetry_meta.setText(f"Источник: {source} · возраст: {age_text}")

        self._update_identity()
        self._update_config_table(config)
        self._update_target_ranges(config)
        self._update_interlocks()

        error = _field(self._snapshot, "last_error")
        if error:
            self.operation_label.setText(f"Ошибка: {error}")

    @Slot(object)
    def _apply_gatt(self, payload: Any) -> None:
        services = _field(payload, "services", ()) or ()
        selected = _field(payload, "selected")
        error = _field(payload, "error")
        self.gatt_tree.clear()
        for service in services:
            service_uuid = str(_field(service, "uuid", "—"))
            service_item = QTreeWidgetItem([service_uuid, "service"])
            service_item.setData(0, Qt.UserRole, service_uuid)
            self.gatt_tree.addTopLevelItem(service_item)
            for characteristic in _field(service, "characteristics", ()) or ():
                char_uuid = str(_field(characteristic, "uuid", "—"))
                props = sorted(str(x) for x in (_field(characteristic, "properties", ()) or ()))
                child = QTreeWidgetItem([char_uuid, ", ".join(props) or "—"])
                child.setData(0, Qt.UserRole, char_uuid)
                service_item.addChild(child)
            service_item.setExpanded(True)

        if error:
            self.gatt_status.setText(f"× Транспорт не выбран: {error}")
            self.gatt_status.setProperty("severity", "danger")
        elif selected is not None:
            self.gatt_status.setText("✓ Выбрана единственная пригодная пара FFE2 / FFE3")
            self.gatt_status.setProperty("severity", "ok")
        elif services:
            self.gatt_status.setText("! Сервисы получены; пригодный транспорт не выбран")
            self.gatt_status.setProperty("severity", "attention")
        else:
            self.gatt_status.setText("○ Топология ещё не получена")
            self.gatt_status.setProperty("severity", "neutral")
        self.gatt_status.style().unpolish(self.gatt_status)
        self.gatt_status.style().polish(self.gatt_status)

        values = {
            "service_uuid": _field(selected, "service_uuid"),
            "rx_uuid": _field(selected, "rx_uuid"),
            "tx_uuid": _field(selected, "tx_uuid"),
            "write_mode": (
                "write · с GATT-подтверждением"
                if _field(selected, "write_with_response") is True
                else "write without response"
                if selected is not None
                else None
            ),
            "chunk_size": (
                f"{_field(selected, 'wnr_chunk_size')} байт"
                if selected is not None and _field(selected, "wnr_chunk_size") is not None
                else None
            ),
        }
        for key, label in self.gatt_role_labels.items():
            label.setText(_stringify(values[key]))

    @Slot(object)
    def _append_log(self, payload: Any) -> None:
        entry = {
            "timestamp": _field(payload, "timestamp") or datetime.now().isoformat(timespec="milliseconds"),
            "direction": _field(payload, "direction", "—"),
            "opcode": _field(payload, "opcode"),
            "summary": _field(payload, "summary", ""),
            "raw_hex": _field(payload, "raw_hex", ""),
            "decoded": _field(payload, "decoded", {}),
        }
        opcode = entry["opcode"]
        opcode_text = (
            f"0x{opcode:02X}" if isinstance(opcode, int) and not isinstance(opcode, bool) else str(opcode or "—")
        )
        summary_lower = str(entry["summary"]).lower()
        is_password = (
            opcode == 0x02
            or opcode_text.lower() in {"0x02", "02", "2"}
            or "password" in summary_lower
            or "парол" in summary_lower
        )
        if is_password:
            entry["raw_hex"] = "<СКРЫТО: пакет аутентификации>"
            entry["decoded"] = "<СКРЫТО: пакет аутентификации>"
            entry["summary"] = "Проверка пароля · содержимое скрыто"
        else:
            entry["decoded"] = _redact(entry["decoded"])
            entry["summary"] = _stringify(_redact(entry["summary"], key="summary"))
            entry["raw_hex"] = _stringify(entry["raw_hex"])
        entry["opcode_text"] = opcode_text
        self._log_entries.append(entry)
        self._diag(
            "packet_row_appended",
            opcode=opcode,
            direction=str(entry["direction"]),
            row=len(self._log_entries) - 1,
        )

        row = self.log_table.rowCount()
        self.log_table.insertRow(row)
        timestamp = str(entry["timestamp"])
        if "T" in timestamp:
            timestamp = timestamp.split("T", 1)[1]
        values = (
            timestamp,
            str(entry["direction"]),
            opcode_text,
            str(entry["summary"]),
            str(entry["raw_hex"]),
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setData(Qt.UserRole, row)
            self.log_table.setItem(row, column, item)
        self.export_log_button.setEnabled(True)
        self.log_table.scrollToBottom()

    @Slot(object)
    def _apply_operation(self, payload: Any) -> None:
        self._operation_busy = bool(_field(payload, "busy", False))
        name = _field(payload, "name") or ""
        completed = bool(_field(payload, "completed", False))
        if name == "start":
            self._start_pending = not completed
        if name == "stop":
            self._stop_pending = not completed
        message = _field(payload, "message") or ("Выполняется…" if self._operation_busy else "Готово")
        if (
            name == "connect"
            and completed
            and (
                str(message).startswith("Ошибка:")
                or str(message) == "Операция отменена"
            )
        ):
            self._pending_connection_identifier = None
        self.operation_label.setText(f"{name}: {message}" if name else str(message))
        if name == "scan" and bool(_field(payload, "completed", not self._operation_busy)):
            self._scanning = False
            self.scan_button.setText("Сканировать")
        self._diag(
            "operation_rendered",
            operation=str(name),
            busy=self._operation_busy,
            completed=completed,
        )
        self._apply_snapshot(self._snapshot)

    # ------------------------------------------------------------ view update
    def _update_identity(self) -> None:
        firmware = _field(self._snapshot, "firmware")
        serial = _field(self._snapshot, "serial_number")
        self.firmware_label.setText(_identity_text(firmware))
        self.serial_label.setText(_identity_text(serial))

    def _update_config_table(self, config: Any) -> None:
        self.config_table.setRowCount(0)
        if not isinstance(config, Mapping):
            return
        labels = {
            "target_voltage": "Целевое напряжение",
            "target_current": "Целевой ток",
            "max_voltage": "Максимальное напряжение",
            "max_single_module_current": "Максимальный ток одного модуля",
            "max_power": "Максимальная мощность",
            "power_limit": "Ограничение мощности",
            "auto_stop": "Автоматическая остановка",
            "shutdown_current": "Ток завершения",
            "temperature_protection": "Защита по температуре",
            "protection_cutoff_temperature": "Температура отключения",
            "fan_boost_temperature": "Ускорение вентилятора",
            "fan_max_temperature": "Максимум вентилятора",
            "two_stage_charging": "Двухступенчатый режим",
            "secondary_voltage": "Напряжение второй ступени",
            "secondary_current": "Ток второй ступени",
            "offline_voltage": "Автономное напряжение",
            "offline_current": "Автономный ток",
            "power_on_output": "Выход при включении питания",
            "offline_control": "Автономное управление",
        }
        ordered = list(labels) + sorted(key for key in config if key not in labels)
        for key in ordered:
            if key not in config:
                continue
            row = self.config_table.rowCount()
            self.config_table.insertRow(row)
            name_item = QTableWidgetItem(labels.get(key, key))
            name_item.setToolTip(key)
            rendered_value = _stringify(_redact(config[key], key=key))
            value_item = QTableWidgetItem(rendered_value)
            value_item.setToolTip(rendered_value)
            self.config_table.setItem(row, 0, name_item)
            self.config_table.setItem(row, 1, value_item)

    def _update_target_ranges(self, config: Any) -> None:
        if not isinstance(config, Mapping):
            config = {}
        limits = _effective_limits(config)
        max_voltage = None if limits is None else limits.voltage.maximum
        max_current = None if limits is None else limits.current.maximum
        target_voltage = _finite_number(config.get("target_voltage"))
        target_current = _finite_number(config.get("target_current"))
        target_pair = (target_voltage, target_current)
        config_targets_changed = target_pair != self._last_config_targets
        self._setting_spinboxes = True
        try:
            if max_voltage is not None:
                self.voltage_input.setRange(MODEL_MIN_VOLTAGE_V, max_voltage)
                self.voltage_range.setText(
                    f"{MODEL_MIN_VOLTAGE_V:.2f}…{max_voltage:.2f} V"
                )
                if (
                    config_targets_changed
                    and target_voltage is not None
                    and limits is not None
                    and limits.voltage.contains(target_voltage)
                ):
                    self.voltage_input.setValue(target_voltage)
            else:
                self.voltage_input.setRange(
                    MODEL_MIN_VOLTAGE_V,
                    MODEL_MIN_VOLTAGE_V,
                )
                self.voltage_range.setText("Диапазон HW178P недоступен")
            if max_current is not None:
                self.current_input.setRange(MODEL_MIN_CURRENT_A, max_current)
                self.current_range.setText(
                    f"{MODEL_MIN_CURRENT_A:.2f}…{max_current:.2f} A · на модуль"
                )
                if (
                    config_targets_changed
                    and target_current is not None
                    and limits is not None
                    and limits.current.contains(target_current)
                ):
                    self.current_input.setValue(target_current)
            else:
                self.current_input.setRange(
                    MODEL_MIN_CURRENT_A,
                    MODEL_MIN_CURRENT_A,
                )
                self.current_range.setText("Диапазон HW178P недоступен")
        finally:
            self._setting_spinboxes = False
            self._last_config_targets = target_pair

    def _update_interlocks(self) -> None:
        if not hasattr(self, "voltage_input"):
            return
        state = _enum_text(_field(self._snapshot, "state", "disconnected"))
        authenticated = bool(_field(self._snapshot, "authenticated", False))
        output_controls = bool(
            _field(self._snapshot, "output_controls_enabled", False)
        )
        unknown = bool(_field(self._snapshot, "control_outcome_unknown", False))
        telemetry_fresh = _field(self._snapshot, "telemetry_fresh", False) is True
        config_fresh = _field(self._snapshot, "config_fresh", False) is True
        config = _field(self._snapshot, "config") or {}
        limits = _effective_limits(config)
        max_voltage = None if limits is None else limits.voltage.maximum
        max_current = None if limits is None else limits.current.maximum
        target_voltage = _finite_number(_field(config, "target_voltage"))
        target_current = _finite_number(_field(config, "target_current"))
        telemetry = _field(self._snapshot, "telemetry") or {}
        output_enabled = _field(telemetry, "output_enabled")

        reasons: list[str] = []
        if self._mode_kind == "monitoring" or not output_controls:
            reasons.append("режим управления не разрешён параметром запуска")
        if state != "ready":
            reasons.append("сессия не готова")
        if not authenticated:
            reasons.append("аутентификация не выполнена")
        if not telemetry_fresh:
            reasons.append("нет явно подтверждённой свежей телеметрии")
        if not config_fresh:
            reasons.append("нет явно подтверждённой свежей конфигурации")
        if max_voltage is None:
            reasons.append(
                "лимит напряжения неизвестен или ниже 50 V для HW178P"
            )
        if max_current is None:
            reasons.append(
                "лимит тока одного модуля неизвестен или ниже 0.01 A"
            )
        if (
            target_voltage is None
            or limits is None
            or not limits.voltage.contains(target_voltage)
        ):
            rendered = "—" if target_voltage is None else f"{target_voltage:g} V"
            upper = (
                "недоступен"
                if max_voltage is None
                else f"{max_voltage:g} V"
            )
            reasons.append(
                "считанное целевое напряжение "
                f"{rendered} вне действующего диапазона "
                f"{MODEL_MIN_VOLTAGE_V:g} V…{upper}"
            )
        if (
            target_current is None
            or limits is None
            or not limits.current.contains(target_current)
        ):
            rendered = "—" if target_current is None else f"{target_current:g} A"
            upper = (
                "недоступен"
                if max_current is None
                else f"{max_current:g} A"
            )
            reasons.append(
                "считанный целевой ток "
                f"{rendered} вне действующего диапазона "
                f"{MODEL_MIN_CURRENT_A:g} A…{upper}"
            )
        if unknown:
            reasons.append("исход предыдущей изменяющей команды неизвестен")
        if self._operation_busy:
            reasons.append("ожидается ответ на текущую команду")

        can_change = not reasons
        self.voltage_input.setEnabled(can_change)
        self.current_input.setEnabled(can_change)
        self.set_voltage_button.setEnabled(can_change)
        self.set_current_button.setEnabled(can_change)

        start_reasons = list(reasons)
        shown_voltage = _canonical_float32(self.voltage_input.value())
        shown_current = _canonical_float32(self.current_input.value())
        if (
            shown_voltage is None
            or shown_current is None
            or shown_voltage != _canonical_float32(target_voltage)
            or shown_current != _canonical_float32(target_current)
        ):
            start_reasons.append(
                "показанные V/I отличаются от считанной конфигурации"
            )
        if output_enabled is not False:
            start_reasons.append(
                "выход уже включён"
                if output_enabled is True
                else "состояние выхода не подтверждено как OFF"
            )

        start_reasons = list(dict.fromkeys(start_reasons))
        self._update_output_control(start_reasons)
        if self._output_allowed and self._output_action == "stop":
            configuration_reasons = [
                reason for reason in start_reasons if reason != "выход уже включён"
            ]
            message = "● Выход включён; Stop доступен сверху без диалога."
            if configuration_reasons:
                message += " Настройки заблокированы: " + "; ".join(
                    configuration_reasons
                ) + "."
            self.interlock_reason.setText(message)
        elif start_reasons:
            self.interlock_reason.setText(
                "Блокировка включения: " + "; ".join(start_reasons) + "."
            )
        else:
            self.interlock_reason.setText(
                "✓ Свежие V/I в лимитах; подтвердите их в диалоге включения."
            )
        self.interlock_reason.show()
        explanation = self.interlock_reason.text()
        for widget in (
            self.voltage_input,
            self.current_input,
            self.set_voltage_button,
            self.set_current_button,
        ):
            widget.setToolTip("" if widget.isEnabled() else explanation)

    def _output_control_state(
        self, start_reasons: list[str]
    ) -> tuple[bool, str | None, str, str, str]:
        connected = bool(_field(self._snapshot, "transport_connected", False))
        authenticated = bool(_field(self._snapshot, "authenticated", False))
        controls = bool(_field(self._snapshot, "output_controls_enabled", False))
        telemetry_fresh = _field(self._snapshot, "telemetry_fresh", False) is True
        telemetry = _field(self._snapshot, "telemetry") or {}
        output_enabled = _field(telemetry, "output_enabled")

        if self._mode_kind == "monitoring" or not controls:
            return (
                False,
                None,
                "■  Управление отключено",
                "режим только для мониторинга",
                "neutral",
            )
        if self._stop_pending:
            return (
                False,
                None,
                "■  Остановка…",
                "Stop уже поставлен в очередь",
                "danger",
            )
        if not connected or not authenticated:
            return (
                False,
                None,
                "■  Нет подключения",
                "нет подтверждённой сессии и доступа",
                "neutral",
            )
        if not telemetry_fresh:
            label = (
                "■  Состояние устарело"
                if output_enabled in {True, False}
                else "■  Состояние неизвестно"
            )
            return False, None, label, "нет свежей телеметрии выхода", "neutral"
        if output_enabled is True:
            allowed, label, reason = self._stop_availability()
            return allowed, "stop" if allowed else None, label, reason, "danger"
        if output_enabled is not False:
            return (
                False,
                None,
                "■  Состояние неизвестно",
                "состояние выхода не подтверждено",
                "neutral",
            )
        if self._start_pending:
            return (
                False,
                "start",
                "▶  Включение…",
                "Start уже выполняется",
                "attention",
            )
        if start_reasons:
            return (
                False,
                "start",
                "▶  Включить выход…",
                "Блокировка включения: " + "; ".join(start_reasons),
                "attention",
            )
        return (
            True,
            "start",
            "▶  Включить выход…",
            "показать подтверждение точных V/I перед включением",
            "attention",
        )

    def _stop_availability(self) -> tuple[bool, str, str]:
        connected = bool(_field(self._snapshot, "transport_connected", False))
        authenticated = bool(_field(self._snapshot, "authenticated", False))
        controls = bool(_field(self._snapshot, "output_controls_enabled", False))
        telemetry_fresh = _field(self._snapshot, "telemetry_fresh", False) is True
        telemetry = _field(self._snapshot, "telemetry") or {}
        output_enabled = _field(telemetry, "output_enabled")

        if self._mode_kind == "monitoring" or not controls:
            return False, "■  Управление отключено", "режим только для мониторинга"
        if self._stop_pending:
            return False, "■  Остановка…", "Stop уже поставлен в очередь"
        if not connected or not authenticated:
            return False, "■  Нет подключения", "нет подтверждённой сессии"
        if not telemetry_fresh:
            if output_enabled in {True, False}:
                return False, "■  Состояние устарело", "телеметрия выхода устарела"
            return False, "■  Состояние неизвестно", "нет свежей телеметрии выхода"
        if output_enabled is False:
            return False, "■  Выход уже выключен", "свежая телеметрия: выход выключен"
        if output_enabled is not True:
            return False, "■  Состояние неизвестно", "состояние выхода не подтверждено"
        return True, "■  Остановить выход", "немедленно отключить подтверждённый включённый выход"

    def _update_output_control(self, start_reasons: list[str]) -> None:
        if not hasattr(self, "output_button"):
            return
        allowed, action, label, reason, variant = self._output_control_state(
            start_reasons
        )
        self._output_allowed = allowed
        self._output_action = action
        self._output_reason = reason
        self.output_button.setText(label)
        self.output_button.setEnabled(allowed)
        self.output_button.setToolTip(reason)
        self.output_button.setAccessibleDescription(reason)
        if self.output_button.property("variant") != variant:
            self.output_button.setProperty("variant", variant)
            self.output_button.style().unpolish(self.output_button)
            self.output_button.style().polish(self.output_button)

        stop_enabled = allowed and action == "stop"
        self.stop_action.setText("Остановить выход")
        self.stop_action.setEnabled(stop_enabled)
        self.stop_action.setToolTip(
            reason
            if action == "stop"
            else "Stop доступен только при свежем подтверждённом состоянии ON"
        )
        self.shortcut_hint.setText(
            "F5 — обновить  ·  Ctrl+Shift+. — Stop"
            if stop_enabled
            else "F5 — обновить"
        )

    # ---------------------------------------------------------- log utilities
    @Slot()
    def _show_log_details(self) -> None:
        row = self.log_table.currentRow()
        valid = 0 <= row < len(self._log_entries)
        self.copy_log_button.setEnabled(valid)
        if not valid:
            self.decoded_log.clear()
            return
        entry = self._log_entries[row]
        self._diag(
            "packet_row_selected", row=row, opcode=entry.get("opcode")
        )
        details = {
            "timestamp": entry["timestamp"],
            "direction": entry["direction"],
            "opcode": entry["opcode_text"],
            "summary": entry["summary"],
            "raw_hex": entry["raw_hex"],
            "decoded": entry["decoded"],
        }
        self.decoded_log.setPlainText(
            json.dumps(_redact(details), ensure_ascii=False, indent=2, default=str)
        )

    @Slot()
    def _copy_selected_log(self) -> None:
        self._diag("button_clicked", control="copy_packet_log", action="copy")
        text = self.decoded_log.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.operation_label.setText("Выбранная редактированная запись скопирована")
            row = self.log_table.currentRow()
            opcode = (
                self._log_entries[row].get("opcode")
                if 0 <= row < len(self._log_entries)
                else None
            )
            self._diag("clipboard_copy_completed", row=row, opcode=opcode)
        else:
            self._diag("action_blocked", action="copy", reason="no_selected_packet")

    @Slot()
    def _export_log(self) -> None:
        self._diag("button_clicked", control="export_packet_log", action="export")
        self._diag("dialog_opened", dialog="packet_log_export")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспортировать редактированный журнал",
            "hwcdq-packets.jsonl",
            "JSON Lines (*.jsonl);;Все файлы (*)",
        )
        if not path:
            self._diag("dialog_rejected", dialog="packet_log_export")
            return
        filename = Path(path).name
        self._diag(
            "dialog_submitted", dialog="packet_log_export", filename=filename
        )
        source = "simulation" if self._mode_kind == "simulation" else "live-device"
        lines = []
        for item in self._log_entries:
            export_item = dict(item)
            export_item["source"] = source
            lines.append(
                json.dumps(_redact(export_item), ensure_ascii=False, default=str)
            )
        try:
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self._diag(
                "export_failed",
                filename=filename,
                error_type=type(exc).__name__,
            )
            self._diag("dialog_opened", dialog="packet_log_export_error")
            QMessageBox.critical(
                self, "Не удалось экспортировать журнал", str(exc)
            )
            self._diag("dialog_closed", dialog="packet_log_export_error")
            return
        self.operation_label.setText(
            f"Экспортирован редактированный журнал: {Path(path).name}"
        )
        self._diag(
            "export_completed", filename=filename, record_count=len(lines)
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._diag("window_closing", scan_active=self._scanning)
        if self._scanning:
            self._diag("shutdown_scan_requested")
            self.controller.stop_scan()
            self._scanning = False
        event.accept()
        self._diag("window_closed")


__all__ = ["MainWindow"]
