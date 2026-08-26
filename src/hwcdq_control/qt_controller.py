"""Thread-safe bridge between Qt Widgets and the asyncio BLE backend."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import ExitStack
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Coroutine

from PySide6.QtCore import QObject, Signal

from hwcdq import (
    AccessMode,
    ChargerSession,
    Credential,
    DeviceTarget,
    DiagnosticLogger,
    EventKind,
    PIDZOOM_HW178P,
    SafetyInterlockError,
    SessionEvent,
    SessionOptions,
    SessionState,
)
from hwcdq import protocol as codec
from hwcdq.bleak import BleakScanner as BleakScannerAdapter, BleakTransport
from hwcdq.redaction import REDACTED, redact_value
from hwcdq.testing import FakeScanner, FakeTransport


class AsyncioWorker:
    """Own one asyncio loop and every Bleak object on a plain Python thread."""

    def __init__(self, diagnostics: DiagnosticLogger | None = None) -> None:
        self._diagnostics = diagnostics
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="hwcdq-ble-worker",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("BLE worker event loop did not start")
        self._diag("worker_ready", thread_name=self._thread.name)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        self._diag("event_loop_started")
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._diag("event_loop_stopped")

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        loop = self._loop
        if loop is None or loop.is_closed():
            coroutine.close()
            raise RuntimeError("BLE worker is not running")
        name = getattr(getattr(coroutine, "cr_code", None), "co_name", "coroutine")
        self._diag("coroutine_submitted", coroutine=name)
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def close(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        self._diag("worker_shutdown_started")
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=3)
        self._diag("worker_shutdown_finished", thread_alive=self._thread.is_alive())

    def _diag(self, event: str, /, **details: Any) -> None:
        logger = self._diagnostics
        if logger is None:
            return
        try:
            logger.emit("controller.worker", event, **details)
        except BaseException:
            return


class AppController(QObject):
    """Fast Qt slots backed by one serialized charger session."""

    mode_changed = Signal(object)
    devices_changed = Signal(object)
    snapshot_changed = Signal(object)
    gatt_changed = Signal(object)
    packet_logged = Signal(object)
    operation_changed = Signal(object)

    _OPCODE_SUMMARIES = {
        codec.OP_GET_FIRMWARE: "Версия прошивки",
        codec.OP_CHECK_PASSWORD: "Проверка пароля",
        codec.OP_GET_SERIAL: "Серийный номер",
        codec.OP_GET_CONFIG: "Конфигурация",
        codec.OP_GET_TELEMETRY: "Телеметрия",
        codec.OP_SET_VOLTAGE: "Установка напряжения",
        codec.OP_SET_CURRENT: "Установка тока",
        codec.OP_OUTPUT_CONTROL: "Управление выходом",
    }

    def __init__(
        self,
        *,
        simulate: bool = False,
        output_controls_enabled: bool = False,
        scan_duration: float = 5.0,
        telemetry_poll_interval: float = 5.0,
        config_poll_interval: float = 60.0,
        request_timeout: float = 8.0,
        native_write_timeout: float = 3.0,
        reconnect_delays: Sequence[float] = (1.0, 2.0, 5.0),
        transport_factory: Callable[[], Any] | None = None,
        diagnostics: DiagnosticLogger | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if scan_duration <= 0:
            raise ValueError("scan duration must be positive")
        if telemetry_poll_interval <= 0 or config_poll_interval <= 0:
            raise ValueError("poll intervals must be positive")
        if request_timeout <= 0 or native_write_timeout <= 0:
            raise ValueError("BLE timeouts must be positive")
        normalized_reconnect_delays = tuple(float(value) for value in reconnect_delays)
        if (
            not normalized_reconnect_delays
            or len(normalized_reconnect_delays) > 3
            or any(value < 0 for value in normalized_reconnect_delays)
        ):
            raise ValueError(
                "reconnect delays must contain one to three non-negative values"
            )
        self.simulate = bool(simulate)
        self.output_controls_enabled = bool(output_controls_enabled)
        self.scan_duration = float(scan_duration)
        self.telemetry_poll_interval = float(telemetry_poll_interval)
        self.config_poll_interval = float(config_poll_interval)
        self.request_timeout = float(request_timeout)
        self.native_write_timeout = float(native_write_timeout)
        self.reconnect_delays = normalized_reconnect_delays
        self._transport_factory = transport_factory
        self._diagnostics = diagnostics
        self._worker = AsyncioWorker(diagnostics)
        self._session: ChargerSession | None = None
        self._scan_future: Future[Any] | None = None
        self._scan_future_generation: int | None = None
        self._scan_state_lock = threading.RLock()
        self._scan_generation = 0
        self._cancelled_scan_generations: set[int] = set()
        self._discovered: dict[str, Any] = {}
        self._operation_lock = threading.Lock()
        self._active_futures: dict[Future[Any], str] = {}
        self._desired_identifier: str | None = None
        self._desired_credential: Credential | None = None
        self._connection_secret_guard: ExitStack | None = None
        self._connection_intent_generation = 0
        self._reconnect_task: asyncio.Task[None] | None = None
        self._closed = False
        self._diag(
            "controller.lifecycle",
            "controller_initialized",
            simulate=self.simulate,
            output_controls_enabled=self.output_controls_enabled,
            scan_duration_seconds=self.scan_duration,
            telemetry_poll_interval_seconds=self.telemetry_poll_interval,
            config_poll_interval_seconds=self.config_poll_interval,
            request_timeout_seconds=self.request_timeout,
            native_write_timeout_seconds=self.native_write_timeout,
            reconnect_delays_seconds=self.reconnect_delays,
        )

    def announce_mode(self) -> None:
        if self.simulate:
            kind = "simulation"
            label = "СИМУЛЯЦИЯ — данные не получены от реального устройства"
        elif self.output_controls_enabled:
            kind = "control"
            label = "УПРАВЛЕНИЕ — изменяющие команды разблокированы"
        else:
            kind = "monitoring"
            label = "МОНИТОРИНГ — изменяющие команды заблокированы"
        self._diag(
            "controller.mode",
            "mode_announced",
            mode=kind,
            output_controls_enabled=self.output_controls_enabled,
        )
        self.mode_changed.emit({"kind": kind, "label": label})
        self.snapshot_changed.emit(self._empty_snapshot())

    def start_scan(self) -> None:
        if self._scan_future is not None and not self._scan_future.done():
            self._diag("controller.action", "scan_ignored", reason="already_running")
            return
        self._diag(
            "controller.action",
            "scan_submitted",
            duration_seconds=self.scan_duration,
            simulated=self.simulate,
        )
        with self._scan_state_lock:
            self._scan_generation += 1
            scan_generation = self._scan_generation
            self._cancelled_scan_generations.clear()
            self._discovered = {}
        self.devices_changed.emit([])
        self._set_operation(True, "scan", "Поиск BLE-устройств…")
        self._scan_future = self._worker.submit(self._scan(scan_generation))
        self._scan_future_generation = scan_generation
        self._finish_future(
            self._scan_future,
            "scan",
            "Поиск завершён",
            completion_guard=lambda: self._scan_completion_is_current(
                scan_generation
            ),
        )

    def stop_scan(self) -> None:
        future = self._scan_future
        if future is not None and not future.done():
            self._diag("controller.action", "scan_stop_submitted")
            generation = self._scan_future_generation
            if generation is not None:
                with self._scan_state_lock:
                    self._cancelled_scan_generations.add(generation)
            future.cancel()
        else:
            self._diag("controller.action", "scan_stop_ignored", reason="not_running")

    def connect_device(self, identifier: str, password: str) -> None:
        if not identifier:
            self._emit_local_error("Устройство не выбрано")
            return
        if self._scan_future is not None and not self._scan_future.done():
            self._emit_local_error("Дождитесь завершения поиска или остановите его")
            return
        credential = Credential.from_password(password)
        temporary_secret_guard: ExitStack | None = None
        if self._diagnostics is not None:
            temporary_secret_guard = ExitStack()
            temporary_secret_guard.enter_context(
                self._diagnostics.register_secret(password)
            )
        connect_coroutine: Coroutine[Any, Any, Any] | None = None
        try:
            self._diag(
                "controller.action",
                "connect_submitted",
                identifier=identifier,
                simulated=self.simulate,
            )
            self._set_operation(True, "connect", "Подключение и проверка протокола…")
            connect_coroutine = self._connect(identifier, credential)
            future = self._worker.submit(connect_coroutine)
        except BaseException as exc:
            if connect_coroutine is not None:
                connect_coroutine.close()
            detail = str(exc).strip() or type(exc).__name__
            self._set_operation(
                False,
                "connect",
                f"Ошибка: {detail}",
                completed=True,
            )
            if temporary_secret_guard is not None:
                temporary_secret_guard.close()
            raise
        password = ""

        previous_connection_guard = self._connection_secret_guard
        self._connection_secret_guard = None

        def finish_connect_secrets() -> None:
            if temporary_secret_guard is not None:
                temporary_secret_guard.close()
            if previous_connection_guard is not None:
                previous_connection_guard.close()
            failed = future.cancelled()
            if not failed:
                try:
                    failed = future.exception() is not None
                except BaseException:
                    failed = True
            if failed:
                self._clear_connection_secret_guard()

        self._finish_future(
            future,
            "connect",
            "Подключено",
            finalizer=finish_connect_secrets,
        )

    def disconnect_device(self) -> None:
        self._diag("controller.action", "disconnect_submitted")
        self._set_operation(True, "disconnect", "Отключение…")
        future = self._worker.submit(self._disconnect(manual=True))
        self._finish_future(
            future,
            "disconnect",
            "Отключено",
            finalizer=self._clear_connection_secret_guard,
        )

    def refresh(self) -> None:
        self._diag("controller.action", "refresh_submitted")
        self._set_operation(True, "refresh", "Чтение конфигурации и телеметрии…")
        future = self._worker.submit(self._refresh())
        self._finish_future(future, "refresh", "Данные обновлены")

    def set_voltage(self, volts: float) -> None:
        self._diag(
            "controller.action",
            "set_voltage_submitted",
            volts=volts,
            operator_confirmed=True,
        )
        self._set_operation(True, "set_voltage", "Установка напряжения и readback…")
        future = self._worker.submit(self._set_voltage(volts))
        self._finish_future(future, "set_voltage", "Напряжение подтверждено readback")

    def set_current(self, amps: float) -> None:
        self._diag(
            "controller.action",
            "set_current_submitted",
            amps=amps,
            operator_confirmed=True,
        )
        self._set_operation(True, "set_current", "Установка тока и readback…")
        future = self._worker.submit(self._set_current(amps))
        self._finish_future(future, "set_current", "Ток подтверждён readback")

    def start_output(self, confirmed_volts: float, confirmed_amps: float) -> None:
        self._diag(
            "controller.action",
            "start_submitted",
            operator_confirmed=True,
            confirmed_volts=confirmed_volts,
            confirmed_amps=confirmed_amps,
        )
        self._set_operation(True, "start", "Включение выхода и readback…")
        future = self._worker.submit(
            self._start_output(confirmed_volts, confirmed_amps)
        )
        self._finish_future(future, "start", "Выход включён и подтверждён")

    def stop_output(self) -> None:
        block_reason = self._stop_block_reason(self._session)
        if block_reason is not None:
            self._diag(
                "controller.action",
                "stop_ignored",
                reason="safety_interlock",
                detail=block_reason,
            )
            self._emit_local_error(block_reason)
            return
        # Stop is intentionally accepted even while another operation is busy;
        # the backend places it at the head of the next transaction slot.
        with self._operation_lock:
            stop_pending = "stop" in self._active_futures.values()
        if stop_pending:
            self._diag(
                "controller.action",
                "stop_ignored",
                reason="already_pending",
            )
            self._emit_local_error("STOP уже поставлен в очередь")
            return
        self._diag("controller.action", "stop_submitted", priority=0)
        self.operation_changed.emit(
            {"busy": True, "name": "stop", "message": "STOP поставлен приоритетно…"}
        )
        future = self._worker.submit(self._stop_output())
        self._finish_future(future, "stop", "Выход остановлен и подтверждён")

    def shutdown(self) -> None:
        if self._closed:
            self._diag("controller.lifecycle", "shutdown_ignored", reason="already_closed")
            return
        self._closed = True
        self._diag("controller.lifecycle", "shutdown_started")
        future = self._worker.submit(self._disconnect(manual=True))
        try:
            future.result(timeout=2.5)
        except BaseException:
            future.cancel()
        self._clear_connection_secret_guard()
        self._worker.close()
        self._diag("controller.lifecycle", "shutdown_finished")

    async def _scan(self, generation: int) -> None:
        scanner = FakeScanner() if self.simulate else BleakScannerAdapter()
        self._diag(
            "ble.scan",
            "scan_started",
            simulated=self.simulate,
            duration_seconds=self.scan_duration,
            generation=generation,
        )

        def on_advertisement(device: Any) -> None:
            with self._scan_state_lock:
                if not self._scan_update_is_current_unlocked(generation):
                    accepted = False
                else:
                    self._discovered[device.identifier] = device
                    payload = self._device_payloads_unlocked()
                    self.devices_changed.emit(payload)
                    accepted = True
            if not accepted:
                self._diag(
                    "ble.scan",
                    "stale_advertisement_discarded",
                    identifier=device.identifier,
                    generation=generation,
                )
                return
            self._diag(
                "ble.scan",
                "advertisement_observed",
                identifier=device.identifier,
                name=device.name,
                rssi=device.rssi,
                service_uuids=device.service_uuids,
                generation=generation,
            )

        try:
            devices = await scanner.scan(self.scan_duration, on_advertisement)
        except asyncio.CancelledError:
            self._diag(
                "ble.scan",
                "scan_cancelled",
                simulated=self.simulate,
                generation=generation,
            )
            raise
        except BaseException as exc:
            self._diag(
                "ble.scan",
                "scan_failed",
                simulated=self.simulate,
                error=exc,
                generation=generation,
            )
            raise
        with self._scan_state_lock:
            if not self._scan_update_is_current_unlocked(generation):
                accepted = False
                device_count = len(devices)
            else:
                for device in devices:
                    # Assignment updates data without moving an existing key;
                    # identifiers seen only in the final batch append.
                    self._discovered[device.identifier] = device
                payload = self._device_payloads_unlocked()
                self.devices_changed.emit(payload)
                accepted = True
                device_count = len(self._discovered)
        if not accepted:
            self._diag(
                "ble.scan",
                "stale_final_batch_discarded",
                simulated=self.simulate,
                generation=generation,
                device_count=device_count,
            )
            return
        self._diag(
            "ble.scan",
            "scan_finished",
            simulated=self.simulate,
            generation=generation,
            device_count=device_count,
        )

    async def _connect(self, identifier: str, credential: Credential) -> None:
        self._connection_intent_generation += 1
        intent_generation = self._connection_intent_generation
        await self._cancel_reconnect()
        # A manual connection supersedes retained recovery intent immediately,
        # but does not gain permission to overlap unfinished native work from
        # the prior client.
        self._desired_identifier = None
        self._desired_credential = None
        existing_session = self._session
        if existing_session is not None:
            self._diag(
                "controller.session",
                "existing_session_disconnect_started",
            )
            await existing_session.disconnect()
            released = await self._wait_for_manual_connect_release(
                existing_session,
                intent_generation,
            )
            if not released:
                raise asyncio.CancelledError
        self._desired_identifier = identifier
        self._desired_credential = credential
        transport = self._create_transport()
        self._diag(
            "controller.session",
            "session_created",
            identifier=identifier,
            transport_type="simulator" if self.simulate else "bleak",
        )
        session = self._create_session(transport)
        session.subscribe(
            lambda event, source=session: self._on_session_event(source, event)
        )
        self._session = session
        try:
            await session.connect(DeviceTarget(identifier), credential)
            if intent_generation != self._connection_intent_generation:
                await session.disconnect()
                raise asyncio.CancelledError
            session.start_periodic_telemetry(
                self.telemetry_poll_interval,
                config_interval=self.config_poll_interval,
            )
        except BaseException:
            if intent_generation == self._connection_intent_generation:
                self._desired_identifier = None
                self._desired_credential = None
            raise
        finally:
            self._emit_snapshot()
            self._emit_gatt()

    async def _wait_for_manual_connect_release(
        self,
        session: ChargerSession,
        intent_generation: int,
    ) -> bool:
        waiting_reported = False
        while (
            not self._closed
            and intent_generation == self._connection_intent_generation
        ):
            if await session.wait_until_reconnectable(timeout=0.25):
                return True
            if not waiting_reported:
                waiting_reported = True
                self._set_operation(
                    True,
                    "connect",
                    "Ожидание завершения предыдущего BLE-клиента…",
                )
                self._diag(
                    "controller.session",
                    "manual_connect_waiting_for_old_client",
                    intent_generation=intent_generation,
                    replayed_mutation=False,
                )
        return False

    async def _disconnect(self, *, manual: bool = False) -> None:
        if manual:
            # Clear intent before the first await so no delayed maintenance
            # callback can re-arm recovery during an explicit Disconnect or
            # application shutdown.
            self._connection_intent_generation += 1
            self._desired_identifier = None
            self._desired_credential = None
            await self._cancel_reconnect()
        session = self._session
        if session is not None:
            await session.disconnect()
            self._emit_snapshot()
        else:
            self._diag(
                "controller.session",
                "disconnect_no_session",
            )

    async def _refresh(self) -> None:
        session = self._require_session()
        await session.refresh_config()
        await session.refresh_telemetry()

    async def _set_voltage(self, volts: float) -> None:
        await self._require_session().set_voltage(volts, operator_confirmed=True)

    async def _set_current(self, amps: float) -> None:
        await self._require_session().set_current(amps, operator_confirmed=True)

    async def _start_output(
        self,
        confirmed_volts: float,
        confirmed_amps: float,
    ) -> None:
        await self._require_session().start(confirmed_volts, confirmed_amps)

    async def _stop_output(self) -> None:
        session = self._require_session()
        block_reason = self._stop_block_reason(session)
        if block_reason is not None:
            raise SafetyInterlockError(block_reason)
        try:
            await session.stop()
        finally:
            if (
                session.state == SessionState.READY
                and session.authenticated
                and session.transport.connected
            ):
                session.start_periodic_telemetry(
                    self.telemetry_poll_interval,
                    config_interval=self.config_poll_interval,
                )

    def _stop_block_reason(self, session: ChargerSession | None) -> str | None:
        """Return why Stop must not be queued from the controller.

        This deliberately depends only on the explicit output state, not on
        configuration or the atomic Start checks.  The session repeats its own
        check immediately before the BLE write so a stale UI snapshot cannot
        authorize a command.
        """

        if not self.output_controls_enabled:
            return "Stop заблокирован: режим управления не включён"
        if session is None:
            return "Stop заблокирован: нет активной сессии"
        if (
            not session.transport.connected
            or not session.authenticated
        ):
            return "Stop заблокирован: нет подтверждённой сессии"
        if not session.telemetry_fresh or session.telemetry is None:
            return "Stop заблокирован: телеметрия устарела или отсутствует"
        if session.telemetry.get("output_enabled") is not True:
            return "Stop заблокирован: выход не подтверждён как включённый"
        return None

    def _create_transport(self) -> Any:
        factory = self._transport_factory
        if factory is not None:
            return factory()
        return FakeTransport() if self.simulate else BleakTransport()

    def _create_session(self, transport: Any) -> ChargerSession:
        return ChargerSession(
            transport,
            profile=PIDZOOM_HW178P,
            access=(
                AccessMode.CONTROL
                if self.output_controls_enabled
                else AccessMode.MONITOR_ONLY
            ),
            options=SessionOptions(
                request_timeout=self.request_timeout,
                native_write_timeout=self.native_write_timeout,
                freshness_seconds=max(15.0, self.config_poll_interval * 1.5),
                notification_settle_delay=0.0 if self.simulate else 1.0,
            ),
            diagnostics=self._diagnostics,
        )

    async def _cancel_reconnect(self) -> None:
        task = self._reconnect_task
        self._reconnect_task = None
        if task is None or task is asyncio.current_task() or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _clear_connection_secret_guard(self) -> None:
        guard = self._connection_secret_guard
        self._connection_secret_guard = None
        if guard is not None:
            guard.close()

    def _require_session(self) -> ChargerSession:
        session = self._session
        if session is None:
            raise SafetyInterlockError("нет активного сеанса зарядника")
        return session

    def _on_session_event(
        self,
        source_session: ChargerSession,
        event: SessionEvent,
    ) -> None:
        if source_session is not self._session:
            self._diag(
                "controller.session",
                "stale_session_event_discarded",
                kind=event.kind.value,
                message=event.message,
            )
            return
        self._diag(
            "controller.session",
            "session_event_received",
            kind=event.kind.value,
            opcode=event.details.get("opcode"),
            message=event.message,
            details=event.details,
        )
        if event.kind in {EventKind.TX, EventKind.RX}:
            self._emit_packet(event)
        self._emit_snapshot()
        if self._should_reconnect_after(source_session, event):
            self._schedule_read_only_reconnect(source_session, event.message)

    def _should_reconnect_after(
        self,
        source_session: ChargerSession,
        event: SessionEvent,
    ) -> bool:
        if self._closed or source_session is not self._session:
            return False
        if self._desired_identifier is None or self._desired_credential is None:
            return False
        task = self._reconnect_task
        if task is not None and not task.done():
            return False
        if event.kind != EventKind.ERROR:
            return False
        operation = event.details.get("operation")
        if operation in {"set_voltage", "set_current", "start", "stop"}:
            # The request may have reached the charger.  Never reconnect as an
            # implicit continuation and never replay a mutating operation.
            return False
        return bool(
            event.details.get("reconnect_required")
            and event.details.get("recoverable_read_only")
        )

    def _schedule_read_only_reconnect(
        self,
        source_session: ChargerSession,
        reason: str,
    ) -> None:
        intent_generation = self._connection_intent_generation
        task = asyncio.create_task(
            self._recover_read_only(source_session, intent_generation, reason),
            name="hwcdq-read-only-reconnect",
        )
        self._reconnect_task = task
        task.add_done_callback(self._finish_reconnect_task)

    def _finish_reconnect_task(self, task: asyncio.Task[None]) -> None:
        if self._reconnect_task is task:
            self._reconnect_task = None
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            return

    async def _recover_read_only(
        self,
        failed_session: ChargerSession,
        intent_generation: int,
        reason: str,
    ) -> None:
        self._diag(
            "controller.reconnect",
            "reconnect_scheduled",
            intent_generation=intent_generation,
            reason=reason,
            replayed_mutation=False,
        )
        ready = await self._wait_for_reconnectable_session(
            failed_session,
            intent_generation,
        )
        if not ready:
            return

        last_error = reason
        total = len(self.reconnect_delays)
        for attempt, delay in enumerate(self.reconnect_delays, start=1):
            if not self._reconnect_intent_is_current(intent_generation):
                return
            self._set_operation(
                True,
                "reconnect",
                f"Переподключение {attempt}/{total} через {delay:g} с…",
            )
            self._diag(
                "controller.reconnect",
                "reconnect_backoff_started",
                attempt=attempt,
                max_attempts=total,
                delay_seconds=delay,
                intent_generation=intent_generation,
            )
            await asyncio.sleep(delay)
            if not self._reconnect_intent_is_current(intent_generation):
                return

            identifier = self._desired_identifier
            credential = self._desired_credential
            assert identifier is not None and credential is not None
            candidate = self._create_session(self._create_transport())
            candidate.subscribe(
                lambda event, source=candidate: self._on_session_event(source, event)
            )
            self._session = candidate
            self._emit_snapshot()
            self._emit_gatt()
            try:
                # Read-only recovery sequence is deterministic inside connect:
                # auth -> firmware/serial -> config -> telemetry.
                await candidate.connect(DeviceTarget(identifier), credential)
            except asyncio.CancelledError:
                await candidate.disconnect()
                raise
            except BaseException as exc:
                last_error = str(exc).strip() or type(exc).__name__
                self._diag(
                    "controller.reconnect",
                    "reconnect_attempt_failed",
                    attempt=attempt,
                    max_attempts=total,
                    error=exc,
                    replayed_mutation=False,
                )
                ready = await self._wait_for_reconnectable_session(
                    candidate,
                    intent_generation,
                )
                if not ready:
                    return
                continue

            if not self._reconnect_intent_is_current(intent_generation):
                await candidate.disconnect()
                return
            candidate.start_periodic_telemetry(
                self.telemetry_poll_interval,
                config_interval=self.config_poll_interval,
            )
            self._set_operation(
                False,
                "reconnect",
                "Соединение восстановлено; конфигурация и телеметрия перечитаны",
                completed=True,
            )
            self._diag(
                "controller.reconnect",
                "reconnect_succeeded",
                attempt=attempt,
                max_attempts=total,
                replayed_mutation=False,
            )
            self._emit_snapshot()
            self._emit_gatt()
            return

        if self._reconnect_intent_is_current(intent_generation):
            self._desired_identifier = None
            self._desired_credential = None
            self._clear_connection_secret_guard()
            self._set_operation(
                False,
                "reconnect",
                f"Автоподключение не удалось после {total} попыток: {last_error}",
                completed=True,
            )
            self._diag(
                "controller.reconnect",
                "reconnect_exhausted",
                max_attempts=total,
                error=last_error,
                replayed_mutation=False,
            )
            self._emit_snapshot()
            self._emit_gatt()

    async def _wait_for_reconnectable_session(
        self,
        session: ChargerSession,
        intent_generation: int,
    ) -> bool:
        waiting_reported = False
        while self._reconnect_intent_is_current(intent_generation):
            if await session.wait_until_reconnectable(timeout=0.25):
                return True
            if not waiting_reported:
                waiting_reported = True
                self._set_operation(
                    True,
                    "reconnect",
                    "Ожидание завершения старого BLE-клиента…",
                )
                self._diag(
                    "controller.reconnect",
                    "reconnect_waiting_for_old_client",
                    intent_generation=intent_generation,
                    replayed_mutation=False,
                )
        return False

    def _reconnect_intent_is_current(self, intent_generation: int) -> bool:
        return (
            not self._closed
            and intent_generation == self._connection_intent_generation
            and self._desired_identifier is not None
            and self._desired_credential is not None
        )

    def _emit_packet(self, event: SessionEvent) -> None:
        opcode = int(event.details.get("opcode", -1))
        direction = "TX" if event.kind == EventKind.TX else "RX"
        semantic: dict[str, Any] = {
            "opcode": f"0x{opcode:02X}" if opcode >= 0 else "—",
            "command": self._OPCODE_SUMMARIES.get(opcode, "Неизвестный opcode"),
        }
        raw_hex = event.message
        if REDACTED not in raw_hex:
            try:
                decoded = codec.decode_packet(bytes.fromhex(raw_hex))
            except (ValueError, codec.ProtocolError):
                pass
            else:
                semantic.update(
                    {
                        key: value
                        for key, value in redact_value(decoded).items()
                        if key not in {"raw", "payload", "password"}
                    }
                )
        self.packet_logged.emit(
            {
                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "direction": direction,
                "opcode": semantic["opcode"],
                "summary": semantic["command"],
                "raw_hex": raw_hex,
                "decoded": semantic,
            }
        )

    def _emit_snapshot(self) -> None:
        session = self._session
        if session is None:
            payload = self._empty_snapshot()
        else:
            payload = asdict(session.snapshot)
            payload["state"] = session.state.value
            payload["config_fresh"] = session.config_fresh
            payload["telemetry_fresh"] = session.telemetry_fresh
            payload["ambiguous_outcomes"] = [
                asdict(item) for item in session.ambiguous_outcomes
            ]
        self.snapshot_changed.emit(payload)

    def _emit_gatt(self) -> None:
        session = self._session
        if session is None:
            self.gatt_changed.emit({"services": [], "selected": None, "error": None})
            return
        services = getattr(session, "services", ())
        selected = session.topology
        self.gatt_changed.emit(
            {
                "services": [self._to_plain(item) for item in services],
                "selected": self._to_plain(selected) if selected is not None else None,
                "error": session.last_error if selected is None else None,
            }
        )

    def _device_payloads(self) -> list[dict[str, Any]]:
        with self._scan_state_lock:
            return self._device_payloads_unlocked()

    def _device_payloads_unlocked(self) -> list[dict[str, Any]]:
        return [self._to_plain(item) for item in self._discovered.values()]

    def _scan_update_is_current_unlocked(self, generation: int) -> bool:
        return (
            generation == self._scan_generation
            and generation not in self._cancelled_scan_generations
        )

    def _scan_completion_is_current(self, generation: int) -> bool:
        with self._scan_state_lock:
            # A cancelled scan may publish its own cancellation completion so
            # the UI leaves its busy state.  Once a newer scan starts, that
            # older completion must not finalize the new scan.
            return generation == self._scan_generation

    def _finish_future(
        self,
        future: Future[Any],
        name: str,
        success_message: str,
        *,
        finalizer: Callable[[], None] | None = None,
        completion_guard: Callable[[], bool] | None = None,
    ) -> None:
        with self._operation_lock:
            self._active_futures[future] = name
        self._diag(
            "controller.operation",
            "operation_registered",
            operation=name,
        )

        def on_done(completed: Future[Any]) -> None:
            if completed.cancelled():
                message = "Операция отменена"
                outcome = "cancelled"
                error: BaseException | None = None
            else:
                try:
                    completed.result()
                except BaseException as exc:
                    detail = str(exc).strip() or type(exc).__name__
                    message = f"Ошибка: {detail}"
                    outcome = "failed"
                    error = exc
                else:
                    message = success_message
                    outcome = "succeeded"
                    error = None
            with self._operation_lock:
                self._active_futures.pop(completed, None)
                still_busy = bool(self._active_futures)
            try:
                if completion_guard is not None and not completion_guard():
                    self._diag(
                        "controller.operation",
                        "stale_operation_completion_discarded",
                        operation=name,
                        outcome=outcome,
                    )
                    return
                self._diag(
                    "controller.operation",
                    "operation_finished",
                    operation=name,
                    outcome=outcome,
                    error=error,
                    still_busy=still_busy,
                )
                self._set_operation(
                    still_busy,
                    name,
                    message,
                    completed=True,
                )
                self._emit_snapshot()
                self._emit_gatt()
            finally:
                if finalizer is not None:
                    try:
                        finalizer()
                    except BaseException:
                        # Secret registration teardown and diagnostics must not
                        # change controller completion semantics.
                        pass

        future.add_done_callback(on_done)

    def _set_operation(
        self,
        busy: bool,
        name: str,
        message: str,
        *,
        completed: bool = False,
    ) -> None:
        self._diag(
            "controller.operation",
            "operation_state_changed",
            busy=busy,
            operation=name,
            message=message,
            completed=completed,
        )
        self.operation_changed.emit(
            {
                "busy": busy,
                "name": name,
                "message": message,
                "completed": completed,
            }
        )

    def _emit_local_error(self, message: str) -> None:
        with self._operation_lock:
            busy = bool(self._active_futures)
        self.operation_changed.emit(
            {"busy": busy, "name": "validation", "message": message}
        )
        self._diag(
            "controller.validation",
            "action_rejected_locally",
            busy=busy,
            message=message,
        )

    def _diag(self, category: str, event: str, /, **details: Any) -> None:
        logger = self._diagnostics
        if logger is None:
            return
        try:
            logger.emit(category, event, **details)
        except BaseException:
            # Diagnostics are observational and must never alter controls.
            return

    def _empty_snapshot(self) -> dict[str, Any]:
        return {
            "state": SessionState.DISCONNECTED.value,
            "transport_connected": False,
            "authenticated": False,
            "control_outcome_unknown": False,
            "output_controls_enabled": self.output_controls_enabled,
            "firmware": None,
            "serial_number": None,
            "config": None,
            "telemetry": None,
            "last_error": None,
            "config_fresh": False,
            "telemetry_fresh": False,
            "ambiguous_outcomes": [],
        }

    @staticmethod
    def _to_plain(value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            return {key: AppController._to_plain(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {key: AppController._to_plain(item) for key, item in value.items()}
        if isinstance(value, (tuple, list, set, frozenset)):
            return [AppController._to_plain(item) for item in value]
        return value

__all__ = ["AppController", "AsyncioWorker"]
