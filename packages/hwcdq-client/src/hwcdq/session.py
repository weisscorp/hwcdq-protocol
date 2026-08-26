"""Safe, serialized HWCDQ application session over an abstract BLE transport."""

from __future__ import annotations

import asyncio
import math
import numbers
import struct
import time
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from typing import Any, overload

from .diagnostics import DiagnosticLogger
from .errors import (
    AmbiguousCommandResultError,
    AuthenticationError,
    BackendError,
    CommandRejectedError,
    CommandTimeoutError,
    FrameStreamError,
    InvalidStateError,
    SafetyInterlockError,
    TransportDisconnectedError,
    UnexpectedResponseError,
)
from .framing import FrameAssembler
from .gatt import chunks_for_write, select_hwcdq_topology
from .models import (
    AmbiguousOutcome,
    EventKind,
    GattService,
    SelectedGattTopology,
    SessionEvent,
    SessionSnapshot,
    SessionState,
)
from . import protocol as codec
from .profile import (
    AccessMode,
    ChargerProfile,
    Credential,
    DeviceTarget,
    PIDZOOM_HW178P,
    SessionOptions,
)
from .redaction import format_packet, redact_text
from .serialization import PrioritySerializer
from .transport import AsyncGattTransport


EventListener = Callable[[SessionEvent], None]
_ARG_UNSET = object()


