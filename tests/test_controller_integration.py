from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from hwcdq_control.qt_controller import AppController  # noqa: E402
from hwcdq_control.diagnostics import DiagnosticLogger  # noqa: E402
from hwcdq_control.backend import (  # noqa: E402
    DeviceAdvertisement,
    FakeTransport,
    SIMULATED_IDENTIFIER,
)
from tools import hwcdq_protocol as codec  # noqa: E402


class ImmediateWriteFailureTransport(FakeTransport):
    """Fail selected complete simulator frames before FakeTransport handles them."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_opcodes: set[int] = set()
        self.failed_write_attempts: list[int] = []

    async def write(self, characteristic_uuid, data, *, response):  # type: ignore[no-untyped-def]
        opcode = int(codec.decode_packet(data)["opcode"])
        if opcode in self.fail_opcodes:
            self.failed_write_attempts.append(opcode)
            raise RuntimeError(f"simulated immediate write failure 0x{opcode:02X}")
        await super().write(characteristic_uuid, data, response=response)


class ControllerSimulationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.controller = AppController(
            simulate=True,
            output_controls_enabled=True,
            scan_duration=0.01,
        )
        self.devices: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.gatt: list[dict[str, Any]] = []
        self.packets: list[dict[str, Any]] = []
        self.operations: list[dict[str, Any]] = []
        self.controller.devices_changed.connect(self._capture_devices)
        self.controller.snapshot_changed.connect(self.snapshots.append)
        self.controller.gatt_changed.connect(self.gatt.append)
        self.controller.packet_logged.connect(self.packets.append)
        self.controller.operation_changed.connect(self.operations.append)

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.app.processEvents()

    def _capture_devices(self, payload: Any) -> None:
        self.devices = list(payload or [])

    def _wait(self, predicate: Callable[[], bool], *, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("timed out waiting for controller condition")

    def _latest_snapshot(self) -> dict[str, Any]:
        return self.snapshots[-1] if self.snapshots else {}

    def test_full_simulated_operator_cycle_and_redacted_evidence(self) -> None:
        self.controller.announce_mode()
        self.controller.start_scan()
        self._wait(lambda: len(self.devices) == 1)
        self._wait(
            lambda: any(
                item.get("name") == "scan"
                and item.get("completed") is True
                and item.get("busy") is False
                for item in self.operations
            )
        )
        self.assertEqual(self.devices[0]["identifier"], "HWCDQ-SIMULATOR")

        self.controller.connect_device("HWCDQ-SIMULATOR", "")
        self._wait(lambda: self._latest_snapshot().get("state") == "ready")
        snapshot = self._latest_snapshot()
        self.assertTrue(snapshot["authenticated"])
        self.assertEqual(snapshot["config"]["target_voltage"], 84.0)
        self.assertEqual(snapshot["telemetry"]["module_count"], 2)
        self.assertFalse(self.gatt[-1]["selected"]["write_with_response"])

        rendered_packets = repr(self.packets)
        self.assertNotIn("'password':", rendered_packets)
        self.assertNotIn("D41D8CD98F00B204E9800998ECF8427E", rendered_packets)
        self.assertNotIn("44 34 31 44 38 43", rendered_packets)
        self.assertIn("REDACTED", rendered_packets)

        self.controller.set_voltage(90.0)
        self._wait(
            lambda: self._latest_snapshot().get("config", {}).get("target_voltage")
            == 90.0
        )
        self.controller.set_current(12.0)
        self._wait(
            lambda: self._latest_snapshot().get("config", {}).get("target_current")
            == 12.0
        )

        self.controller.start_output(90.0, 12.0)
        self._wait(
            lambda: self._latest_snapshot().get("telemetry", {}).get("output_enabled")
            is True
        )
        self.controller.stop_output()
        self._wait(
            lambda: self._latest_snapshot().get("telemetry", {}).get("output_enabled")
            is False
        )

        expected_completed = {
            "scan",
            "connect",
            "set_voltage",
            "set_current",
            "start",
            "stop",
        }

        def completed_operations() -> set[object]:
            return {
                item.get("name")
                for item in self.operations
                if item.get("busy") is False
                and not str(item.get("message", "")).startswith("Ошибка")
            }

        self._wait(lambda: expected_completed <= completed_operations())
        self.assertTrue(
            expected_completed <= completed_operations()
        )

    def test_stop_is_rejected_while_fresh_telemetry_says_output_is_off(self) -> None:
        self.controller.connect_device(SIMULATED_IDENTIFIER, "")
        self._wait(lambda: self._latest_snapshot().get("state") == "ready")
        self.assertIs(
            self._latest_snapshot().get("telemetry", {}).get("output_enabled"),
            False,
        )
        output_packets_before = sum(
            packet.get("direction") == "TX"
            and packet.get("opcode") == codec.OP_OUTPUT_CONTROL
            for packet in self.packets
        )

        self.controller.stop_output()
        self._wait(
            lambda: any(
                item.get("name") == "validation"
                and "не подтверждён как включённый"
                in str(item.get("message"))
                for item in self.operations
            )
        )
        output_packets_after = sum(
            packet.get("direction") == "TX"
            and packet.get("opcode") == codec.OP_OUTPUT_CONTROL
            for packet in self.packets
        )
        self.assertEqual(output_packets_after, output_packets_before)

    def test_controller_submits_confirmed_pair_as_one_atomic_start_operation(self) -> None:
        self.controller.connect_device(SIMULATED_IDENTIFIER, "")
        self._wait(lambda: self._latest_snapshot().get("state") == "ready")
        session = self.controller._session
        self.assertIsNotNone(session)
        assert session is not None
        transport = session.transport
        self.assertIsInstance(transport, FakeTransport)
        assert isinstance(transport, FakeTransport)
        transport.write_records.clear()

        self.controller.start_output(84.0, 10.0)
        self._wait(
            lambda: self._latest_snapshot().get("telemetry", {}).get(
                "output_enabled"
            )
            is True
        )

        self.assertEqual(
            [record.opcode for record in transport.write_records],
            [
                codec.OP_GET_CONFIG,
                codec.OP_GET_TELEMETRY,
                codec.OP_OUTPUT_CONTROL,
                codec.OP_GET_TELEMETRY,
            ],
        )
        self.assertTrue(
            any(
                item.get("name") == "start"
                and item.get("completed") is True
                for item in self.operations
            )
        )

    def test_stop_controller_gate_does_not_inherit_ready_or_config_requirements(self) -> None:
        class ConnectedTransport:
            connected = True

        class AuthenticatedLoadingSession:
            state = "loading"
            transport = ConnectedTransport()
            authenticated = True
            telemetry_fresh = True
            telemetry = {"output_enabled": True}
            config_fresh = False
            config = None

        self.assertIsNone(
            self.controller._stop_block_reason(AuthenticatedLoadingSession())  # type: ignore[arg-type]
        )

    def test_monitoring_controller_rejects_stop_without_submitting_a_write(self) -> None:
        controller = AppController(
            simulate=True,
            output_controls_enabled=False,
            scan_duration=0.01,
        )
        snapshots: list[dict[str, Any]] = []
        packets: list[dict[str, Any]] = []
        operations: list[dict[str, Any]] = []
        controller.snapshot_changed.connect(snapshots.append)
        controller.packet_logged.connect(packets.append)
        controller.operation_changed.connect(operations.append)
        try:
            controller.connect_device(SIMULATED_IDENTIFIER, "")
            self._wait(
                lambda: bool(snapshots) and snapshots[-1].get("state") == "ready"
            )
            controller.stop_output()
            self._wait(
                lambda: any(
                    item.get("name") == "validation"
                    and "режим управления не включён"
                    in str(item.get("message"))
                    for item in operations
                )
            )
            self.assertFalse(
                any(
                    packet.get("direction") == "TX"
                    and packet.get("opcode") == codec.OP_OUTPUT_CONTROL
                    for packet in packets
                )
            )
        finally:
            controller.shutdown()
            self.app.processEvents()

    def test_scan_keeps_first_seen_order_through_updates_and_final_merge(self) -> None:
        advertisements = (
            DeviceAdvertisement("A", "first", -80, ("FFE0",)),
            DeviceAdvertisement("B", "second", -20, ("FFE0",)),
            DeviceAdvertisement("A", "first-updated", -10, ("FFE0",)),
        )
        final_batch = (
            DeviceAdvertisement("B", "second-final", -25, ("FFE0",)),
            DeviceAdvertisement("C", "final-only", -40, ("FFE0",)),
            DeviceAdvertisement("A", "first-final", -5, ("FFE0",)),
        )

        class ScriptedScanner:
            async def scan(self, _duration, on_advertisement):  # type: ignore[no-untyped-def]
                for device in advertisements:
                    on_advertisement(device)
                    await asyncio.sleep(0)
                return final_batch

        controller = AppController(simulate=True, scan_duration=0.01)
        emissions: list[list[dict[str, Any]]] = []
        operations: list[dict[str, Any]] = []
        controller.devices_changed.connect(
            lambda items: emissions.append(list(items or []))
        )
        controller.operation_changed.connect(operations.append)
        try:
            with patch(
                "hwcdq_control.qt_controller.FakeScanner",
                return_value=ScriptedScanner(),
            ):
                controller.start_scan()
                self._wait(
                    lambda: any(
                        item.get("name") == "scan"
                        and item.get("completed") is True
                        for item in operations
                    )
                )

            ordered_ids = [
                [str(device["identifier"]) for device in emission]
                for emission in emissions
                if emission
            ]
            self.assertEqual(
                ordered_ids,
                [["A"], ["A", "B"], ["A", "B"], ["A", "B", "C"]],
            )
            final_by_id = {item["identifier"]: item for item in emissions[-1]}
            self.assertEqual(final_by_id["A"]["name"], "first-final")
            self.assertEqual(final_by_id["B"]["name"], "second-final")
            self.assertEqual(final_by_id["C"]["name"], "final-only")
        finally:
            controller.shutdown()
            self.app.processEvents()

    def test_cancelled_scan_cannot_publish_after_a_new_scan_starts(self) -> None:
        old_started = threading.Event()
        old_cancelled = threading.Event()
        release_old = threading.Event()
        old_finished = threading.Event()
        new_finished = threading.Event()

        class CancellationResistantOldScanner:
            async def scan(self, _duration, on_advertisement):  # type: ignore[no-untyped-def]
                on_advertisement(DeviceAdvertisement("OLD", "old", -80))
                old_started.set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    old_cancelled.set()
                    while not release_old.is_set():
                        await asyncio.sleep(0.001)
                on_advertisement(DeviceAdvertisement("STALE-AD", "stale", -1))
                old_finished.set()
                return (DeviceAdvertisement("STALE-FINAL", "stale-final", -1),)

        class NewScanner:
            async def scan(self, _duration, on_advertisement):  # type: ignore[no-untyped-def]
                on_advertisement(DeviceAdvertisement("NEW", "new", -30))
                new_finished.set()
                return (DeviceAdvertisement("NEW", "new-final", -25),)

        controller = AppController(simulate=True, scan_duration=0.01)
        emissions: list[list[str]] = []
        controller.devices_changed.connect(
            lambda items: emissions.append(
                [str(item["identifier"]) for item in (items or [])]
            )
        )
        scanners = [CancellationResistantOldScanner(), NewScanner()]
        try:
            with patch(
                "hwcdq_control.qt_controller.FakeScanner",
                side_effect=scanners,
            ):
                controller.start_scan()
                self.assertTrue(old_started.wait(timeout=1.0))
                self._wait(lambda: any("OLD" in emission for emission in emissions))
                controller.stop_scan()
                self.assertTrue(old_cancelled.wait(timeout=1.0))

                controller.start_scan()
                new_scan_boundary = len(emissions) - 1
                self.assertTrue(new_finished.wait(timeout=1.0))
                self._wait(lambda: emissions and emissions[-1] == ["NEW"])

                release_old.set()
                self.assertTrue(old_finished.wait(timeout=1.0))
                deadline = time.monotonic() + 0.1
                while time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.002)

            after_new_scan = emissions[new_scan_boundary:]
            self.assertTrue(any(emission == ["NEW"] for emission in after_new_scan))
            self.assertFalse(
                any(
                    identifier.startswith("STALE") or identifier == "OLD"
                    for emission in after_new_scan
                    for identifier in emission
                )
            )
            self.assertEqual(
                [item["identifier"] for item in controller._device_payloads()],
                ["NEW"],
            )
        finally:
            release_old.set()
            controller.shutdown()
            self.app.processEvents()

    def test_stale_scan_completion_cannot_finalize_the_current_scan(self) -> None:
        operations: list[dict[str, Any]] = []
        self.controller.operation_changed.connect(operations.append)
        with self.controller._scan_state_lock:
            self.controller._scan_generation = 2
        stale_future: Future[object] = Future()
        self.controller._finish_future(
            stale_future,
            "scan",
            "Старый поиск завершён",
            completion_guard=lambda: (
                self.controller._scan_completion_is_current(1)
            ),
        )

        stale_future.set_result(None)
        self.app.processEvents()

        self.assertFalse(
            any(
                item.get("name") == "scan" and item.get("completed") is True
                for item in operations
            )
        )

    def test_completion_of_one_overlapping_operation_keeps_ui_busy(self) -> None:
        first: Future[object] = Future()
        second: Future[object] = Future()
        self.controller._finish_future(first, "set_voltage", "V done")
        self.controller._finish_future(second, "stop", "Stop done")

        first.set_result(None)
        self._wait(
            lambda: any(
                item.get("name") == "set_voltage" and item.get("completed") is True
                for item in self.operations
            )
        )
        voltage_done = next(
            item
            for item in reversed(self.operations)
            if item.get("name") == "set_voltage" and item.get("completed") is True
        )
        self.assertTrue(voltage_done["busy"])

        second.set_result(None)
        self._wait(
            lambda: any(
                item.get("name") == "stop"
                and item.get("completed") is True
                and item.get("busy") is False
                for item in self.operations
            )
        )

    def test_debug_trace_covers_full_simulated_transaction_pipeline(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as directory:
            log_path = Path(directory) / "hwcdq-debug.jsonl"
            diagnostics = DiagnosticLogger(log_path, enabled=True)
            controller = AppController(
                simulate=True,
                output_controls_enabled=True,
                scan_duration=0.01,
                diagnostics=diagnostics,
            )
            devices: list[dict[str, Any]] = []
            snapshots: list[dict[str, Any]] = []
            operations: list[dict[str, Any]] = []
            controller.devices_changed.connect(lambda items: devices.extend(items or []))
            controller.snapshot_changed.connect(snapshots.append)
            controller.operation_changed.connect(operations.append)

            def latest_snapshot() -> dict[str, Any]:
                return snapshots[-1] if snapshots else {}

            try:
                controller.announce_mode()
                controller.start_scan()
                self._wait(lambda: bool(devices))
                self._wait(
                    lambda: any(
                        item.get("name") == "scan" and item.get("completed") is True
                        for item in operations
                    )
                )
                controller.connect_device("HWCDQ-SIMULATOR", "")
                self._wait(lambda: latest_snapshot().get("state") == "ready")
                controller.set_voltage(90.0)
                self._wait(
                    lambda: latest_snapshot().get("config", {}).get("target_voltage")
                    == 90.0
                )
                controller.set_current(12.0)
                self._wait(
                    lambda: latest_snapshot().get("config", {}).get("target_current")
                    == 12.0
                )
                controller.start_output(90.0, 12.0)
                self._wait(
                    lambda: latest_snapshot().get("telemetry", {}).get("output_enabled")
                    is True
                )
                controller.stop_output()
                self._wait(
                    lambda: latest_snapshot().get("telemetry", {}).get("output_enabled")
                    is False
                )
            finally:
                controller.shutdown()
                diagnostics.close()
                self.app.processEvents()

            raw_log = log_path.read_text(encoding="utf-8")
            records = [json.loads(line) for line in raw_log.splitlines()]
            self.assertTrue(records)
            self.assertEqual(
                [record["sequence"] for record in records],
                list(range(1, len(records) + 1)),
            )

            events = {record["event"] for record in records}
            self.assertTrue(
                {
                    "scan_started",
                    "advertisement_observed",
                    "connect_started",
                    "topology_selected",
                    "notifications_enabled",
                    "transaction_queued",
                    "transaction_acquired",
                    "request_started",
                    "tx_frame_prepared",
                    "chunk_write_started",
                    "chunk_write_completed",
                    "fragment_received",
                    "fragment_processed",
                    "rx_frame_decoded",
                    "response_matched",
                    "mutation_outcome_ambiguous",
                    "mutation_evaluated",
                    "mutation_outcome_resolved",
                    "transaction_completed",
                    "transaction_released",
                    "shutdown_started",
                    "shutdown_finished",
                }
                <= events
            )

            voltage_queue = next(
                record
                for record in records
                if record["event"] == "transaction_queued"
                and record["details"].get("operation") == "set_voltage"
            )
            transaction_id = voltage_queue["details"]["transaction_id"]
            ordered_events = [
                record["event"]
                for record in records
                if record["details"].get("transaction_id") == transaction_id
            ]
            positions = {
                event: ordered_events.index(event)
                for event in (
                    "transaction_queued",
                    "transaction_acquired",
                    "chunk_write_started",
                    "chunk_write_completed",
                    "response_matched",
                    "transaction_completed",
                    "transaction_released",
                )
            }
            self.assertEqual(list(positions.values()), sorted(positions.values()))

            lowered = raw_log.casefold()
            self.assertNotIn("d41d8cd98f00b204e9800998ecf8427e", lowered)
            self.assertNotIn("44 34 31 44 38 43", lowered)
            self.assertNotIn('"password"', lowered)
            self.assertNotIn('"key_press"', lowered)
            self.assertIn('"opcode":2,"redacted":"[redacted]"', lowered)

    def test_debug_trace_scrubs_unicode_password_through_async_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as directory:
            log_path = Path(directory) / "hwcdq-debug.jsonl"
            diagnostics = DiagnosticLogger(log_path, enabled=True)
            controller = AppController(
                simulate=True,
                output_controls_enabled=False,
                diagnostics=diagnostics,
            )
            operations: list[dict[str, Any]] = []
            controller.operation_changed.connect(operations.append)
            secret = "пароль-🔑"
            derived = codec.derive_password_credential(secret)
            try:
                controller.connect_device("HWCDQ-SIMULATOR", secret)
                self._wait(
                    lambda: any(
                        item.get("name") == "connect"
                        and item.get("completed") is True
                        for item in operations
                    )
                )
            finally:
                controller.shutdown()
                diagnostics.close()
                self.app.processEvents()

            persisted = log_path.read_bytes()
            encoded = secret.encode("utf-8")
            forbidden = (
                encoded,
                encoded.hex().encode("ascii"),
                encoded.hex(" ").upper().encode("ascii"),
                repr(encoded).encode("utf-8"),
                secret.encode("unicode_escape"),
                derived.encode("ascii"),
                derived.encode("ascii").hex().encode("ascii"),
                derived.encode("ascii").hex(" ").upper().encode("ascii"),
            )
            for rendering in forbidden:
                self.assertNotIn(rendering, persisted)
            records = [json.loads(line) for line in persisted.decode("utf-8").splitlines()]
            self.assertTrue(
                any(
                    record["event"] == "operation_finished"
                    and record["details"].get("operation") == "connect"
                    and record["details"].get("outcome") == "failed"
                    for record in records
                )
            )

    def test_connect_submit_failure_preserves_error_and_releases_secret_guard(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as directory:
            log_path = Path(directory) / "hwcdq-debug.jsonl"
            diagnostics = DiagnosticLogger(log_path, enabled=True)
            controller = AppController(
                simulate=True,
                output_controls_enabled=False,
                diagnostics=diagnostics,
            )
            operations: list[dict[str, Any]] = []
            controller.operation_changed.connect(operations.append)
            failure = RuntimeError("simulated worker submit failure")

            try:
                with patch.object(
                    controller._worker,
                    "submit",
                    side_effect=failure,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "simulated worker submit failure",
                    ):
                        controller.connect_device("HWCDQ-SIMULATOR", "guard-secret")

                self.assertFalse(diagnostics._secret_refcounts)
                self.assertFalse(operations[-1]["busy"])
                self.assertTrue(operations[-1]["completed"])
                self.assertEqual(operations[-1]["name"], "connect")
                self.assertIn("simulated worker submit failure", operations[-1]["message"])
            finally:
                controller.shutdown()
                diagnostics.close()
                self.app.processEvents()


class ControllerReconnectIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.controller: AppController | None = None
        self.snapshots: list[dict[str, Any]] = []
        self.operations: list[dict[str, Any]] = []
        self.packets: list[dict[str, Any]] = []

    def tearDown(self) -> None:
        if self.controller is not None:
            self.controller.shutdown()
        self.app.processEvents()

    def _wait(self, predicate: Callable[[], bool], *, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.003)
        self.fail("timed out waiting for reconnect condition")

    def _start_controller(
        self,
        factory: Callable[[], FakeTransport],
        *,
        controls: bool = False,
        reconnect_delays: tuple[float, ...] = (0.005, 0.01, 0.02),
    ) -> AppController:
        controller = AppController(
            simulate=True,
            output_controls_enabled=controls,
            telemetry_poll_interval=0.01,
            config_poll_interval=0.2,
            request_timeout=0.015,
            native_write_timeout=0.05,
            reconnect_delays=reconnect_delays,
            transport_factory=factory,
        )
        controller.snapshot_changed.connect(self.snapshots.append)
        controller.operation_changed.connect(self.operations.append)
        controller.packet_logged.connect(self.packets.append)
        self.controller = controller
        controller.connect_device(SIMULATED_IDENTIFIER, "")
        self._wait(
            lambda: bool(self.snapshots)
            and self.snapshots[-1].get("state") == "ready"
        )
        return controller

    def test_read_only_timeout_reconnects_and_reloads_snapshot(self) -> None:
        instances: list[FakeTransport] = []

        def factory() -> FakeTransport:
            transport = FakeTransport()
            instances.append(transport)
            return transport

        self._start_controller(factory)
        first = instances[0]
        first.drop_responses.add(codec.OP_GET_TELEMETRY)

        self._wait(
            lambda: len(instances) == 2
            and any(
                item.get("name") == "reconnect"
                and item.get("completed") is True
                and "восстановлено" in str(item.get("message", "")).casefold()
                for item in self.operations
            )
        )
        snapshot = self.snapshots[-1]
        self.assertEqual(snapshot["state"], "ready")
        self.assertTrue(snapshot["authenticated"])
        self.assertTrue(snapshot["config_fresh"])
        self.assertTrue(snapshot["telemetry_fresh"])
        self.assertEqual(
            [record.opcode for record in instances[1].write_records[:5]],
            [
                codec.OP_CHECK_PASSWORD,
                codec.OP_GET_FIRMWARE,
                codec.OP_GET_SERIAL,
                codec.OP_GET_CONFIG,
                codec.OP_GET_TELEMETRY,
            ],
        )

    def test_read_only_native_write_error_reconnects_and_reloads_snapshot(self) -> None:
        instances: list[FakeTransport] = []

        def factory() -> FakeTransport:
            transport: FakeTransport = (
                ImmediateWriteFailureTransport()
                if not instances
                else FakeTransport()
            )
            instances.append(transport)
            return transport

        self._start_controller(factory)
        first = instances[0]
        self.assertIsInstance(first, ImmediateWriteFailureTransport)
        first.fail_opcodes.add(codec.OP_GET_TELEMETRY)  # type: ignore[attr-defined]

        self._wait(
            lambda: len(instances) == 2
            and any(
                item.get("name") == "reconnect"
                and item.get("completed") is True
                and "восстановлено" in str(item.get("message", "")).casefold()
                for item in self.operations
            )
        )
        self.assertEqual(
            first.failed_write_attempts,  # type: ignore[attr-defined]
            [codec.OP_GET_TELEMETRY],
        )
        snapshot = self.snapshots[-1]
        self.assertEqual(snapshot["state"], "ready")
        self.assertTrue(snapshot["authenticated"])
        self.assertTrue(snapshot["config_fresh"])
        self.assertTrue(snapshot["telemetry_fresh"])

    def test_reconnect_stops_after_three_failed_attempts(self) -> None:
        instances: list[FakeTransport] = []

        class FailingConnectTransport(FakeTransport):
            async def connect(self, identifier, disconnected_callback):  # type: ignore[no-untyped-def]
                raise ConnectionError("simulated reconnect failure")

        def factory() -> FakeTransport:
            transport = (
                FakeTransport()
                if not instances
                else FailingConnectTransport()
            )
            instances.append(transport)
            return transport

        self._start_controller(factory)
        instances[0].drop_responses.add(codec.OP_GET_TELEMETRY)
        self._wait(
            lambda: any(
                item.get("name") == "reconnect"
                and item.get("completed") is True
                and "3 попыток" in str(item.get("message", ""))
                for item in self.operations
            )
        )
        self.assertEqual(len(instances), 4)
        deadline = time.monotonic() + 0.06
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.003)
        self.assertEqual(len(instances), 4)

    def test_failed_reconnect_orphan_blocks_next_client_until_finished(self) -> None:
        instances: list[FakeTransport] = []
        write_entered = threading.Event()
        write_active = threading.Event()
        release_write = threading.Event()
        write_exits: list[str] = []

        class CancellationResistantReconnectTransport(FakeTransport):
            async def write(self, characteristic_uuid, data, *, response):  # type: ignore[no-untyped-def]
                write_entered.set()
                write_active.set()
                try:
                    while not release_write.is_set():
                        try:
                            await asyncio.sleep(0.005)
                        except asyncio.CancelledError:
                            continue
                    await super().write(
                        characteristic_uuid,
                        data,
                        response=response,
                    )
                except BaseException as exc:
                    write_exits.append(f"{type(exc).__name__}: {exc}")
                    raise
                else:
                    write_exits.append("returned")
                finally:
                    write_active.clear()

        def factory() -> FakeTransport:
            if not instances:
                transport = FakeTransport()
            elif len(instances) == 1:
                transport = CancellationResistantReconnectTransport()
            else:
                transport = FakeTransport()
            instances.append(transport)
            return transport

        controller = self._start_controller(factory)
        instances[0].drop_responses.add(codec.OP_GET_TELEMETRY)
        self._wait(write_entered.is_set)
        orphan_session = controller._session
        try:
            # Allow well over the native-write timeout and the next reconnect
            # backoff.  A third transport must still not exist while the
            # orphan is cancellation-resistant.
            deadline = time.monotonic() + 0.4
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.003)
            self.assertFalse(release_write.is_set())
            self.assertTrue(write_active.is_set(), write_exits)
            self.assertIsNotNone(orphan_session)
            self.assertIs(orphan_session.transport, instances[1])  # type: ignore[union-attr]
            self.assertTrue(orphan_session._orphaned_write_tasks)  # type: ignore[union-attr]
            self.assertEqual(len(instances), 2)
        finally:
            release_write.set()

        self._wait(
            lambda: len(instances) == 3
            and any(
                item.get("name") == "reconnect"
                and item.get("completed") is True
                and "восстановлено" in str(item.get("message", "")).casefold()
                for item in self.operations
            )
        )

    def test_manual_connect_waits_for_orphaned_mutation_write(self) -> None:
        instances: list[FakeTransport] = []
        write_entered = threading.Event()
        write_active = threading.Event()
        release_write = threading.Event()
        mutation_attempts: list[int] = []

        class CancellationResistantMutationTransport(FakeTransport):
            async def write(self, characteristic_uuid, data, *, response):  # type: ignore[no-untyped-def]
                opcode = int(codec.decode_packet(data)["opcode"])
                if opcode != codec.OP_SET_VOLTAGE:
                    await super().write(
                        characteristic_uuid,
                        data,
                        response=response,
                    )
                    return
                mutation_attempts.append(opcode)
                write_entered.set()
                write_active.set()
                try:
                    while not release_write.is_set():
                        try:
                            await asyncio.sleep(0.005)
                        except asyncio.CancelledError:
                            continue
                    await super().write(
                        characteristic_uuid,
                        data,
                        response=response,
                    )
                finally:
                    write_active.clear()

        def factory() -> FakeTransport:
            transport: FakeTransport = (
                CancellationResistantMutationTransport()
                if not instances
                else FakeTransport()
            )
            instances.append(transport)
            return transport

        controller = self._start_controller(factory, controls=True)
        controller.set_voltage(79.0)
        self._wait(write_entered.is_set)
        self._wait(
            lambda: bool(self.snapshots)
            and self.snapshots[-1].get("state") == "error"
            and self.snapshots[-1].get("control_outcome_unknown") is True
        )
        controller.connect_device(SIMULATED_IDENTIFIER, "")
        try:
            deadline = time.monotonic() + 0.35
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.003)
            self.assertTrue(write_active.is_set())
            self.assertEqual(mutation_attempts, [codec.OP_SET_VOLTAGE])
            self.assertEqual(len(instances), 1)
            self.assertTrue(
                any(
                    item.get("name") == "connect"
                    and "предыдущего BLE-клиента" in str(item.get("message", ""))
                    for item in self.operations
                )
            )
        finally:
            release_write.set()

        self._wait(
            lambda: len(instances) == 2
            and bool(self.snapshots)
            and self.snapshots[-1].get("state") == "ready"
            and any(
                item.get("name") == "connect"
                and item.get("completed") is True
                and item.get("message") == "Подключено"
                for item in self.operations
            )
        )
        self.assertEqual(
            [record.opcode for record in instances[1].write_records[:5]],
            [
                codec.OP_CHECK_PASSWORD,
                codec.OP_GET_FIRMWARE,
                codec.OP_GET_SERIAL,
                codec.OP_GET_CONFIG,
                codec.OP_GET_TELEMETRY,
            ],
        )

    def test_manual_disconnect_cancels_reconnect_backoff(self) -> None:
        instances: list[FakeTransport] = []

        def factory() -> FakeTransport:
            transport = FakeTransport()
            instances.append(transport)
            return transport

        controller = self._start_controller(
            factory,
            reconnect_delays=(0.2,),
        )
        instances[0].drop_responses.add(codec.OP_GET_TELEMETRY)
        self._wait(
            lambda: any(
                item.get("name") == "reconnect"
                and item.get("busy") is True
                for item in self.operations
            )
        )
        controller.disconnect_device()
        self._wait(
            lambda: bool(self.snapshots)
            and self.snapshots[-1].get("state") == "disconnected"
        )
        deadline = time.monotonic() + 0.24
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertEqual(len(instances), 1)

    def test_shutdown_cancels_reconnect_backoff(self) -> None:
        instances: list[FakeTransport] = []

        def factory() -> FakeTransport:
            transport = FakeTransport()
            instances.append(transport)
            return transport

        controller = self._start_controller(
            factory,
            reconnect_delays=(0.2,),
        )
        instances[0].drop_responses.add(codec.OP_GET_TELEMETRY)
        self._wait(
            lambda: any(
                item.get("name") == "reconnect"
                and item.get("busy") is True
                for item in self.operations
            )
        )
        controller.shutdown()
        deadline = time.monotonic() + 0.24
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertEqual(len(instances), 1)
        self.assertFalse(controller._worker._thread.is_alive())

    def test_mutation_timeout_is_never_replayed_or_auto_reconnected(self) -> None:
        instances: list[FakeTransport] = []

        def factory() -> FakeTransport:
            transport = FakeTransport()
            instances.append(transport)
            return transport

        controller = self._start_controller(factory, controls=True)
        first = instances[0]
        first.drop_responses.add(codec.OP_SET_VOLTAGE)
        controller.set_voltage(79.0)
        self._wait(
            lambda: bool(self.snapshots)
            and self.snapshots[-1].get("state") == "error"
            and self.snapshots[-1].get("control_outcome_unknown") is True
        )
        deadline = time.monotonic() + 0.08
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.003)
        self.assertEqual(len(instances), 1)
        self.assertEqual(first.count_opcode(codec.OP_SET_VOLTAGE), 1)

    def test_mutation_native_write_error_is_never_replayed_or_reconnected(self) -> None:
        instances: list[FakeTransport] = []

        def factory() -> FakeTransport:
            transport: FakeTransport = ImmediateWriteFailureTransport()
            instances.append(transport)
            return transport

        controller = self._start_controller(factory, controls=True)
        first = instances[0]
        self.assertIsInstance(first, ImmediateWriteFailureTransport)
        first.fail_opcodes.add(codec.OP_SET_VOLTAGE)  # type: ignore[attr-defined]
        controller.set_voltage(79.0)
        self._wait(
            lambda: bool(self.snapshots)
            and self.snapshots[-1].get("state") == "error"
            and self.snapshots[-1].get("control_outcome_unknown") is True
        )
        deadline = time.monotonic() + 0.08
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.003)
        self.assertEqual(len(instances), 1)
        self.assertEqual(
            first.failed_write_attempts,  # type: ignore[attr-defined]
            [codec.OP_SET_VOLTAGE],
        )
        self.assertFalse(
            any(item.get("name") == "reconnect" for item in self.operations)
        )


if __name__ == "__main__":
    unittest.main()
