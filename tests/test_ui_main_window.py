from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from PySide6.QtCore import QPersistentModelIndex, QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QLineEdit,
    QPushButton,
)

from hwcdq_control.ui import (  # noqa: E402
    MainWindow,
    SetpointConfirmationDialog,
    StartConfirmationDialog,
)
from hwcdq_control.ui.contracts import SnapshotPayload  # noqa: E402
from hwcdq_control.diagnostics import DiagnosticLogger  # noqa: E402
from tools import hwcdq_protocol as codec  # noqa: E402


class FakeController(QObject):
    mode_changed = Signal(object)
    devices_changed = Signal(object)
    snapshot_changed = Signal(object)
    gatt_changed = Signal(object)
    packet_logged = Signal(object)
    operation_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[object, ...]] = []

    def start_scan(self) -> None:
        self.calls.append(("start_scan",))

    def stop_scan(self) -> None:
        self.calls.append(("stop_scan",))

    def connect_device(self, identifier: str, password: str) -> None:
        self.calls.append(("connect_device", identifier, password))

    def disconnect_device(self) -> None:
        self.calls.append(("disconnect_device",))

    def refresh(self) -> None:
        self.calls.append(("refresh",))

    def set_voltage(self, volts: float) -> None:
        self.calls.append(("set_voltage", volts))

    def set_current(self, amps: float) -> None:
        self.calls.append(("set_current", amps))

    def start_output(self, volts: float, amps: float) -> None:
        self.calls.append(("start_output", volts, amps))

    def stop_output(self) -> None:
        self.calls.append(("stop_output",))


class FakeSettings:
    def __init__(
        self,
        initial: dict[str, object] | None = None,
        *,
        fail_reads: bool = False,
        fail_writes: bool = False,
    ) -> None:
        self.data = dict(initial or {})
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self.reads: list[str] = []
        self.writes: list[tuple[str, object]] = []

    def value(self, key: str, default: object = None) -> object:
        self.reads.append(key)
        if self.fail_reads:
            raise OSError("settings read failed")
        return self.data.get(key, default)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802 - Qt API
        if self.fail_writes:
            raise OSError("settings write failed")
        self.writes.append((key, value))
        self.data[key] = value


class BrokenDiagnostics:
    """Exercise the UI's stronger-than-required diagnostic isolation."""

    enabled = True
    healthy = False
    active = False
    error = "simulated disk failure"
    path = Path("/unavailable/hwcdq-debug.jsonl")

    def emit(self, *_args: object, **_kwargs: object) -> bool:
        raise OSError("simulated diagnostic failure")