class ChargerSession:
    """One charger connection with explicit safety and uncertainty gates.

    The object and its transport must remain on one asyncio event loop.  It
    never retries application writes: a timeout after any mutating write makes
    the outcome explicitly ambiguous until a matching readback or reconnect.
    """

    @overload
    def __init__(
        self,
        transport: AsyncGattTransport,
        *,
        profile: ChargerProfile = PIDZOOM_HW178P,
        access: AccessMode = AccessMode.MONITOR_ONLY,
        options: SessionOptions = SessionOptions(),
        diagnostics: DiagnosticLogger | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        transport: AsyncGattTransport,
        *,
        output_controls_enabled: bool = False,
        request_timeout: float = 8.0,
        native_write_timeout: float | None = None,
        freshness_seconds: float = 10.0,
        notification_settle_delay: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        diagnostics: DiagnosticLogger | None = None,
    ) -> None: ...

    def __init__(
        self,
        transport: AsyncGattTransport,
        *,
        profile: ChargerProfile | object = _ARG_UNSET,
        access: AccessMode | object = _ARG_UNSET,
        options: SessionOptions | object = _ARG_UNSET,
        diagnostics: DiagnosticLogger | None = None,
        output_controls_enabled: bool | object = _ARG_UNSET,
        request_timeout: float | object = _ARG_UNSET,
        native_write_timeout: float | None | object = _ARG_UNSET,
        freshness_seconds: float | object = _ARG_UNSET,
        notification_settle_delay: float | object = _ARG_UNSET,
        clock: Callable[[], float] | object = _ARG_UNSET,
    ) -> None:
        legacy_arguments = (
            output_controls_enabled,
            request_timeout,
            native_write_timeout,
            freshness_seconds,
            notification_settle_delay,
            clock,
        )
        typed_arguments = (profile, access, options)
        legacy_supplied = any(value is not _ARG_UNSET for value in legacy_arguments)
        typed_supplied = any(value is not _ARG_UNSET for value in typed_arguments)
        if legacy_supplied and typed_supplied:
            raise TypeError(
                "profile/access/options cannot be combined with deprecated "
                "session option keywords"
            )

        if legacy_supplied:
            profile = PIDZOOM_HW178P
            access = (
                AccessMode.CONTROL
                if bool(
                    False
                    if output_controls_enabled is _ARG_UNSET
                    else output_controls_enabled
                )
                else AccessMode.MONITOR_ONLY
            )
            options = SessionOptions(
                request_timeout=(
                    8.0 if request_timeout is _ARG_UNSET else request_timeout
                ),
                native_write_timeout=(
                    None
                    if native_write_timeout is _ARG_UNSET
                    else native_write_timeout
                ),
                freshness_seconds=(
                    10.0 if freshness_seconds is _ARG_UNSET else freshness_seconds
                ),
                notification_settle_delay=(
                    1.0
                    if notification_settle_delay is _ARG_UNSET
                    else notification_settle_delay
                ),
                clock=time.monotonic if clock is _ARG_UNSET else clock,
            )
        else:
            profile = PIDZOOM_HW178P if profile is _ARG_UNSET else profile
            access = AccessMode.MONITOR_ONLY if access is _ARG_UNSET else access
            options = SessionOptions() if options is _ARG_UNSET else options

        if not isinstance(profile, ChargerProfile):
            raise TypeError("profile must be a ChargerProfile")
        if not isinstance(access, AccessMode):
            raise TypeError("access must be an AccessMode")
        if not isinstance(options, SessionOptions):
            raise TypeError("options must be a SessionOptions")
        self.transport = transport
        self.profile = profile
        self.access = access
        self.options = options
        self.output_controls_enabled = access is AccessMode.CONTROL
        self.request_timeout = options.request_timeout
        self.native_write_timeout = float(
            options.request_timeout
            if options.native_write_timeout is None
            else options.native_write_timeout
        )
        self.freshness_seconds = options.freshness_seconds
        self.notification_settle_delay = options.notification_settle_delay
        self._clock = options.clock
        self._diagnostics = diagnostics

        self.state = SessionState.DISCONNECTED
        self.services: tuple[GattService, ...] = ()
        self.topology: SelectedGattTopology | None = None
        self.authenticated = False
        self.firmware: bytes | None = None
        self.serial_number: bytes | None = None
        self.config: dict[str, Any] | None = None
        self.telemetry: dict[str, Any] | None = None
        self.last_error: str | None = None

        self._config_at: float | None = None
        self._telemetry_at: float | None = None
        self._last_exchange_completed_at: float | None = None
        self._last_config_exchange_completed_at: float | None = None
        self._ambiguities: list[AmbiguousOutcome] = []
        self._listeners: list[EventListener] = []
        self._assembler = FrameAssembler()
        self._serializer = PrioritySerializer()
        self._pending_opcode: int | None = None
        self._pending_response: asyncio.Future[dict[str, Any]] | None = None
        self._expected_disconnect = False
        self._connection_generation = 0
        self._connect_task: asyncio.Task[Any] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._polling_enabled = False
        self._client_poisoned = False
        self._poison_reason: str | None = None
        self._poison_teardown_task: asyncio.Task[None] | None = None
        self._orphaned_write_tasks: set[asyncio.Task[None]] = set()
        self._transaction_sequence = 0
        self._active_transaction_id: str | None = None
        self._active_transaction_operation: str | None = None

    @property
    def control_outcome_unknown(self) -> bool:
        return bool(self._ambiguities)

    @property
    def client_poisoned(self) -> bool:
        """Whether the current native client must never receive another write."""

        return self._client_poisoned

    @property
    def ambiguous_outcomes(self) -> tuple[AmbiguousOutcome, ...]:
        return tuple(self._ambiguities)

    @property
    def config_fresh(self) -> bool:
        return self._is_fresh(self._config_at)

    @property
    def telemetry_fresh(self) -> bool:
        return self._is_fresh(self._telemetry_at)

    @property
    def config_age_s(self) -> float | None:
        return self._age(self._config_at)

    @property
    def telemetry_age_s(self) -> float | None:
        return self._age(self._telemetry_at)

    @property
    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            state=self.state,
            transport_connected=self.transport.connected,
            authenticated=self.authenticated,
            control_outcome_unknown=self.control_outcome_unknown,
            ambiguous_outcomes=self.ambiguous_outcomes,
            output_controls_enabled=self.output_controls_enabled,
            config_fresh=self.config_fresh,
            telemetry_fresh=self.telemetry_fresh,
            config_age_s=self.config_age_s,
            telemetry_age_s=self.telemetry_age_s,
            services=self.services,
            topology=self.topology,
            firmware=self.firmware,
            serial_number=self.serial_number,
            config=None if self.config is None else dict(self.config),
            telemetry=None if self.telemetry is None else dict(self.telemetry),
            last_error=self.last_error,
        )

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    @overload
    async def connect(
        self,
        target: DeviceTarget,
        credential: Credential,
    ) -> SessionSnapshot: ...

    @overload
    async def connect(
        self,
        identifier: str,
        password: str,
        *,
        _credential: str | None = None,
    ) -> SessionSnapshot: ...

    async def connect(
        self,
        target: DeviceTarget | str | object = _ARG_UNSET,
        credential: Credential | str | object = _ARG_UNSET,
        *,
        identifier: str | object = _ARG_UNSET,
        password: str | object = _ARG_UNSET,
        _credential: str | None = None,
    ) -> SessionSnapshot:
        if target is not _ARG_UNSET and identifier is not _ARG_UNSET:
            raise TypeError("target and identifier are aliases; provide only one")
        if credential is not _ARG_UNSET and password is not _ARG_UNSET:
            raise TypeError("credential and password are aliases; provide only one")
        raw_target = identifier if target is _ARG_UNSET else target
        raw_credential = password if credential is _ARG_UNSET else credential
        if raw_target is _ARG_UNSET or raw_credential is _ARG_UNSET:
            raise TypeError("connect requires a target/credential or identifier/password")

        if self.state not in {SessionState.DISCONNECTED, SessionState.ERROR}:
            raise InvalidStateError(f"cannot connect while state is {self.state.value}")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("connect must run in an asyncio task")
        if self._connect_task is not None and not self._connect_task.done():
            raise InvalidStateError("another connection attempt is still finishing")
        # Invalidate all state from the previous peripheral before validating
        # this attempt.  A rejected target/credential can therefore never
        # leave stale configuration eligible for a later control command.
        self._connection_generation += 1
        generation = self._connection_generation
        self._reset_connection_data()
        legacy_plaintext: str | None = None
        if isinstance(raw_target, DeviceTarget) and isinstance(
            raw_credential, Credential
        ):
            if _credential is not None:
                raise TypeError("_credential is available only to the legacy string API")
            target = raw_target
            credential = raw_credential
        elif isinstance(raw_target, str) and isinstance(raw_credential, str):
            target = DeviceTarget(raw_target)
            legacy_plaintext = raw_credential
            if _credential is None:
                credential = Credential.from_password(raw_credential)
            else:
                if raw_credential:
                    raise ValueError(
                        "plaintext password and derived credential are exclusive"
                    )
                credential = Credential.from_digest(_credential)
        else:
            raise TypeError(
                "connect requires DeviceTarget/Credential or string identifier/password"
            )
        if self._client_poisoned:
            self._reap_finished_orphaned_writes()
            teardown = self._poison_teardown_task
            if (
                self.transport.connected
                or self._orphaned_write_tasks
                or (teardown is not None and not teardown.done())
            ):
                raise InvalidStateError(
                    "poisoned BLE client teardown or native write is incomplete; "
                    "create a clean connection before reconnecting"
                )
            self._client_poisoned = False
            self._poison_reason = None
            self._poison_teardown_task = None

        wire_credential = credential._wire_value()
        self._connect_task = task
        secret_stack = ExitStack()
        if self._diagnostics is not None:
            if legacy_plaintext is not None:
                secret_stack.enter_context(
                    self._diagnostics.register_secret(legacy_plaintext)
                )
            secret_stack.enter_context(
                self._diagnostics.register_secret(wire_credential)
            )

        try:
            self._diag(
                "ble.lifecycle",
                "connect_started",
                identifier=target.identifier,
                generation=generation,
            )

            self.last_error = None
            self._set_state(SessionState.CONNECTING)
            await self.transport.connect(
                target.identifier,
                lambda generation=generation: self._on_transport_disconnected(
                    generation
                ),
            )
            self._diag(
                "ble.lifecycle",
                "transport_connected",
                identifier=target.identifier,
                generation=generation,
            )
            self._ensure_connect_is_current(generation)
            if not self.transport.connected:
                raise TransportDisconnectedError("transport did not remain connected")

            self._set_state(SessionState.DISCOVERING)
            self.services = tuple(await self.transport.discover_gatt())
            self._ensure_connect_is_current(generation)
            self._diag(
                "ble.gatt",
                "services_discovered",
                generation=generation,
                services=[
                    {
                        "uuid": service.uuid,
                        "characteristics": [
                            {
                                "uuid": characteristic.uuid,
                                "properties": sorted(characteristic.properties),
                                "max_write_without_response_size": (
                                    characteristic.max_write_without_response_size
                                ),
                            }
                            for characteristic in service.characteristics
                        ],
                    }
                    for service in self.services
                ],
            )
            self.topology = select_hwcdq_topology(
                self.services,
                gatt_profile=self.profile.gatt,
            )
            self._diag(
                "ble.gatt",
                "topology_selected",
                generation=generation,
                service_uuid=self.topology.service_uuid,
                rx_uuid=self.topology.rx_uuid,
                tx_uuid=self.topology.tx_uuid,
                write_with_response=self.topology.write_with_response,
                wnr_chunk_size=self.topology.wnr_chunk_size,
                service_count=len(self.services),
            )
            self._emit(
                EventKind.DATA,
                "GATT-топология обнаружена",
                services=self.services,
                selected_topology=self.topology,
            )
            await self.transport.start_notify(
                self.topology.rx_uuid,
                lambda data, generation=generation: self._on_notification(
                    generation, data
                ),
            )
            self._diag(
                "ble.gatt",
                "notifications_enabled",
                characteristic_uuid=self.topology.rx_uuid,
                generation=generation,
            )
            self._ensure_connect_is_current(generation)
            # The recovered Android route waits one second after subscribing
            # before its password request.  Preserve that hardware-facing
            # settling interval; the deterministic simulator opts out.
            if self.notification_settle_delay:
                await asyncio.sleep(self.notification_settle_delay)
                self._ensure_connect_is_current(generation)

            self._set_state(SessionState.AUTHENTICATING)
            # Authentication is a one-shot state-changing request.  Never
            # retry it or switch credentials after an uncertain/negative
            # result: the caller must explicitly start another connection.
            auth = await self._request(
                codec.encode_check_password_credential(wire_credential)
            )
            self._ensure_connect_is_current(generation)
            auth_payload = auth.get("payload")
            exact_success = b"\x03\x02\x01\x03"
            if auth.get("raw") != exact_success:
                if auth_payload == b"\x00":
                    raise AuthenticationError("charger rejected the password")
                raise UnexpectedResponseError(
                    "password response was not the exact success acknowledgement"
                )
            self.authenticated = True
            self._diag(
                "ble.authentication",
                "authentication_accepted",
                opcode=codec.OP_CHECK_PASSWORD,
            )

            self._set_state(SessionState.LOADING)
            firmware = await self._request(codec.encode_get_firmware())
            self._ensure_connect_is_current(generation)
            serial = await self._request(codec.encode_get_serial())
            self._ensure_connect_is_current(generation)
            self.firmware = bytes(firmware["payload"])
            self.serial_number = bytes(serial["payload"])
            await self.refresh_config(_allow_loading=True)
            self._ensure_connect_is_current(generation)
            await self.refresh_telemetry(_allow_loading=True)
            self._ensure_connect_is_current(generation)
            self._set_state(SessionState.READY)
            self._emit(EventKind.INFO, "Сеанс HWCDQ готов")
            self._diag(
                "ble.lifecycle",
                "session_ready",
                generation=generation,
                authenticated=True,
            )
            return self.snapshot
        except asyncio.CancelledError:
            # A Disconnect request invalidates and cancels setup.  Some
            # transports may suppress cancellation until their native connect
            # call returns, so the generation checks above remain necessary.
            await self._cleanup_failed_connection()
            self._reset_connection_data()
            if self.transport.connected:
                self.last_error = (
                    "native BLE link remained connected after setup cancellation"
                )
                self._set_state(SessionState.ERROR)
                self._emit(EventKind.ERROR, self.last_error)
            else:
                self.last_error = None
                self._set_state(SessionState.DISCONNECTED)
                self._emit(EventKind.INFO, "Подключение отменено")
            self._diag(
                "ble.lifecycle",
                "connect_cancelled",
                generation=generation,
                transport_connected=self.transport.connected,
            )
            raise
        except BaseException as exc:
            # The derived credential is used only to scrub this boundary and
            # is not retained as session state.
            original_error = str(exc)
            secrets = [
                wire_credential,
                wire_credential.lower(),
                wire_credential.upper(),
            ]
            if legacy_plaintext is not None:
                secrets.append(legacy_plaintext)
            self.last_error = redact_text(
                original_error,
                secrets,
            )
            await self._cleanup_failed_connection()
            self._reset_connection_data()
            self._set_state(SessionState.ERROR)
            self._emit(EventKind.ERROR, self.last_error or type(exc).__name__)
            self._diag(
                "ble.lifecycle",
                "connect_failed",
                generation=generation,
                error=self.last_error or type(exc).__name__,
                transport_connected=self.transport.connected,
            )
            if self.last_error != original_error:
                raise BackendError(self.last_error) from None
            raise
        finally:
            if self._connect_task is task:
                self._connect_task = None
            secret_stack.close()
            legacy_plaintext = ""
            wire_credential = ""

    async def disconnect(self) -> None:
        connect_task = self._connect_task
        connect_in_flight = (
            connect_task is not None
            and connect_task is not asyncio.current_task()
            and not connect_task.done()
        )
        if (
            self.state == SessionState.DISCONNECTED
            and not connect_in_flight
            and not self.transport.connected
        ):
            self._connection_generation += 1
            self._reset_connection_data()
            return
        # Invalidate setup before yielding.  The connect path checks this even
        # when a platform transport delays or suppresses task cancellation.
        self._connection_generation += 1
        self._diag(
            "ble.lifecycle",
            "disconnect_started",
            generation=self._connection_generation,
            transport_connected=self.transport.connected,
            client_poisoned=self._client_poisoned,
        )
        self._set_state(SessionState.DISCONNECTING)
        await self.stop_periodic_telemetry()
        poison_teardown = self._poison_teardown_task
        if (
            self._client_poisoned
            and poison_teardown is not None
            and not poison_teardown.done()
        ):
            # Never race an explicit disconnect/stop-notify with the
            # best-effort poisoned-client teardown already in flight.
            await asyncio.shield(poison_teardown)
        if connect_in_flight:
            assert connect_task is not None
            connect_task.cancel()
            await asyncio.gather(connect_task, return_exceptions=True)
        poison_teardown = self._poison_teardown_task
        if (
            self._client_poisoned
            and poison_teardown is not None
            and not poison_teardown.done()
        ):
            await asyncio.shield(poison_teardown)
        self._expected_disconnect = True
        topology = self.topology
        try:
            if (
                not self._client_poisoned
                and topology is not None
                and self.transport.connected
            ):
                try:
                    await self.transport.stop_notify(topology.rx_uuid)
                except Exception as exc:
                    self._emit(EventKind.WARNING, f"Не удалось отключить уведомления: {exc}")
            if self.transport.connected:
                await self.transport.disconnect()
        finally:
            self._expected_disconnect = False
            self._fail_pending(TransportDisconnectedError("session disconnected"))
            self._assembler.reset()
            self._reset_connection_data()
            if self.transport.connected:
                self.last_error = "native BLE link remained connected after disconnect"
                self._set_state(SessionState.ERROR)
                self._emit(EventKind.ERROR, self.last_error)
            else:
                self._set_state(SessionState.DISCONNECTED)
            self._diag(
                "ble.lifecycle",
                "disconnect_finished",
                transport_connected=self.transport.connected,
                state=self.state.value,
            )

    async def wait_until_reconnectable(self, timeout: float = 5.0) -> bool:
        """Wait until poisoned native work can no longer overlap a new client."""

        if timeout <= 0:
            raise ValueError("timeout must be positive")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        teardown = self._poison_teardown_task
        if teardown is not None and not teardown.done():
            remaining = max(0.0, deadline - loop.time())
            try:
                await asyncio.wait_for(asyncio.shield(teardown), timeout=remaining)
            except TimeoutError:
                return False
        while loop.time() < deadline:
            self._reap_finished_orphaned_writes()
            if not self.transport.connected and not self._orphaned_write_tasks:
                return True
            await asyncio.sleep(0.01)
        self._reap_finished_orphaned_writes()
        return not self.transport.connected and not self._orphaned_write_tasks

    def _ensure_connect_is_current(self, generation: int) -> None:
        if generation != self._connection_generation:
            raise asyncio.CancelledError

    async def refresh_config(
        self,
        *,
        _allow_loading: bool = False,
        _priority: int = 10,
    ) -> dict[str, Any]:
        self._require_readable(_allow_loading=_allow_loading)
        async with self._transaction(
            "refresh_config", _priority, opcode=codec.OP_GET_CONFIG
        ):
            self._require_application_writes_allowed()
            self._require_readable(_allow_loading=_allow_loading)
            return await self._refresh_config_locked()

    async def refresh_telemetry(
        self,
        *,
        _allow_loading: bool = False,
        _priority: int = 10,
    ) -> dict[str, Any]:
        self._require_readable(_allow_loading=_allow_loading)
        async with self._transaction(
            "refresh_telemetry", _priority, opcode=codec.OP_GET_TELEMETRY
        ):
            self._require_application_writes_allowed()
            self._require_readable(_allow_loading=_allow_loading)
            return await self._refresh_telemetry_locked()

    async def refresh(self) -> SessionSnapshot:
        """Refresh configuration and telemetry, then return one snapshot."""

        await self.refresh_config()
        await self.refresh_telemetry()
        return self.snapshot

    async def set_voltage(
        self,
        volts: float,
        *,
        operator_confirmed: bool = False,
    ) -> dict[str, Any]:
        value = self._valid_positive_number(volts, "voltage")
        self._require_control_context(require_confirmation=operator_confirmed)
        self._validate_voltage_limit(value)
        async with self._transaction(
            "set_voltage", 10, opcode=codec.OP_SET_VOLTAGE
        ):
            self._require_application_writes_allowed()
            value = self._valid_positive_number(volts, "voltage")
            self._require_control_context(require_confirmation=operator_confirmed)
            self._validate_voltage_limit(value)
            expectation = AmbiguousOutcome("set_voltage", value, "awaiting response")
            response = await self._request_locked(
                codec.encode_set_voltage(value),
                mutating=True,
                expectation=expectation,
            )
            self._mark_ambiguous(
                AmbiguousOutcome("set_voltage", value, "awaiting configuration readback")
            )
            try:
                config = await self._refresh_config_locked()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise AmbiguousCommandResultError(
                    "voltage acknowledgement arrived, but readback did not complete"
                ) from exc
            applied = self._numbers_match(config.get("target_voltage"), value)
            self._evaluate_mutation_response(response, expectation, applied=applied)
            self._resolve_ambiguity("set_voltage")
            return config

    async def set_current(
        self,
        amps: float,
        *,
        operator_confirmed: bool = False,
    ) -> dict[str, Any]:
        value = self._valid_positive_number(amps, "current")
        self._require_control_context(require_confirmation=operator_confirmed)
        self._validate_current_limit(value)
        async with self._transaction(
            "set_current", 10, opcode=codec.OP_SET_CURRENT
        ):
            self._require_application_writes_allowed()
            value = self._valid_positive_number(amps, "current")
            self._require_control_context(require_confirmation=operator_confirmed)
            self._validate_current_limit(value)
            expectation = AmbiguousOutcome("set_current", value, "awaiting response")
            response = await self._request_locked(
                codec.encode_set_current(value),
                mutating=True,
                expectation=expectation,
            )
            self._mark_ambiguous(
                AmbiguousOutcome("set_current", value, "awaiting configuration readback")
            )
            try:
                config = await self._refresh_config_locked()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise AmbiguousCommandResultError(
                    "current acknowledgement arrived, but readback did not complete"
                ) from exc
            applied = self._numbers_match(config.get("target_current"), value)
            self._evaluate_mutation_response(response, expectation, applied=applied)
            self._resolve_ambiguity("set_current")
            return config

    async def start(
        self,
        confirmed_volts: float,
        confirmed_amps: float,
    ) -> dict[str, Any]:
        confirmed_voltage = self._canonical_float32(
            confirmed_volts,
            "confirmed voltage",
        )
        confirmed_current = self._canonical_float32(
            confirmed_amps,
            "confirmed current",
        )
        self._precheck_start_context()
        self._validate_voltage_limit(confirmed_voltage[0])
        self._validate_current_limit(confirmed_current[0])
        expectation = AmbiguousOutcome("output", True, "awaiting response")
        async with self._transaction(
            "start", 10, opcode=codec.OP_OUTPUT_CONTROL
        ):
            self._require_application_writes_allowed()
            self._precheck_start_context()
            await self._refresh_config_locked()
            await self._refresh_telemetry_locked()
            self._validate_atomic_start_context(
                confirmed_voltage,
                confirmed_current,
            )
            response = await self._request_locked(
                codec.encode_start(),
                mutating=True,
                expectation=expectation,
            )
            self._mark_ambiguous(
                AmbiguousOutcome("output", True, "awaiting telemetry readback")
            )
            try:
                telemetry = await self._refresh_telemetry_locked()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise AmbiguousCommandResultError(
                    "start acknowledgement arrived, but output readback did not complete"
                ) from exc
            applied = telemetry.get("output_enabled") is True
            self._evaluate_mutation_response(response, expectation, applied=applied)
            self._resolve_ambiguity("output", expected_value=True)
            return telemetry

    async def start_output(
        self,
        confirmed_volts: float,
        confirmed_amps: float,
    ) -> dict[str, Any]:
        """Public descriptive alias for :meth:`start`."""

        return await self.start(confirmed_volts, confirmed_amps)

    async def stop(self) -> dict[str, Any]:
        """Prioritized de-energizing command with an explicit-state interlock."""

        self._require_application_writes_allowed()
        self._validate_stop_context()
        await self.stop_periodic_telemetry()
        expectation = AmbiguousOutcome("output", False, "awaiting response")
        async with self._transaction(
            "stop", 0, opcode=codec.OP_OUTPUT_CONTROL
        ):
            # A Stop queued behind a write that subsequently poisoned the
            # client must report that it was not transmitted.  It must never
            # become a second write overlapping the orphaned native call.
            self._require_application_writes_allowed()
            # Recheck after acquiring the serialized transaction slot.  A
            # queued read may have established that the output is already OFF,
            # or the telemetry may have expired while Stop was waiting.
            self._validate_stop_context()
            response = await self._request_locked(
                codec.encode_stop(),
                mutating=True,
                expectation=expectation,
            )
            self._mark_ambiguous(
                AmbiguousOutcome("output", False, "awaiting telemetry readback")
            )
            try:
                telemetry = await self._refresh_telemetry_locked()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise AmbiguousCommandResultError(
                    "stop acknowledgement arrived, but output readback did not complete"
                ) from exc
            applied = telemetry.get("output_enabled") is False
            self._evaluate_mutation_response(response, expectation, applied=applied)
            self._resolve_ambiguity("output", expected_value=False)
            return telemetry

    async def stop_output(self) -> dict[str, Any]:
        """Public descriptive alias for :meth:`stop`."""

        return await self.stop()

    def start_periodic_telemetry(
        self,
        interval: float = 5.0,
        *,
        config_interval: float = 60.0,
    ) -> None:
        if interval <= 0:
            raise ValueError("polling interval must be positive")
        if config_interval <= 0:
            raise ValueError("configuration polling interval must be positive")
        self._require_readable()
        self._polling_enabled = True
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(
                self._poll_maintenance(float(interval), float(config_interval)),
                name="hwcdq-maintenance-poll",
            )

    async def stop_periodic_telemetry(self) -> None:
        self._polling_enabled = False
        task = self._poll_task
        self._poll_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _poll_maintenance(
        self,
        telemetry_interval: float,
        config_interval: float,
    ) -> None:
        """Maintain read-only state without overlapping operator commands.

        Every completed exchange starts a new quiet period.  When the slower
        configuration read is due it occupies one maintenance slot; telemetry
        resumes after the next quiet period instead of creating a burst of two
        adjacent BLE writes.
        """

        loop = asyncio.get_running_loop()
        try:
            while self._polling_enabled:
                anchor = self._last_exchange_completed_at
                if anchor is None:
                    anchor = loop.time()
                    self._last_exchange_completed_at = anchor
                remaining = anchor + telemetry_interval - loop.time()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                if not self._polling_enabled:
                    break
                # Any manual/configuration/mutation exchange that completed
                # during the sleep owns a fresh full quiet period.
                if self._last_exchange_completed_at != anchor:
                    continue
                # Polling is observational and never queues behind an operator
                # command.  It simply skips busy intervals.
                if self._serializer.busy:
                    await asyncio.sleep(min(0.05, telemetry_interval))
                    continue
                try:
                    last_config = self._last_config_exchange_completed_at
                    config_due = (
                        last_config is None
                        or loop.time() - last_config >= config_interval
                    )
                    if config_due:
                        await self.refresh_config(_priority=50)
                    else:
                        await self.refresh_telemetry(_priority=50)
                except (BackendError, asyncio.TimeoutError) as exc:
                    self._emit(
                        EventKind.WARNING,
                        f"Остановлен фоновый опрос до переподключения: {exc}",
                        maintenance_failure=True,
                        reconnect_required=self._client_poisoned,
                    )
                    break
        except asyncio.CancelledError:
            raise

    async def _refresh_config_locked(self) -> dict[str, Any]:
        decoded = await self._request_locked(codec.encode_get_config())
        config = decoded.get("config")
        if not isinstance(config, dict):
            raise UnexpectedResponseError("configuration response had an unknown layout")
        # _on_notification already applied this data; return an independent
        # shallow copy so callers cannot mutate session state accidentally.
        return dict(config)

    async def _refresh_telemetry_locked(self) -> dict[str, Any]:
        decoded = await self._request_locked(codec.encode_get_telemetry())
        telemetry = decoded.get("telemetry")
        if not isinstance(telemetry, dict):
            raise UnexpectedResponseError("telemetry response had an unknown layout")
        return dict(telemetry)

    async def _request(
        self,
        packet: bytes,
        *,
        priority: int = 10,
    ) -> dict[str, Any]:
        request = codec.decode_packet(packet)
        opcode = int(request["opcode"])
        async with self._transaction(
            f"opcode_0x{opcode:02x}", priority, opcode=opcode
        ):
            self._require_application_writes_allowed()
            return await self._request_locked(packet)

    @asynccontextmanager
    async def _transaction(
        self,
        operation: str,
        priority: int,
        *,
        opcode: int,
    ) -> AsyncIterator[str]:
        """Acquire one serializer slot for one atomic logical operation."""

        loop = asyncio.get_running_loop()
        self._transaction_sequence += 1
        transaction_id = f"tx-{self._transaction_sequence:06d}"
        queued_at = loop.time()
        self._diag(
            "ble.serializer",
            "transaction_queued",
            opcode=opcode,
            transaction_id=transaction_id,
            operation=operation,
            priority=priority,
        )
        acquired = False
        try:
            await self._serializer.acquire(priority)
            acquired = True
        except asyncio.CancelledError:
            self._diag(
                "ble.serializer",
                "transaction_queue_cancelled",
                opcode=opcode,
                transaction_id=transaction_id,
                operation=operation,
                priority=priority,
            )
            raise
        except BaseException as exc:
            self._diag(
                "ble.serializer",
                "transaction_queue_failed",
                opcode=opcode,
                transaction_id=transaction_id,
                operation=operation,
                priority=priority,
                error=exc,
            )
            raise

        acquired_at = loop.time()
        self._active_transaction_id = transaction_id
        self._active_transaction_operation = operation
        self._diag(
            "ble.serializer",
            "transaction_acquired",
            opcode=opcode,
            transaction_id=transaction_id,
            operation=operation,
            priority=priority,
            queue_wait_ms=(acquired_at - queued_at) * 1000.0,
            response_timeout_seconds=self.request_timeout,
            native_write_timeout_seconds=self.native_write_timeout,
        )
        try:
            yield transaction_id
        except asyncio.CancelledError:
            self._diag(
                "ble.serializer",
                "transaction_cancelled",
                opcode=opcode,
                transaction_id=transaction_id,
                operation=operation,
            )
            raise
        except BaseException as exc:
            self._diag(
                "ble.serializer",
                "transaction_failed",
                opcode=opcode,
                transaction_id=transaction_id,
                operation=operation,
                error=exc,
            )
            raise
        else:
            self._diag(
                "ble.serializer",
                "transaction_completed",
                opcode=opcode,
                transaction_id=transaction_id,
                operation=operation,
            )
        finally:
            if acquired:
                self._active_transaction_id = None
                self._active_transaction_operation = None
                await self._serializer.release()
                released_at = loop.time()
                self._diag(
                    "ble.serializer",
                    "transaction_released",
                    opcode=opcode,
                    transaction_id=transaction_id,
                    operation=operation,
                    occupied_ms=(released_at - acquired_at) * 1000.0,
                )

    async def _request_locked(
        self,
        packet: bytes,
        *,
        mutating: bool = False,
        expectation: AmbiguousOutcome | None = None,
    ) -> dict[str, Any]:
        self._require_application_writes_allowed()
        topology = self.topology
        if topology is None or not self.transport.connected:
            raise TransportDisconnectedError("BLE transport is not connected")
        if self._pending_response is not None:
            raise RuntimeError("more than one application request was attempted")

        request = codec.decode_packet(packet)
        opcode = int(request["opcode"])
        loop = asyncio.get_running_loop()
        write_deadline = loop.time() + self.native_write_timeout
        transaction_id = self._active_transaction_id
        secret_packet = opcode == codec.OP_CHECK_PASSWORD
        self._diag(
            "ble.packet",
            "request_started",
            opcode=opcode,
            transaction_id=transaction_id,
            operation=self._active_transaction_operation,
            write_deadline_monotonic=write_deadline,
            response_timeout_seconds=self.request_timeout,
            frame=None if secret_packet else bytes(packet),
            checksum=None if secret_packet else packet[-1],
        )
        response_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_opcode = opcode
        self._pending_response = response_future
        attempted = False
        try:
            display = format_packet(packet)
            self._emit(
                EventKind.TX,
                display,
                opcode=opcode,
                display=display,
                raw=None if secret_packet else bytes(packet),
                redacted=secret_packet,
            )
            chunks = chunks_for_write(
                packet,
                write_with_response=topology.write_with_response,
                wnr_chunk_size=topology.wnr_chunk_size,
            )
            self._diag(
                "ble.packet",
                "tx_frame_prepared",
                opcode=opcode,
                transaction_id=transaction_id,
                characteristic_uuid=topology.tx_uuid,
                write_with_response=topology.write_with_response,
                chunk_count=len(chunks),
                frame=None if secret_packet else bytes(packet),
            )
            for chunk_index, chunk in enumerate(chunks, start=1):
                if write_deadline - asyncio.get_running_loop().time() <= 0:
                    message = (
                        f"native BLE write deadline expired before opcode 0x{opcode:02X} "
                        "write completed"
                    )
                    if attempted:
                        self._poison_current_client(message)
                    self._diag(
                        "ble.timeout",
                        "deadline_expired_between_chunks",
                        opcode=opcode,
                        transaction_id=transaction_id,
                        chunk_index=chunk_index,
                        chunk_count=len(chunks),
                        attempted=attempted,
                    )
                    raise CommandTimeoutError(message)
                attempted = True
                self._diag(
                    "ble.write",
                    "chunk_write_started",
                    opcode=opcode,
                    transaction_id=transaction_id,
                    characteristic_uuid=topology.tx_uuid,
                    write_with_response=topology.write_with_response,
                    chunk_index=chunk_index,
                    chunk_count=len(chunks),
                    chunk=None if secret_packet else chunk,
                )
                await self._write_chunk_until(
                    topology.tx_uuid,
                    chunk,
                    response=topology.write_with_response,
                    deadline=write_deadline,
                    opcode=opcode,
                )
                self._diag(
                    "ble.write",
                    "chunk_write_completed",
                    opcode=opcode,
                    transaction_id=transaction_id,
                    characteristic_uuid=topology.tx_uuid,
                    write_with_response=topology.write_with_response,
                    chunk_index=chunk_index,
                    chunk_count=len(chunks),
                )
            try:
                response_deadline = loop.time() + self.request_timeout
                self._diag(
                    "ble.packet",
                    "response_wait_started",
                    opcode=opcode,
                    transaction_id=transaction_id,
                    deadline_monotonic=response_deadline,
                    remaining_ms=self.request_timeout * 1000.0,
                )
                done, _ = await asyncio.wait(
                    {response_future}, timeout=self.request_timeout
                )
                if response_future not in done:
                    raise TimeoutError
                result = response_future.result()
                self._diag(
                    "ble.packet",
                    "response_matched",
                    opcode=opcode,
                    transaction_id=transaction_id,
                    acknowledged=result.get("acknowledged"),
                )
                completed_at = loop.time()
                self._last_exchange_completed_at = completed_at
                if opcode == codec.OP_GET_CONFIG:
                    self._last_config_exchange_completed_at = completed_at
                return result
            except TimeoutError as exc:
                self._diag(
                    "ble.timeout",
                    "response_deadline_expired",
                    opcode=opcode,
                    transaction_id=transaction_id,
                    attempted=attempted,
                )
                message = (
                    f"response deadline expired for opcode 0x{opcode:02X}; "
                    "notification stream desynchronized, command not replayed, "
                    "reconnect required"
                )
                self._poison_current_client(message)
                raise CommandTimeoutError(message) from exc
        except BaseException as exc:
            self._diag(
                "ble.packet",
                "request_failed",
                opcode=opcode,
                transaction_id=transaction_id,
                attempted=attempted,
                mutating=mutating,
                error=exc,
            )
            if mutating and attempted and expectation is not None:
                self._mark_ambiguous(
                    AmbiguousOutcome(
                        expectation.operation,
                        expectation.expected_value,
                        str(exc) or type(exc).__name__,
                    )
                )
            raise
        finally:
            if self._pending_response is response_future:
                self._pending_response = None
                self._pending_opcode = None
            if not response_future.done():
                response_future.cancel()
            elif not response_future.cancelled():
                # A poison-triggered disconnect can complete this future while
                # the request is unwinding from a write timeout.  Retrieve the
                # exception so asyncio does not report a detached failure.
                response_future.exception()
            self._diag(
                "ble.packet",
                "request_finished",
                opcode=opcode,
                transaction_id=transaction_id,
                attempted=attempted,
            )

    async def _write_chunk_until(
        self,
        characteristic_uuid: str,
        chunk: bytes,
        *,
        response: bool,
        deadline: float,
        opcode: int,
    ) -> None:
        """Bound a native write without trusting it to honour cancellation.

        ``asyncio.wait_for`` waits for a cancelled child to finish.  A native
        CoreBluetooth awaitable can suppress that cancellation, which would
        wedge the serializer forever.  Keep the write in its own task, poison
        the client at the deadline, and detach only that poisoned task.  The
        poison gate guarantees that it can never overlap a later application
        write on the same client.
        """

        loop = asyncio.get_running_loop()
        write_task = loop.create_task(
            self.transport.write(
                characteristic_uuid,
                chunk,
                response=response,
            ),
            name=f"hwcdq-write-0x{opcode:02x}",
        )
        try:
            remaining = max(0.0, deadline - loop.time())
            done, _ = await asyncio.wait({write_task}, timeout=remaining)
            if write_task in done:
                try:
                    write_task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    message = (
                        f"native BLE write failed for opcode 0x{opcode:02X}; "
                        "client poisoned, command not replayed, reconnect required"
                    )
                    self._diag(
                        "ble.write",
                        "native_write_failed",
                        opcode=opcode,
                        transaction_id=self._active_transaction_id,
                        error=exc,
                        replayed=False,
                        reconnect_required=True,
                    )
                    self._poison_current_client(message)
                    raise BackendError(message) from exc
                return

            write_task.cancel()
            self._track_orphaned_write(write_task)
            message = (
                f"native BLE write deadline expired for opcode 0x{opcode:02X}; "
                "client poisoned, command not replayed, reconnect required"
            )
            self._diag(
                "ble.timeout",
                "native_write_deadline_expired",
                opcode=opcode,
                transaction_id=self._active_transaction_id,
                deadline_monotonic=deadline,
                replayed=False,
                reconnect_required=True,
            )
            self._poison_current_client(message)
            raise CommandTimeoutError(message)
        except asyncio.CancelledError:
            # Once a native write was scheduled, cancellation cannot prove
            # that no byte reached the peripheral.  Fail closed and never use
            # this client for a second application write.
            if not write_task.done():
                write_task.cancel()
                self._track_orphaned_write(write_task)
            else:
                self._consume_finished_task(write_task)
            self._diag(
                "ble.write",
                "native_write_cancelled",
                opcode=opcode,
                transaction_id=self._active_transaction_id,
                replayed=False,
                reconnect_required=True,
            )
            self._poison_current_client(
                f"native BLE write for opcode 0x{opcode:02X} was cancelled; "
                "client poisoned and reconnect required"
            )
            raise

    def _track_orphaned_write(self, task: asyncio.Task[None]) -> None:
        self._orphaned_write_tasks.add(task)
        task.add_done_callback(self._finish_orphaned_write)

    def _finish_orphaned_write(self, task: asyncio.Task[None]) -> None:
        self._orphaned_write_tasks.discard(task)
        self._consume_finished_task(task)

    def _reap_finished_orphaned_writes(self) -> None:
        for task in tuple(self._orphaned_write_tasks):
            if task.done():
                self._finish_orphaned_write(task)

    @staticmethod
    def _consume_finished_task(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    def _poison_current_client(self, reason: str) -> None:
        if self._client_poisoned:
            return
        operation = self._active_transaction_operation
        recoverable_read_only = (
            self.state == SessionState.READY
            and operation not in {"set_voltage", "set_current", "start", "stop"}
        )
        self._client_poisoned = True
        # No packet carries a sequence number.  Once an exchange is uncertain,
        # every callback from this transport generation must be ignored so a
        # late same-opcode response cannot be mistaken for future state.
        self._connection_generation += 1
        self._poison_reason = reason
        self.last_error = reason
        self._reset_connection_data(preserve_ambiguities=True)
        self._polling_enabled = False
        poll_task = self._poll_task
        self._poll_task = None
        if (
            poll_task is not None
            and poll_task is not asyncio.current_task()
            and not poll_task.done()
        ):
            poll_task.cancel()
        self._set_state(SessionState.ERROR)
        self._diag(
            "ble.safety",
            "client_poisoned",
            transaction_id=self._active_transaction_id,
            operation=operation,
            reason=reason,
            invalidated_generation=self._connection_generation,
            recoverable_read_only=recoverable_read_only,
            reconnect_required=True,
            replayed=False,
        )
        self._emit(
            EventKind.ERROR,
            reason,
            client_poisoned=True,
            operation=operation,
            recoverable_read_only=recoverable_read_only,
            reconnect_required=True,
            replayed=False,
        )
        teardown = self._poison_teardown_task
        if teardown is None or teardown.done():
            teardown = asyncio.create_task(
                self._teardown_poisoned_client(),
                name="hwcdq-poisoned-client-teardown",
            )
            self._poison_teardown_task = teardown
            teardown.add_done_callback(self._finish_poison_teardown)

    def _finish_poison_teardown(self, task: asyncio.Task[None]) -> None:
        if self._poison_teardown_task is task:
            self._poison_teardown_task = None
        self._consume_finished_task(task)

    async def _teardown_poisoned_client(self) -> None:
        self._diag(
            "ble.lifecycle",
            "poisoned_client_teardown_started",
            transport_connected=self.transport.connected,
        )
        self._expected_disconnect = True
        try:
            if self.transport.connected:
                await self.transport.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._emit(
                EventKind.WARNING,
                f"Poisoned BLE client teardown did not complete: {exc}",
                client_poisoned=True,
                reconnect_required=True,
            )
        finally:
            self._expected_disconnect = False
            self._diag(
                "ble.lifecycle",
                "poisoned_client_teardown_finished",
                transport_connected=self.transport.connected,
            )
            # Publish the post-teardown transport value.  Otherwise the UI can
            # retain the earlier ERROR snapshot that still said BLE connected.
            self._emit(
                EventKind.STATE,
                self.state.value,
                state=self.state.value,
                teardown_complete=True,
                transport_connected=self.transport.connected,
                reconnect_required=True,
            )

    def _on_notification(self, generation: int, data: bytes) -> None:
        if generation != self._connection_generation or self._client_poisoned:
            self._diag(
                "ble.notification",
                "stale_fragment_discarded",
                callback_generation=generation,
                current_generation=self._connection_generation,
                client_poisoned=self._client_poisoned,
                fragment_size=len(data),
            )
            return
        pending_opcode = self._pending_opcode
        self._diag(
            "ble.notification",
            "fragment_received",
            opcode=pending_opcode,
            transaction_id=self._active_transaction_id,
            characteristic_uuid=self.topology.rx_uuid if self.topology else None,
            fragment=data,
            fragment_size=len(data),
            buffered_before=self._assembler.buffered_bytes,
        )
        try:
            frames = self._assembler.feed(data)
        except FrameStreamError as exc:
            self._diag(
                "ble.framing",
                "frame_reassembly_failed",
                opcode=pending_opcode,
                transaction_id=self._active_transaction_id,
                error=exc,
                buffer_reset=True,
            )
            self._fail_pending(exc)
            self._poison_current_client(
                f"notification stream framing failed; reconnect required: {exc}"
            )
            return

        self._diag(
            "ble.framing",
            "fragment_processed",
            opcode=pending_opcode,
            transaction_id=self._active_transaction_id,
            completed_frame_count=len(frames),
            buffered_after=self._assembler.buffered_bytes,
        )

        for frame in frames:
            decoded = codec.decode_packet(frame)
            opcode = int(decoded["opcode"])
            display = format_packet(frame)
            self._diag(
                "ble.packet",
                "rx_frame_decoded",
                opcode=opcode,
                transaction_id=self._active_transaction_id,
                frame=frame,
                checksum=frame[-1],
                checksum_valid=True,
                decoded=decoded,
            )
            self._emit(
                EventKind.RX,
                display,
                opcode=opcode,
                display=display,
                raw=bytes(frame),
                redacted=False,
            )
            self._apply_decoded(decoded)
            pending = self._pending_response
            if (
                pending is not None
                and not pending.done()
                and opcode == self._pending_opcode
            ):
                pending.set_result(decoded)
            elif pending is not None and not pending.done():
                self._diag(
                    "ble.packet",
                    "unexpected_response_opcode",
                    opcode=opcode,
                    transaction_id=self._active_transaction_id,
                    expected_opcode=self._pending_opcode,
                )

    def _apply_decoded(self, decoded: dict[str, Any]) -> None:
        config = decoded.get("config")
        if isinstance(config, dict):
            self.config = dict(config)
            self._config_at = self._clock()
            self._resolve_from_config()
            self._emit(EventKind.DATA, "Конфигурация обновлена")
            self._diag(
                "ble.readback",
                "configuration_updated",
                transaction_id=self._active_transaction_id,
                config=self.config,
            )

        telemetry = decoded.get("telemetry")
        if isinstance(telemetry, dict):
            self.telemetry = dict(telemetry)
            self._telemetry_at = self._clock()
            self._resolve_from_telemetry()
            self._emit(EventKind.DATA, "Телеметрия обновлена")
            self._diag(
                "ble.readback",
                "telemetry_updated",
                transaction_id=self._active_transaction_id,
                telemetry=self.telemetry,
            )

    def _on_transport_disconnected(self, generation: int) -> None:
        if generation != self._connection_generation:
            self._diag(
                "ble.lifecycle",
                "stale_transport_disconnect_discarded",
                callback_generation=generation,
                current_generation=self._connection_generation,
            )
            return
        operation = self._active_transaction_operation
        recoverable_read_only = (
            self.state == SessionState.READY
            and operation not in {"set_voltage", "set_current", "start", "stop"}
        )
        self._connection_generation += 1
        self._diag(
            "ble.lifecycle",
            "transport_disconnected_callback",
            expected=self._expected_disconnect,
            prior_state=self.state.value,
            client_poisoned=self._client_poisoned,
        )
        self._reset_connection_data(preserve_ambiguities=True)
        self._polling_enabled = False
        task = self._poll_task
        self._poll_task = None
        if (
            task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()
        self._fail_pending(TransportDisconnectedError("BLE transport disconnected"))
        if self._expected_disconnect or self.state == SessionState.DISCONNECTING:
            self._set_state(SessionState.DISCONNECTED)
        else:
            self.last_error = "BLE transport disconnected unexpectedly"
            self._set_state(SessionState.ERROR)
            self._emit(
                EventKind.ERROR,
                self.last_error,
                operation=operation,
                recoverable_read_only=recoverable_read_only,
                reconnect_required=True,
                replayed=False,
            )

    async def _cleanup_failed_connection(self) -> None:
        await self.stop_periodic_telemetry()
        teardown = self._poison_teardown_task
        if self._client_poisoned and teardown is not None and not teardown.done():
            # The timeout path already owns best-effort native teardown.  Do
            # not start a concurrent disconnect while an orphaned write may
            # still be unwinding.
            self.authenticated = False
            self._fail_pending(TransportDisconnectedError("connection setup failed"))
            self._reset_connection_data()
            return
        self._expected_disconnect = True
        try:
            if self.transport.connected:
                await self.transport.disconnect()
        except Exception:
            pass
        finally:
            self._expected_disconnect = False
            self.authenticated = False
            self._fail_pending(TransportDisconnectedError("connection setup failed"))
            self._reset_connection_data()

    def _require_readable(self, *, _allow_loading: bool = False) -> None:
        allowed = {SessionState.READY}
        if _allow_loading:
            allowed |= {SessionState.AUTHENTICATING, SessionState.LOADING}
        if self.state not in allowed or not self.transport.connected:
            raise InvalidStateError(f"session is not readable in state {self.state.value}")
        if not self.authenticated:
            raise AuthenticationError("session is not authenticated")

    def _require_application_writes_allowed(self) -> None:
        if self._client_poisoned:
            reason = self._poison_reason or "native BLE write did not terminate cleanly"
            raise InvalidStateError(
                "BLE client is poisoned; command was not transmitted; reconnect "
                f"is required ({reason})"
            )

    def _require_control_context(self, *, require_confirmation: bool) -> None:
        self._require_readable()
        if not self.output_controls_enabled:
            raise SafetyInterlockError(
                "output-changing controls were not enabled at process startup"
            )
        if not require_confirmation:
            raise SafetyInterlockError("operator confirmation is required")
        if self.control_outcome_unknown:
            raise AmbiguousCommandResultError(
                "a previous output-changing result is still unknown"
            )
        if not self.config_fresh or self.config is None:
            raise SafetyInterlockError("charger configuration is stale or unavailable")
        if not self.telemetry_fresh or self.telemetry is None:
            raise SafetyInterlockError("charger telemetry is stale or unavailable")
        self._validate_telemetry_for_control()

    def _precheck_start_context(self) -> None:
        """Reject an ineligible Start before it can enter the transaction queue."""

        self._require_readable()
        if not self.output_controls_enabled:
            raise SafetyInterlockError(
                "output-changing controls were not enabled at process startup"
            )
        if self.control_outcome_unknown:
            raise AmbiguousCommandResultError(
                "a previous output-changing result is still unknown"
            )

    def _validate_atomic_start_context(
        self,
        confirmed_voltage: tuple[float, bytes],
        confirmed_current: tuple[float, bytes],
    ) -> None:
        """Validate fresh device state immediately before the Start write."""

        self._precheck_start_context()
        if not self.config_fresh or self.config is None:
            raise SafetyInterlockError("charger configuration is stale or unavailable")
        if not self.telemetry_fresh or self.telemetry is None:
            raise SafetyInterlockError("charger telemetry is stale or unavailable")

        live_voltage = self._canonical_float32(
            self.config.get("target_voltage"),
            "device target voltage",
        )
        live_current = self._canonical_float32(
            self.config.get("target_current"),
            "device target current",
        )
        if live_voltage[1] != confirmed_voltage[1]:
            raise SafetyInterlockError(
                "confirmed voltage does not match fresh device target"
            )
        if live_current[1] != confirmed_current[1]:
            raise SafetyInterlockError(
                "confirmed current does not match fresh device target"
            )

        self._validate_voltage_limit(live_voltage[0])
        self._validate_current_limit(live_current[0])
        self._validate_telemetry_for_control()
        output_enabled = self.telemetry.get("output_enabled")
        if output_enabled is True:
            raise SafetyInterlockError("charger output is already on")
        if output_enabled is not False:
            raise SafetyInterlockError("charger output state is unknown")

    def _validate_stop_context(self) -> None:
        """Allow only an idempotent OFF request for a freshly observed ON output.

        Stop deliberately does not depend on configuration or the full atomic
        Start safety context.  It still requires the process-level
        control opt-in and a fresh, explicit output state so a mislabeled or
        stale action can never become an energizing write.
        """

        self._require_readable(_allow_loading=True)
        if not self.output_controls_enabled:
            raise SafetyInterlockError(
                "output-changing controls were not enabled at process startup"
            )
        if not self.telemetry_fresh or self.telemetry is None:
            raise SafetyInterlockError("charger telemetry is stale or unavailable")
        output_enabled = self.telemetry.get("output_enabled")
        if output_enabled is False:
            raise SafetyInterlockError("charger output is already off")
        if output_enabled is not True:
            raise SafetyInterlockError("charger output state is unknown")

    def _validate_voltage_limit(self, value: float) -> None:
        candidate = self._canonical_float32(value, "voltage")[0]
        limits = self.profile.effective_limits(self.config)
        if limits is None:
            raise SafetyInterlockError(
                "charger limits are missing, malformed, or outside the model envelope"
            )
        minimum = self._canonical_float32(
            limits.voltage.minimum,
            "effective minimum voltage",
        )[0]
        maximum = self._canonical_float32(
            limits.voltage.maximum,
            "effective maximum voltage",
        )[0]
        if candidate < minimum:
            raise SafetyInterlockError(
                "voltage "
                f"{candidate:g} V is below model minimum {limits.voltage.minimum:g} V"
            )
        if candidate > maximum:
            raise SafetyInterlockError(
                "voltage "
                f"{candidate:g} V exceeds effective maximum {limits.voltage.maximum:g} V"
            )

    def _validate_current_limit(self, value: float) -> None:
        # Deliberately do not multiply this value by module_count: the app's
        # semantics are not verified on hardware and the conservative limit is
        # the per-module field itself, capped by the HW178P model profile.
        candidate = self._canonical_float32(value, "current")[0]
        limits = self.profile.effective_limits(self.config)
        if limits is None:
            raise SafetyInterlockError(
                "charger limits are missing, malformed, or outside the model envelope"
            )
        minimum = self._canonical_float32(
            limits.current.minimum,
            "effective minimum current",
        )[0]
        maximum = self._canonical_float32(
            limits.current.maximum,
            "effective maximum current",
        )[0]
        if candidate < minimum:
            raise SafetyInterlockError(
                "current "
                f"{candidate:g} A is below model minimum {limits.current.minimum:g} A"
            )
        if candidate > maximum:
            raise SafetyInterlockError(
                "current "
                f"{candidate:g} A exceeds effective maximum {limits.current.maximum:g} A"
            )

    def _validate_telemetry_for_control(self) -> None:
        assert self.telemetry is not None
        required_float_fields = (
            "input_voltage",
            "input_current",
            "input_frequency",
            "temperature_1",
            "temperature_2",
            "output_voltage",
            "output_current",
            "current_point",
            "efficiency",
            "accumulated_capacity_ah",
            "accumulated_energy_wh",
        )
        invalid = [
            field
            for field in required_float_fields
            if not self._finite_real(self.telemetry.get(field))
        ]
        if invalid or not isinstance(self.telemetry.get("output_enabled"), bool):
            fields = ", ".join(invalid) if invalid else "output_enabled"
            raise SafetyInterlockError(
                f"telemetry contains invalid safety context: {fields}"
            )

    def _evaluate_mutation_response(
        self,
        decoded: dict[str, Any],
        expectation: AmbiguousOutcome,
        *,
        applied: bool,
    ) -> None:
        acknowledged = decoded.get("acknowledged")
        self._diag(
            "ble.readback",
            "mutation_evaluated",
            transaction_id=self._active_transaction_id,
            operation=expectation.operation,
            expected_value=expectation.expected_value,
            acknowledged=acknowledged,
            readback_applied=applied,
        )
        if acknowledged is True and applied:
            return
        if acknowledged is False and not applied:
            raise CommandRejectedError(f"charger rejected {expectation.operation}")
        if acknowledged is True:
            raise UnexpectedResponseError(
                f"{expectation.operation} was acknowledged but readback disagrees"
            )
        if acknowledged is False:
            raise UnexpectedResponseError(
                f"{expectation.operation} was rejected but readback matches it"
            )
        raise UnexpectedResponseError(
            f"{expectation.operation} response was not an acknowledgement; "
            f"readback applied={applied}"
        )

    def _mark_ambiguous(self, outcome: AmbiguousOutcome) -> None:
        self._ambiguities = [
            item
            for item in self._ambiguities
            if not (
                item.operation == outcome.operation
                and item.expected_value == outcome.expected_value
            )
        ]
        self._ambiguities.append(outcome)
        self._diag(
            "ble.safety",
            "mutation_outcome_ambiguous",
            transaction_id=self._active_transaction_id,
            operation=outcome.operation,
            expected_value=outcome.expected_value,
            reason=outcome.reason,
        )
        self._emit(
            EventKind.WARNING,
            f"Результат команды {outcome.operation} не подтверждён",
            expected_value=outcome.expected_value,
            reason=outcome.reason,
        )

    def _resolve_ambiguity(
        self,
        operation: str,
        *,
        expected_value: float | bool | None = None,
        readback_matches: bool | None = None,
        actual_value: float | bool | None = None,
    ) -> None:
        before = len(self._ambiguities)
        self._ambiguities = [
            item
            for item in self._ambiguities
            if not (
                item.operation == operation
                and (expected_value is None or item.expected_value == expected_value)
            )
        ]
        if len(self._ambiguities) != before:
            self._diag(
                "ble.readback",
                "mutation_outcome_resolved",
                transaction_id=self._active_transaction_id,
                operation=operation,
                expected_value=expected_value,
                actual_value=actual_value,
                readback_matches=readback_matches,
            )
            if readback_matches is False:
                self._emit(
                    EventKind.WARNING,
                    f"Readback не совпал для {operation}",
                    expected_value=expected_value,
                    actual_value=actual_value,
                )
            else:
                self._emit(
                    EventKind.INFO,
                    f"Readback подтвердил {operation}",
                    expected_value=expected_value,
                    actual_value=actual_value,
                )

    def _resolve_from_config(self) -> None:
        if self.config is None:
            return
        for outcome in tuple(self._ambiguities):
            field = {
                "set_voltage": "target_voltage",
                "set_current": "target_current",
            }.get(outcome.operation)
            actual = self.config.get(field) if field else None
            if (
                field
                and isinstance(actual, numbers.Real)
                and not isinstance(actual, bool)
                and math.isfinite(float(actual))
            ):
                # Readback resolves uncertainty even when it proves the
                # requested value was not applied.
                self._resolve_ambiguity(
                    outcome.operation,
                    expected_value=outcome.expected_value,
                    readback_matches=self._numbers_match(
                        actual, outcome.expected_value
                    ),
                    actual_value=float(actual),
                )

    def _resolve_from_telemetry(self) -> None:
        if self.telemetry is None:
            return
        output = self.telemetry.get("output_enabled")
        for outcome in tuple(self._ambiguities):
            if outcome.operation == "output" and isinstance(output, bool):
                # Any explicit output-state readback resolves the current
                # safety uncertainty, whether or not it matches the request.
                self._resolve_ambiguity(
                    "output",
                    expected_value=outcome.expected_value,
                    readback_matches=output is outcome.expected_value,
                    actual_value=output,
                )

    def _reset_connection_data(self, *, preserve_ambiguities: bool = False) -> None:
        self.services = ()
        self.topology = None
        self.authenticated = False
        self.firmware = None
        self.serial_number = None
        self.config = None
        self.telemetry = None
        self._config_at = None
        self._telemetry_at = None
        self._last_exchange_completed_at = None
        self._last_config_exchange_completed_at = None
        if not preserve_ambiguities:
            self._ambiguities.clear()
        self._assembler.reset()

    def _fail_pending(self, error: BaseException) -> None:
        pending = self._pending_response
        if pending is not None and not pending.done():
            pending.set_exception(error)

    def _set_state(self, state: SessionState) -> None:
        previous = self.state
        self.state = state
        self._diag(
            "session.state",
            "state_changed",
            previous=previous.value,
            current=state.value,
            transport_connected=self.transport.connected,
            authenticated=self.authenticated,
        )
        self._emit(EventKind.STATE, state.value, state=state.value)

    def _emit(self, kind: EventKind, message: str, **details: Any) -> None:
        self._diag(
            "session.event",
            kind.value,
            message=message,
            **details,
        )
        event = SessionEvent(kind, message, details)
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                # A presentation listener must never corrupt BLE sequencing.
                continue

    def _diag(self, category: str, event: str, /, **details: Any) -> None:
        logger = self._diagnostics
        if logger is None:
            return
        try:
            logger.emit(category, event, **details)
        except BaseException:
            # Diagnostics are observational and must never alter BLE behavior.
            return

    def _is_fresh(self, timestamp: float | None) -> bool:
        return timestamp is not None and self._clock() - timestamp <= self.freshness_seconds

    def _age(self, timestamp: float | None) -> float | None:
        if timestamp is None:
            return None
        return max(0.0, self._clock() - timestamp)

    @staticmethod
    def _canonical_float32(value: Any, name: str) -> tuple[float, bytes]:
        """Return the finite positive IEEE-754 binary32 value and wire bytes."""

        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise SafetyInterlockError(f"{name} is not a real number")
        converted = float(value)
        if not math.isfinite(converted) or converted <= 0:
            raise SafetyInterlockError(f"{name} must be positive and finite")
        try:
            packed = struct.pack("<f", converted)
        except (OverflowError, struct.error) as exc:
            raise SafetyInterlockError(
                f"{name} is not representable as IEEE-754 binary32"
            ) from exc
        canonical = struct.unpack("<f", packed)[0]
        if not math.isfinite(canonical) or canonical <= 0:
            raise SafetyInterlockError(
                f"{name} is not representable as a positive IEEE-754 binary32"
            )
        return canonical, packed

    @staticmethod
    def _valid_positive_number(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise SafetyInterlockError(f"{name} is not a real number")
        converted = float(value)
        if not math.isfinite(converted) or converted <= 0:
            raise SafetyInterlockError(f"{name} must be positive and finite")
        return converted

    @staticmethod
    def _numbers_match(actual: Any, expected: Any) -> bool:
        if isinstance(actual, bool) or isinstance(expected, bool):
            return False
        if not isinstance(actual, numbers.Real) or not isinstance(expected, numbers.Real):
            return False
        left = float(actual)
        right = float(expected)
        return math.isfinite(left) and math.isfinite(right) and math.isclose(
            left,
            right,
            rel_tol=1e-5,
            abs_tol=1e-4,
        )

    @staticmethod
    def _finite_real(value: Any) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, numbers.Real)
            and math.isfinite(float(value))
        )


__all__ = ["ChargerSession", "EventListener"]