def ready_snapshot(
    *,
    controls: bool = True,
    unknown: bool = False,
    voltage_limit: float = 100.0,
    current_limit: float = 20.0,
    target_voltage: float = 84.0,
    target_current: float = 10.0,
    output_enabled: bool | None = False,
) -> dict[str, object]:
    return {
        "state": "ready",
        "transport_connected": True,
        "authenticated": True,
        "output_controls_enabled": controls,
        "control_outcome_unknown": unknown,
        "config_fresh": True,
        "telemetry_fresh": True,
        "telemetry_age_s": 0.25,
        "firmware": b"1.2.3",
        "serial_number": b"HWCDQ-TEST",
        "config": {
            "target_voltage": target_voltage,
            "target_current": target_current,
            "max_voltage": voltage_limit,
            "max_single_module_current": current_limit,
            "max_power": 2400,
        },
        "telemetry": {
            "input_voltage": 229.87,
            "input_current": 4.13,
            "input_power_w": 949.31,
            "output_voltage": 83.91,
            "output_current": 9.97,
            "output_power_w": 836.58,
            "temperature_1": 37.2,
            "temperature_2": 42.8,
            "input_frequency": 50.0,
            "accumulated_capacity_ah": 12.345,
            "accumulated_energy_wh": 932.4,
            "module_count": 2,
            "output_enabled": output_enabled,
        },
        "last_error": None,
    }


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["hwcdq-ui-tests"])

    def setUp(self) -> None:
        self.controller = FakeController()
        self.window = MainWindow(self.controller)
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_snapshot_contract_exposes_physical_transport_state(self) -> None:
        self.assertIn("transport_connected", SnapshotPayload.__annotations__)

    def test_binary_identity_values_fit_the_minimum_window_without_clipping(self) -> None:
        self.window.resize(1100, 720)
        snapshot = ready_snapshot()
        snapshot["firmware"] = b"SIM-1.0\x00"
        snapshot["serial_number"] = b"HWCDQ-SIM-0001\x00"
        snapshot["config"]["raw_ascii_23"] = b"A very long raw configuration value"
        self.controller.snapshot_changed.emit(snapshot)
        self.app.processEvents()

        product_title = self.window.findChild(QObject, "productTitle")
        self.assertIsNotNone(product_title)
        self.assertLessEqual(
            product_title.fontMetrics().horizontalAdvance(product_title.text()),
            product_title.contentsRect().width(),
        )
        self.assertTrue(self.window.firmware_label.wordWrap())
        self.assertTrue(self.window.serial_label.wordWrap())
        self.assertLessEqual(
            self.window.serial_label.fontMetrics().horizontalAdvance(
                self.window.serial_label.text()
            ),
            self.window.serial_label.contentsRect().width(),
        )
        self.assertGreaterEqual(self.window.config_table.columnWidth(0), 240)
        self.assertGreaterEqual(self.window.config_table.columnWidth(1), 150)
        self.assertGreaterEqual(self.window.targets_panel.height(), 190)
        vertical_controls = (
            self.window.voltage_input,
            self.window.current_input,
            self.window.interlock_reason,
        )
        for upper, lower in zip(vertical_controls, vertical_controls[1:]):
            self.assertLess(upper.geometry().bottom(), lower.geometry().top())
        self.assertLessEqual(
            self.window.interlock_reason.geometry().bottom(),
            self.window.targets_panel.contentsRect().bottom(),
        )
        self.assertLess(
            self.window.targets_panel.geometry().bottom(),
            self.window.config_panel.geometry().top(),
        )

    def test_native_instrument_surfaces_have_stable_names_and_no_raw_sender(self) -> None:
        expected_name = "Pidzoom Portable charger HW178P"
        self.assertEqual(self.window.windowTitle(), expected_name)
        product_title = self.window.findChild(QObject, "productTitle")
        self.assertIsNotNone(product_title)
        self.assertEqual(product_title.text(), expected_name)
        self.assertEqual(self.window.tabs.count(), 3)
        self.assertEqual(
            [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())],
            ["Рабочая панель", "GATT и транспорт", "Журнал пакетов"],
        )
        for name in (
            "connectionStatus",
            "authenticationStatus",
            "freshnessStatus",
            "outcomeStatus",
            "debugStatus",
            "outputControlButton",
            "targetVoltageInput",
            "targetCurrentInput",
            "gattTree",
            "packetLogTable",
        ):
            self.assertIsNotNone(self.window.findChild(QObject, name), name)

        visible_text = " ".join(
            button.text().lower()
            for button in self.window.findChildren(QPushButton)
        )
        self.assertNotIn("opcode", visible_text)
        self.assertNotIn("raw command", visible_text)
        self.assertFalse(
            any("opcode" in editor.objectName().lower() for editor in self.window.findChildren(QLineEdit))
        )
        self.assertFalse(self.window.debug_badge.isVisible())

    def test_debug_logger_failure_is_visible_and_never_blocks_controller_calls(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.controller = FakeController()
        self.window = MainWindow(self.controller, diagnostics=BrokenDiagnostics())  # type: ignore[arg-type]
        self.window.show()
        self.app.processEvents()

        self.assertTrue(self.window.debug_badge.isVisible())
        self.assertIn("ошибка журнала", self.window.debug_badge.text())
        self.assertIn("simulated disk failure", self.window.debug_badge.toolTip())

        self.window.scan_button.click()
        self.assertEqual(self.controller.calls[-1], ("start_scan",))

    def test_debug_records_semantic_ui_actions_without_password_or_raw_keys(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary_directory:
            log_path = Path(temporary_directory) / "hwcdq-debug.jsonl"
            export_path = Path(temporary_directory) / "exported-packets.jsonl"
            diagnostics = DiagnosticLogger(log_path, enabled=True)

            self.window.close()
            self.window.deleteLater()
            self.controller = FakeController()
            self.window = MainWindow(self.controller, diagnostics=diagnostics)
            self.window.show()
            self.app.processEvents()

            self.assertTrue(self.window.debug_badge.isVisible())
            self.assertIn(log_path.name, self.window.debug_badge.text())
            self.assertIn(str(log_path), self.window.debug_badge.toolTip())

            self.controller.devices_changed.emit(
                [
                    {
                        "identifier": "AA:BB:CC:DD:EE:FF",
                        "name": "HWCDQBLE_NIUB",
                        "rssi": -51,
                        "service_uuids": ["FFE1"],
                    }
                ]
            )
            self.app.processEvents()
            self.window.device_combo.activated.emit(0)

            password = "session-only-password"
            with (
                patch.object(
                    QInputDialog,
                    "exec",
                    side_effect=[QDialog.Rejected, QDialog.Accepted],
                ),
                patch.object(QInputDialog, "textValue", return_value=password),
            ):
                self.window.connect_button.click()
                self.window.connect_button.click()

            self.window.scan_button.click()
            self.window.scan_button.click()
            self.window.tabs.setCurrentIndex(1)
            self.window.tabs.setCurrentIndex(2)
            self.window.tabs.setCurrentIndex(0)

            self.controller.mode_changed.emit({"kind": "control"})
            self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=True))
            self.app.processEvents()

            self.window.refresh_button.click()
            self.window.refresh_action.trigger()

            with patch.object(
                SetpointConfirmationDialog,
                "exec",
                return_value=QDialog.Accepted,
            ):
                self.window.set_voltage_button.click()
                self.window.set_current_button.click()
            def accept_start(dialog: StartConfirmationDialog) -> int:
                dialog.acknowledge.setChecked(True)
                return int(QDialog.Accepted)

            self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=False))
            self.app.processEvents()
            with patch.object(StartConfirmationDialog, "exec", new=accept_start):
                self.window.output_button.click()

            self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=True))
            self.app.processEvents()
            self.window.output_button.click()
            self.window.stop_action.trigger()
            self.window.disconnect_button.click()

            self.controller.packet_logged.emit(
                {
                    "timestamp": "2026-08-25T12:00:00.000",
                    "direction": "RX",
                    "opcode": 0x05,
                    "summary": "telemetry",
                    "raw_hex": "07 05 00 00",
                    "decoded": {"state": "ready"},
                }
            )
            self.app.processEvents()
            self.window.log_table.selectRow(0)
            self.app.processEvents()
            self.window.copy_log_button.click()
            with patch(
                "hwcdq_control.ui.main_window.QFileDialog.getSaveFileName",
                side_effect=[("", ""), (str(export_path), "")],
            ):
                self.window.export_log_button.click()
                self.window.export_log_button.click()

            self.window.close()
            diagnostics.close()

            raw_log = log_path.read_text(encoding="utf-8")
            records = [json.loads(line) for line in raw_log.splitlines()]
            event_names = [record["event"] for record in records]
            self.assertTrue(
                {
                    "window_opened",
                    "button_clicked",
                    "shortcut_triggered",
                    "dialog_opened",
                    "dialog_submitted",
                    "dialog_rejected",
                    "device_selected",
                    "tab_selected",
                    "safe_load_acknowledgement_changed",
                    "command_submitted",
                    "clipboard_copy_completed",
                    "export_completed",
                    "window_closing",
                    "window_closed",
                }.issubset(event_names)
            )

            shortcut_events = [
                record["details"]
                for record in records
                if record["event"] == "shortcut_triggered"
            ]
            self.assertIn(
                {"action": "refresh", "shortcut": "F5"}, shortcut_events
            )
            self.assertIn(
                {"action": "stop_output", "shortcut": "Ctrl+Shift+."},
                shortcut_events,
            )
            button_events = [
                record["details"]
                for record in records
                if record["event"] == "button_clicked"
            ]
            self.assertTrue(
                any(item.get("control") == "refresh" for item in button_events)
            )
            self.assertTrue(
                any(
                    item.get("control") == "output"
                    and item.get("action") == "stop_output"
                    for item in button_events
                )
            )

            auth_dialogs = [
                record["details"]
                for record in records
                if record["event"] in {"dialog_opened", "dialog_submitted", "dialog_rejected"}
                and record["details"].get("dialog") == "device_authentication"
            ]
            self.assertEqual(
                {"dialog_opened", "dialog_submitted", "dialog_rejected"},
                {
                    record["event"]
                    for record in records
                    if record["details"].get("dialog") == "device_authentication"
                },
            )
            self.assertTrue(auth_dialogs)
            self.assertTrue(
                all(
                    not ({"value", "length", "focus", "contents"} & set(details))
                    for details in auth_dialogs
                )
            )

            self.assertNotIn(password, raw_log)
            self.assertNotIn(password.encode().hex(), raw_log.lower())
            self.assertFalse(any("key" in event.casefold() for event in event_names))

    def test_monitoring_mode_blocks_every_mutation_including_stop(self) -> None:
        self.controller.snapshot_changed.emit(
            ready_snapshot(controls=False, output_enabled=True)
        )
        self.app.processEvents()

        self.assertFalse(self.window.set_voltage_button.isEnabled())
        self.assertFalse(self.window.set_current_button.isEnabled())
        self.assertFalse(self.window.output_button.isEnabled())
        self.assertFalse(self.window.stop_action.isEnabled())
        self.assertIn("Управление отключено", self.window.output_button.text())
        self.assertIn("режим управления", self.window.interlock_reason.text())

        self.window.output_button.click()
        self.window.stop_action.trigger()
        self.assertNotIn(("stop_output",), self.controller.calls)

    def test_one_header_button_replaces_start_and_setpoint_acceptance(self) -> None:
        output_button = self.window.findChild(QPushButton, "outputControlButton")
        self.assertIsNotNone(output_button)
        self.assertIsNone(self.window.findChild(QPushButton, "startOutputButton"))
        self.assertIsNone(self.window.findChild(QPushButton, "acceptSetpointsButton"))
        output_controls = [
            button
            for button in self.window.findChildren(QPushButton)
            if button.objectName()
            in {"outputControlButton", "startOutputButton", "stopOutputButton"}
        ]
        self.assertEqual(output_controls, [output_button])

    def test_header_output_button_geometry_is_stationary_between_off_and_on(self) -> None:
        self.window.resize(1100, 720)
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=False))
        self.app.processEvents()
        off_geometry = self.window.output_button.geometry().getRect()

        self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=True))
        self.app.processEvents()
        on_geometry = self.window.output_button.geometry().getRect()

        self.assertEqual(off_geometry, on_geometry)
        self.assertEqual(off_geometry[3], 38)

    def test_header_output_button_dispatches_only_the_fresh_device_state(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        off = ready_snapshot(output_enabled=False)
        self.controller.snapshot_changed.emit(off)
        self.app.processEvents()

        output_button = self.window.findChild(QPushButton, "outputControlButton")
        self.assertIsNotNone(output_button)
        assert output_button is not None
        self.assertTrue(output_button.isEnabled())
        self.assertIn("Включить выход", output_button.text())
        self.assertEqual(output_button.property("variant"), "attention")
        with patch.object(self.window, "_confirm_start", return_value=True):
            output_button.click()
        self.assertEqual(self.controller.calls[-1], ("start_output", 84.0, 10.0))

        on = ready_snapshot(
            output_enabled=True,
            unknown=True,
            current_limit=0.0,
        )
        on["config_fresh"] = False
        self.controller.snapshot_changed.emit(on)
        self.app.processEvents()
        self.assertIs(
            output_button,
            self.window.findChild(QPushButton, "outputControlButton"),
        )
        self.assertTrue(output_button.isEnabled())
        self.assertIn("Остановить выход", output_button.text())
        self.assertEqual(output_button.property("variant"), "danger")
        with patch.object(self.window, "_confirm_start") as confirm:
            output_button.click()
        confirm.assert_not_called()
        self.assertEqual(self.controller.calls[-1], ("stop_output",))

    def test_start_confirmation_rechecks_output_before_dispatch(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(
            ready_snapshot(output_enabled=False)
        )
        self.app.processEvents()
        output_button = self.window.findChild(QPushButton, "outputControlButton")
        self.assertIsNotNone(output_button)
        assert output_button is not None

        def output_turns_on(_volts: float, _amps: float) -> bool:
            self.controller.snapshot_changed.emit(
                ready_snapshot(output_enabled=True)
            )
            self.app.processEvents()
            return True

        with patch.object(
            self.window,
            "_confirm_start",
            side_effect=output_turns_on,
        ):
            output_button.click()

        self.assertFalse(any(call[0] == "start_output" for call in self.controller.calls))
        self.assertIn("Остановить выход", output_button.text())
        self.assertTrue(output_button.isEnabled())

    def test_start_confirmation_rechecks_exact_displayed_values_before_dispatch(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(
            ready_snapshot(output_enabled=False)
        )
        self.app.processEvents()

        def voltage_changes(_volts: float, _amps: float) -> bool:
            self.window.voltage_input.setValue(83.0)
            self.app.processEvents()
            return True

        with patch.object(
            self.window,
            "_confirm_start",
            side_effect=voltage_changes,
        ):
            self.window.output_button.click()

        self.assertFalse(any(call[0] == "start_output" for call in self.controller.calls))
        self.assertFalse(self.window.output_button.isEnabled())
        self.assertIn("V/I", self.window.output_button.toolTip())

    def test_fresh_on_overrides_pending_start_and_exposes_stop(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=False))
        self.controller.operation_changed.emit(
            {"busy": True, "name": "start", "message": "Start выполняется"}
        )
        self.app.processEvents()
        self.assertFalse(self.window.output_button.isEnabled())
        self.assertIn("Включение", self.window.output_button.text())

        self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=True))
        self.app.processEvents()
        self.assertTrue(self.window.output_button.isEnabled())
        self.assertIn("Остановить выход", self.window.output_button.text())
        self.assertTrue(self.window.stop_action.isEnabled())

        self.window.output_button.click()
        self.assertEqual(self.controller.calls[-1], ("stop_output",))

    def test_protocol_error_with_live_transport_exposes_only_disconnect_path(self) -> None:
        snapshot = ready_snapshot(controls=False)
        snapshot.update(
            {
                "state": "error",
                "transport_connected": True,
                "authenticated": False,
                "last_error": "invalid HWCDQ checksum",
            }
        )
        self.controller.snapshot_changed.emit(snapshot)
        self.app.processEvents()

        self.assertTrue(self.window.disconnect_button.isEnabled())
        self.assertFalse(self.window.scan_button.isEnabled())
        self.assertFalse(self.window.connect_button.isEnabled())
        self.assertFalse(self.window.stop_button.isEnabled())
        self.assertIn("BLE подключён", self.window.connection_badge.text())

        self.window.disconnect_button.click()
        self.assertEqual(self.controller.calls[-1], ("disconnect_device",))

    def test_defensive_badge_never_calls_a_live_transport_disconnected(self) -> None:
        snapshot = ready_snapshot(controls=False)
        snapshot.update(
            {
                "state": "disconnected",
                "transport_connected": True,
                "authenticated": False,
            }
        )
        self.controller.snapshot_changed.emit(snapshot)
        self.app.processEvents()

        self.assertIn("требуется отключение", self.window.connection_badge.text())
        self.assertTrue(self.window.disconnect_button.isEnabled())
        self.assertFalse(self.window.scan_button.isEnabled())
        self.assertFalse(self.window.connect_button.isEnabled())

    def test_pending_stop_cannot_be_double_submitted(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=True))
        self.controller.operation_changed.emit(
            {"busy": True, "name": "stop", "message": "STOP поставлен"}
        )
        self.app.processEvents()
        self.assertFalse(self.window.stop_button.isEnabled())

        self.controller.snapshot_changed.emit(ready_snapshot(output_enabled=True))
        self.app.processEvents()
        self.assertFalse(self.window.stop_button.isEnabled())

        self.controller.operation_changed.emit(
            {
                "busy": False,
                "name": "stop",
                "message": "STOP подтверждён",
                "completed": True,
            }
        )
        self.app.processEvents()
        self.assertTrue(self.window.stop_button.isEnabled())

    def test_control_mode_delegates_only_named_confirmed_operations(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(ready_snapshot())
        self.app.processEvents()

        self.assertTrue(self.window.set_voltage_button.isEnabled())
        self.assertTrue(self.window.set_current_button.isEnabled())
        self.assertTrue(self.window.output_button.isEnabled())
        self.assertIn("Включить выход", self.window.output_button.text())

        with patch.object(self.window, "_confirm_setpoint", return_value=True) as confirm_setpoint:
            self.window.set_voltage_button.click()
            self.window.set_current_button.click()
        with patch.object(self.window, "_confirm_start", return_value=True) as confirm:
            self.window.output_button.click()
        confirm.assert_called_once_with(84.0, 10.0)
        self.assertEqual(
            confirm_setpoint.call_args_list[0].args,
            ("Целевое напряжение", 84.0, 100.0, "V"),
        )
        self.assertEqual(
            confirm_setpoint.call_args_list[1].args,
            ("Ограничение тока", 10.0, 14.0, "A"),
        )

        self.assertIn(("set_voltage", 84.0), self.controller.calls)
        self.assertIn(("set_current", 10.0), self.controller.calls)
        self.assertEqual(self.controller.calls[-1], ("start_output", 84.0, 10.0))

    def test_hw178p_profile_intersects_wider_and_narrower_device_limits(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(
            ready_snapshot(voltage_limit=250.0, current_limit=20.0)
        )
        self.app.processEvents()

        self.assertEqual(self.window.voltage_input.minimum(), 50.0)
        self.assertEqual(self.window.voltage_input.maximum(), 178.0)
        self.assertEqual(self.window.current_input.minimum(), 0.01)
        self.assertEqual(self.window.current_input.maximum(), 14.0)
        self.assertIn("50.00…178.00 V", self.window.voltage_range.text())
        self.assertIn("0.01…14.00 A", self.window.current_range.text())

        self.controller.snapshot_changed.emit(
            ready_snapshot(
                voltage_limit=120.0,
                current_limit=9.0,
                target_current=8.0,
            )
        )
        self.app.processEvents()
        self.assertEqual(self.window.voltage_input.maximum(), 120.0)
        self.assertEqual(self.window.current_input.maximum(), 9.0)

    def test_out_of_profile_readback_is_visible_but_never_accepted(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(
            ready_snapshot(
                voltage_limit=178.0,
                current_limit=14.0,
                target_voltage=49.0,
            )
        )
        self.app.processEvents()

        self.assertFalse(self.window.set_voltage_button.isEnabled())
        self.assertFalse(self.window.set_current_button.isEnabled())
        self.assertFalse(self.window.output_button.isEnabled())
        self.assertIn("50", self.window.interlock_reason.text())
        rendered_config_values = {
            self.window.config_table.item(row, 1).text()
            for row in range(self.window.config_table.rowCount())
        }
        self.assertIn("49", rendered_config_values)
        self.assertNotEqual(self.window.voltage_input.value(), 49.0)

    def test_invalid_or_below_floor_reported_limit_fails_closed(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        for voltage_limit, current_limit in (
            (49.0, 14.0),
            (178.0, 0.001),
            (float("nan"), 14.0),
            (178.0, float("inf")),
        ):
            with self.subTest(
                voltage_limit=voltage_limit,
                current_limit=current_limit,
            ):
                self.controller.snapshot_changed.emit(
                    ready_snapshot(
                        voltage_limit=voltage_limit,
                        current_limit=current_limit,
                    )
                )
                self.app.processEvents()
                self.assertFalse(self.window.set_voltage_button.isEnabled())
                self.assertFalse(self.window.set_current_button.isEnabled())
                self.assertFalse(self.window.output_button.isEnabled())

    def test_unknown_outcome_and_invalid_current_limit_fail_closed_but_not_stop(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(
            ready_snapshot(
                unknown=True,
                current_limit=0.0,
                output_enabled=True,
            )
        )
        self.app.processEvents()

        self.assertFalse(self.window.set_voltage_button.isEnabled())
        self.assertFalse(self.window.set_current_button.isEnabled())
        self.assertTrue(self.window.output_button.isEnabled())
        self.assertIn("Остановить выход", self.window.output_button.text())
        reason = self.window.interlock_reason.text()
        self.assertIn("исход предыдущей", reason)
        self.assertIn("лимит тока одного модуля", reason)

    def test_stale_config_blocks_all_mutations_but_not_stop(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        snapshot = ready_snapshot(output_enabled=True)
        snapshot["config_fresh"] = False
        self.controller.snapshot_changed.emit(snapshot)
        self.app.processEvents()

        self.assertFalse(self.window.set_voltage_button.isEnabled())
        self.assertFalse(self.window.set_current_button.isEnabled())
        self.assertTrue(self.window.output_button.isEnabled())
        self.assertIn("Остановить выход", self.window.output_button.text())
        self.assertIn("свежей конфигурации", self.window.interlock_reason.text())
        self.assertIn("конфиг устарел", self.window.freshness_badge.text())

    def test_cancelled_setpoint_confirmation_does_not_call_controller(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        self.controller.snapshot_changed.emit(ready_snapshot())
        self.app.processEvents()
        with patch.object(self.window, "_confirm_setpoint", return_value=False) as confirm:
            self.window.set_voltage_button.click()
        confirm.assert_called_once_with("Целевое напряжение", 84.0, 100.0, "V")
        self.assertNotIn(("set_voltage", 84.0), self.controller.calls)

    def test_periodic_telemetry_does_not_overwrite_operator_input(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})
        snapshot = ready_snapshot()
        self.controller.snapshot_changed.emit(snapshot)
        self.window.voltage_input.setValue(80.0)
        self.assertFalse(self.window.output_button.isEnabled())

        updated = ready_snapshot()
        updated["telemetry_age_s"] = 0.1
        self.controller.snapshot_changed.emit(updated)
        self.app.processEvents()

        self.assertEqual(self.window.voltage_input.value(), 80.0)
        self.assertFalse(self.window.output_button.isEnabled())

    def test_scan_button_resets_when_controller_reports_completion(self) -> None:
        self.window.scan_button.click()
        self.assertEqual(self.window.scan_button.text(), "Остановить поиск")
        self.controller.operation_changed.emit(
            {"busy": False, "name": "scan", "message": "Поиск завершён"}
        )
        self.app.processEvents()
        self.assertEqual(self.window.scan_button.text(), "Сканировать")

    def test_stop_requires_fresh_explicit_on_but_not_start_configuration(self) -> None:
        self.controller.mode_changed.emit({"kind": "control"})

        off = ready_snapshot(output_enabled=False)
        self.controller.snapshot_changed.emit(off)
        self.assertTrue(self.window.output_button.isEnabled())
        self.assertIn("Включить выход", self.window.output_button.text())
        self.assertFalse(self.window.stop_action.isEnabled())
        self.assertNotIn("Ctrl+Shift", self.window.shortcut_hint.text())

        stale = ready_snapshot(output_enabled=True)
        stale["telemetry_fresh"] = False
        self.controller.snapshot_changed.emit(stale)
        self.assertFalse(self.window.stop_button.isEnabled())
        self.assertIn("устарело", self.window.stop_button.text())

        unknown = ready_snapshot(output_enabled=None)
        self.controller.snapshot_changed.emit(unknown)
        self.assertFalse(self.window.stop_button.isEnabled())
        self.assertIn("неизвестно", self.window.stop_button.text())

        on = ready_snapshot(output_enabled=True, current_limit=0.0)
        on["state"] = "loading"
        on["config_fresh"] = False
        on["config"] = None
        self.controller.snapshot_changed.emit(on)
        self.assertTrue(self.window.stop_button.isEnabled())
        self.assertTrue(self.window.stop_action.isEnabled())
        self.assertIn("Остановить выход", self.window.stop_button.text())
        self.assertIn("Ctrl+Shift", self.window.shortcut_hint.text())

    def test_saved_device_is_restored_incrementally_until_manual_selection(self) -> None:
        remembered = "00000000-0000-0000-0000-000000000001"
        settings = FakeSettings({"ble/lastDeviceIdentifier": remembered})
        self.window.close()
        self.window.deleteLater()
        self.window = MainWindow(self.controller, settings=settings)
        self.window.show()

        other = {
            "identifier": "OTHER-DEVICE",
            "name": "Other",
            "rssi": -30,
            "service_uuids": [],
        }
        preferred = {
            "identifier": remembered,
            "name": "HWCDQ_TEST_0001",
            "rssi": -70,
            "service_uuids": ["FFE0"],
        }
        self.controller.devices_changed.emit([other])
        self.assertEqual(self.window.device_combo.currentData(Qt.UserRole), "OTHER-DEVICE")
        self.controller.devices_changed.emit([other, preferred])
        self.assertEqual(self.window.device_combo.currentData(Qt.UserRole), remembered)
        self.assertEqual(
            [
                self.window.device_combo.itemData(index, Qt.UserRole)
                for index in range(self.window.device_combo.count())
            ],
            ["OTHER-DEVICE", remembered],
        )
        self.assertNotIn(("connect_device",), self.controller.calls)

        other_index = self.window.device_combo.findData("OTHER-DEVICE", Qt.UserRole)
        self.window.device_combo.setCurrentIndex(other_index)
        self.window.device_combo.activated.emit(other_index)
        self.controller.devices_changed.emit([preferred, other])
        self.assertEqual(self.window.device_combo.currentData(Qt.UserRole), "OTHER-DEVICE")
        self.assertEqual(
            [
                self.window.device_combo.itemData(index, Qt.UserRole)
                for index in range(self.window.device_combo.count())
            ],
            ["OTHER-DEVICE", remembered],
        )
        self.assertFalse(any(call[0] == "connect_device" for call in self.controller.calls))

    def test_device_rows_update_in_place_without_rssi_reordering(self) -> None:
        first = [
            {
                "identifier": "DEVICE-A",
                "name": "Alpha",
                "rssi": -40,
                "service_uuids": ["FFE1"],
            },
            {
                "identifier": "DEVICE-B",
                "name": "Beta",
                "rssi": -70,
                "service_uuids": [],
            },
        ]
        self.controller.devices_changed.emit(first)
        beta_index = self.window.device_combo.findData("DEVICE-B", Qt.UserRole)
        self.window.device_combo.setCurrentIndex(beta_index)
        self.window.device_combo.activated.emit(beta_index)

        persistent_alpha = QPersistentModelIndex(
            self.window.device_combo.model().index(0, 0)
        )
        row_events: list[tuple[str, int, int]] = []
        model = self.window.device_combo.model()
        model.rowsInserted.connect(
            lambda _parent, start, end: row_events.append(("insert", start, end))
        )
        model.rowsRemoved.connect(
            lambda _parent, start, end: row_events.append(("remove", start, end))
        )

        updated_in_rssi_order = [
            {
                "identifier": "DEVICE-B",
                "name": "Beta nearby",
                "rssi": -30,
                "service_uuids": ["FFE2"],
            },
            {
                "identifier": "DEVICE-A",
                "name": "Alpha far",
                "rssi": -80,
                "service_uuids": ["FFE1", "FFE3"],
            },
        ]
        self.controller.devices_changed.emit(updated_in_rssi_order)

        self.assertEqual(row_events, [])
        self.assertTrue(persistent_alpha.isValid())
        self.assertEqual(persistent_alpha.row(), 0)
        self.assertEqual(
            [
                self.window.device_combo.itemData(index, Qt.UserRole)
                for index in range(self.window.device_combo.count())
            ],
            ["DEVICE-A", "DEVICE-B"],
        )
        self.assertEqual(
            self.window.device_combo.currentData(Qt.UserRole), "DEVICE-B"
        )
        self.assertIn("Alpha far", self.window.device_combo.itemText(0))
        self.assertIn("-80 dBm", self.window.device_combo.itemText(0))
        self.assertIn("FFE3", self.window.device_combo.itemData(0, Qt.ToolTipRole))
        self.assertEqual(
            [device["identifier"] for device in self.window._devices],
            ["DEVICE-A", "DEVICE-B"],
        )

    def test_device_rows_append_newcomers_and_reset_only_for_new_scan(self) -> None:
        device_a = {
            "identifier": "DEVICE-A",
            "name": "Alpha",
            "rssi": -40,
            "service_uuids": [],
        }
        device_b = {
            "identifier": "DEVICE-B",
            "name": "Beta",
            "rssi": -50,
            "service_uuids": [],
        }
        device_c = {
            "identifier": "DEVICE-C",
            "name": "Gamma",
            "rssi": -30,
            "service_uuids": [],
        }
        self.controller.devices_changed.emit([device_a])
        self.controller.devices_changed.emit([device_c, device_a, device_b])
        self.assertEqual(
            [
                self.window.device_combo.itemData(index, Qt.UserRole)
                for index in range(self.window.device_combo.count())
            ],
            ["DEVICE-A", "DEVICE-C", "DEVICE-B"],
        )

        self.controller.devices_changed.emit([])
        self.assertEqual(self.window.device_combo.count(), 0)
        self.controller.devices_changed.emit([device_b, device_a])
        self.assertEqual(
            [
                self.window.device_combo.itemData(index, Qt.UserRole)
                for index in range(self.window.device_combo.count())
            ],
            ["DEVICE-B", "DEVICE-A"],
        )

    def test_device_preference_is_written_only_after_successful_authentication(self) -> None:
        settings = FakeSettings()
        self.window.close()
        self.window.deleteLater()
        self.window = MainWindow(self.controller, settings=settings)
        self.window.show()
        identifier = "REAL-COREBLUETOOTH-ID"
        self.controller.devices_changed.emit(
            [{"identifier": identifier, "name": "HWCDQ", "rssi": -50}]
        )

        password = "never-persist-this"
        with (
            patch.object(QInputDialog, "exec", return_value=QDialog.Accepted),
            patch.object(QInputDialog, "textValue", return_value=password),
        ):
            self.window.connect_button.click()
        self.assertEqual(settings.writes, [])

        connecting = ready_snapshot(controls=False)
        connecting.update({"state": "authenticating", "authenticated": False})
        self.controller.snapshot_changed.emit(connecting)
        self.assertEqual(settings.writes, [])

        self.controller.snapshot_changed.emit(ready_snapshot(controls=False))
        self.assertEqual(
            settings.writes,
            [("ble/lastDeviceIdentifier", identifier)],
        )
        self.assertEqual(set(settings.data), {"ble/lastDeviceIdentifier"})
        self.assertNotIn(password, repr(settings.data))

    def test_cancel_failure_stale_setting_and_settings_errors_are_harmless(self) -> None:
        settings = FakeSettings({"ble/lastDeviceIdentifier": "STALE-ID"})
        self.window.close()
        self.window.deleteLater()
        self.window = MainWindow(self.controller, settings=settings)
        self.window.show()
        self.controller.devices_changed.emit(
            [{"identifier": "FOUND-ID", "name": "HWCDQ", "rssi": -50}]
        )
        self.assertEqual(self.window.device_combo.currentData(Qt.UserRole), "FOUND-ID")

        with patch.object(QInputDialog, "exec", return_value=QDialog.Rejected):
            self.window.connect_button.click()
        self.assertEqual(settings.writes, [])

        with (
            patch.object(QInputDialog, "exec", return_value=QDialog.Accepted),
            patch.object(QInputDialog, "textValue", return_value="bad"),
        ):
            self.window.connect_button.click()
        failed = ready_snapshot(controls=False)
        failed.update({"state": "error", "authenticated": False})
        self.controller.snapshot_changed.emit(failed)
        self.controller.operation_changed.emit(
            {
                "busy": False,
                "name": "connect",
                "message": "Ошибка: неверный пароль",
                "completed": True,
            }
        )
        self.controller.snapshot_changed.emit(ready_snapshot(controls=False))
        self.assertEqual(settings.writes, [])

        broken = FakeSettings(fail_reads=True, fail_writes=True)
        self.window.close()
        self.window.deleteLater()
        self.window = MainWindow(self.controller, settings=broken)
        self.window._pending_connection_identifier = "FOUND-ID"
        self.controller.snapshot_changed.emit(ready_snapshot(controls=False))
        self.assertEqual(broken.writes, [])

    def test_simulator_does_not_read_or_write_real_device_preferences(self) -> None:
        settings = FakeSettings(fail_reads=True, fail_writes=True)
        self.window.close()
        self.window.deleteLater()
        self.window = MainWindow(
            self.controller,
            initial_mode="simulation",
            settings=settings,
        )
        self.window._pending_connection_identifier = "HWCDQ-SIMULATOR"
        self.controller.snapshot_changed.emit(ready_snapshot())
        self.assertEqual(settings.reads, [])
        self.assertEqual(settings.writes, [])

    def test_device_selection_prompts_for_password_without_exposing_input(self) -> None:
        self.controller.devices_changed.emit(
            [
                {
                    "identifier": "AA:BB:CC:DD:EE:FF",
                    "name": "HWCDQBLE_NIUB",
                    "rssi": -51,
                    "service_uuids": ["FFE1"],
                }
            ]
        )
        self.app.processEvents()
        self.assertIn("HWCDQBLE_NIUB", self.window.device_combo.currentText())

        with (
            patch.object(QInputDialog, "exec", return_value=QDialog.Accepted),
            patch.object(
                QInputDialog,
                "textValue",
                return_value="session-only-password",
            ),
        ):
            self.window.connect_button.click()
        self.assertEqual(
            self.controller.calls[-1],
            ("connect_device", "AA:BB:CC:DD:EE:FF", "session-only-password"),
        )
        texts = " ".join(
            widget.text() for widget in self.window.findChildren(QLineEdit)
        )
        self.assertNotIn("session-only-password", texts)

    def test_auth_dialog_is_compact_password_prompt_with_explicit_buttons(self) -> None:
        dialog = self.window._create_auth_dialog()
        try:
            self.assertEqual(dialog.windowTitle(), "Доступ к HWCDQ")
            self.assertEqual(
                dialog.labelText(),
                "Пароль приложения (не Bluetooth PIN). "
                "Пусто — ключ из APK. Не сохраняется.",
            )
            self.assertEqual(dialog.textEchoMode(), QLineEdit.Password)
            buttons = dialog.findChild(QDialogButtonBox)
            self.assertIsNotNone(buttons)
            assert buttons is not None
            self.assertEqual(
                buttons.button(QDialogButtonBox.Ok).text(), "Подключиться"
            )
            self.assertEqual(
                buttons.button(QDialogButtonBox.Cancel).text(), "Отмена"
            )
        finally:
            dialog.deleteLater()

    def test_empty_auth_dialog_value_selects_apk_fallback(self) -> None:
        self.controller.devices_changed.emit(
            [
                {
                    "identifier": "HWCDQ-SIMULATOR",
                    "name": "HWCDQBLE_NIUB",
                    "rssi": -42,
                    "service_uuids": ["FFE1"],
                }
            ]
        )
        self.app.processEvents()

        with (
            patch.object(QInputDialog, "exec", return_value=QDialog.Accepted),
            patch.object(QInputDialog, "textValue", return_value=""),
        ):
            self.window.connect_button.click()

        self.assertEqual(
            self.controller.calls[-1],
            ("connect_device", "HWCDQ-SIMULATOR", ""),
        )

    def test_telemetry_and_selected_gatt_roles_are_exact_and_textual(self) -> None:
        self.controller.snapshot_changed.emit(ready_snapshot(controls=False))
        self.controller.gatt_changed.emit(
            {
                "services": [
                    {
                        "uuid": "0000FFE1-0000-1000-8000-00805F9B34FB",
                        "characteristics": [
                            {
                                "uuid": "FFE2",
                                "properties": [
                                    "indicate",
                                    "notify",
                                    "read",
                                    "write",
                                    "write-without-response",
                                ],
                            },
                            {
                                "uuid": "FFE3",
                                "properties": ["write", "write-without-response"],
                            },
                        ],
                    }
                ],
                "selected": {
                    "service_uuid": "0000FFE1-0000-1000-8000-00805F9B34FB",
                    "rx_uuid": "FFE2",
                    "tx_uuid": "FFE3",
                    "write_with_response": True,
                    "wnr_chunk_size": 120,
                },
                "error": None,
            }
        )
        self.app.processEvents()

        self.assertEqual(
            self.window.readings["output_voltage"].value_label.text(), "83.91"
        )
        self.assertEqual(
            self.window.readings["accumulated_capacity_ah"].value_label.text(),
            "12.345",
        )
        self.assertEqual(self.window.gatt_tree.topLevelItemCount(), 1)
        self.assertEqual(self.window.gatt_tree.topLevelItem(0).childCount(), 2)
        self.assertIn("подтверждением", self.window.gatt_role_labels["write_mode"].text())
        self.assertIn("120", self.window.gatt_role_labels["chunk_size"].text())

    def test_password_packet_is_redacted_before_display_copy_or_export_storage(self) -> None:
        plaintext = "session-only-password"
        credential = codec.derive_password_credential(plaintext)
        frame = codec.encode_check_password(plaintext)
        frame_hex = frame.hex(" ").upper()
        self.controller.packet_logged.emit(
            {
                "timestamp": "2026-08-25T12:00:00.000",
                "direction": "TX",
                "opcode": 0x02,
                "summary": f"credential={credential}",
                "raw_hex": frame_hex,
                "decoded": {"credential": credential, "checksum": frame[-1]},
            }
        )
        self.app.processEvents()

        visible = " ".join(
            self.window.log_table.item(0, column).text()
            for column in range(self.window.log_table.columnCount())
        )
        self.assertNotIn(plaintext, visible)
        self.assertNotIn(credential, visible)
        self.assertNotIn(frame_hex, visible)
        self.assertIn("СКРЫТО", visible)

        self.window.log_table.selectRow(0)
        self.app.processEvents()
        self.window.copy_log_button.click()
        copied = QApplication.clipboard().text()
        self.assertNotIn(plaintext, copied)
        self.assertNotIn(credential, copied)
        self.assertNotIn(frame_hex, copied)
        self.assertIn("СКРЫТО", copied)
        retained = repr(self.window._log_entries)
        self.assertNotIn(plaintext, retained)
        self.assertNotIn(credential, retained)
        self.assertNotIn(frame_hex, retained)
        self.assertNotIn("checksum", retained)


class StartConfirmationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["hwcdq-dialog-tests"])

    def test_start_requires_safe_load_acknowledgement(self) -> None:
        dialog = StartConfirmationDialog(84.0, 10.0, simulated=False)
        start = dialog.buttons.button(QDialogButtonBox.Ok)
        self.assertFalse(start.isEnabled())
        self.assertIn("84.00 V", dialog.findChild(QObject, "startConfirmationSummary").text())
        dialog.acknowledge.setChecked(True)
        self.assertTrue(start.isEnabled())
        dialog.close()

    def test_setpoint_confirmation_shows_exact_value_and_limit(self) -> None:
        dialog = SetpointConfirmationDialog(
            title="Ограничение тока",
            value=10.0,
            maximum=20.0,
            unit="A",
            simulated=False,
        )
        summary = dialog.findChild(QObject, "setpointConfirmationSummary").text()
        self.assertIn("РЕАЛЬНОЕ УСТРОЙСТВО", summary)
        self.assertIn("10.00 A", summary)
        self.assertIn("20.00 A", summary)
        self.assertIn("Действующий максимум", summary)
        self.assertIsNotNone(dialog.findChild(QPushButton, "confirmSetpointButton"))
        dialog.close()


if __name__ == "__main__":
    unittest.main()
