from __future__ import annotations

import asyncio
import math
import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from hwcdq import (  # noqa: E402
    AccessMode,
    Credential,
    DeviceTarget,
    SessionOptions,
)
from hwcdq_control.backend import (  # noqa: E402
    AmbiguousOutcome,
    AmbiguousCommandResultError,
    AuthenticationError,
    BackendError,
    ChargerSession,
    CommandRejectedError,
    CommandTimeoutError,
    EventKind,
    FakeTransport,
    FrameStreamError,
    InvalidStateError,
    SIMULATED_IDENTIFIER,
    SafetyInterlockError,
    SessionState,
    UnexpectedResponseError,
)
from hwcdq_control.backend.redaction import REDACTED  # noqa: E402
from hwcdq_control.product import (  # noqa: E402
    APP_DISPLAY_NAME,
    LEGACY_SETTINGS_APPLICATION_NAME,
    LEGACY_SETTINGS_ORGANIZATION_NAME,
    MODEL_MAX_CURRENT_A,
    MODEL_MAX_VOLTAGE_V,
    MODEL_MIN_CURRENT_A,
    MODEL_MIN_VOLTAGE_V,
)
from tools import hwcdq_protocol as protocol  # noqa: E402


def session_options(
    *,
    request_timeout: float = 8.0,
    native_write_timeout: float | None = None,
    freshness_seconds: float = 10.0,
    notification_settle_delay: float = 0.0,
    clock=None,  # type: ignore[no-untyped-def]
) -> SessionOptions:
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return SessionOptions(
        request_timeout=request_timeout,
        native_write_timeout=native_write_timeout,
        freshness_seconds=freshness_seconds,
        notification_settle_delay=notification_settle_delay,
        **kwargs,
    )


def charger_session(
    transport,  # type: ignore[no-untyped-def]
    *,
    controls: bool = False,
    request_timeout: float = 8.0,
    native_write_timeout: float | None = None,
    freshness_seconds: float = 10.0,
    notification_settle_delay: float = 0.0,
    clock=None,  # type: ignore[no-untyped-def]
) -> ChargerSession:
    return ChargerSession(
        transport,
        access=AccessMode.CONTROL if controls else AccessMode.MONITOR_ONLY,
        options=session_options(
            request_timeout=request_timeout,
            native_write_timeout=native_write_timeout,
            freshness_seconds=freshness_seconds,
            notification_settle_delay=notification_settle_delay,
            clock=clock,
        ),
    )


async def connect_session(
    session: ChargerSession,
    password: str = "",
    *,
    identifier: str = SIMULATED_IDENTIFIER,
    digest: str | None = None,
):  # type: ignore[no-untyped-def]
    credential = (
        Credential.from_digest(digest)
        if digest is not None
        else Credential.from_password(password)
    )
    return await session.connect(DeviceTarget(identifier), credential)


class ProductProfileTests(unittest.TestCase):
    def test_hw178p_identity_limits_and_legacy_settings_namespace(self) -> None:
        self.assertEqual(APP_DISPLAY_NAME, "Pidzoom Portable charger HW178P")
        self.assertEqual(
            (MODEL_MIN_VOLTAGE_V, MODEL_MAX_VOLTAGE_V),
            (50.0, 178.0),
        )
        self.assertEqual(
            (MODEL_MIN_CURRENT_A, MODEL_MAX_CURRENT_A),
            (0.01, 14.0),
        )
        self.assertEqual(
            (LEGACY_SETTINGS_ORGANIZATION_NAME, LEGACY_SETTINGS_APPLICATION_NAME),
            ("HWCDQ interoperability", "HWCDQ Bench Control"),
        )


class SessionTestCase(unittest.IsolatedAsyncioTestCase):
    async def make_session(
        self,
        *,
        controls: bool = True,
        transport: FakeTransport | None = None,
        timeout: float = 0.1,
        clock=None,  # type: ignore[no-untyped-def]
    ) -> tuple[FakeTransport, ChargerSession]:
        fake = transport or FakeTransport(notification_fragment_size=7)
        session = charger_session(
            fake,
            controls=controls,
            request_timeout=timeout,
            freshness_seconds=5,
            notification_settle_delay=0,
            clock=clock,
        )
        await connect_session(session)
        self.addAsyncCleanup(session.disconnect)
        return fake, session

    async def test_handshake_loads_full_snapshot_and_fragmented_notifications(self) -> None:
        fake, session = await self.make_session()
        snapshot = session.snapshot
        self.assertEqual(snapshot.state, SessionState.READY)
        self.assertTrue(snapshot.authenticated)
        self.assertTrue(snapshot.config_fresh)
        self.assertTrue(snapshot.telemetry_fresh)
        self.assertEqual(snapshot.firmware, b"SIM-1.0\x00")
        self.assertEqual(snapshot.serial_number, b"HWCDQ-SIM-0001\x00")
        self.assertEqual(snapshot.config["target_voltage"], 84.0)  # type: ignore[index]
        self.assertEqual(snapshot.config["max_voltage"], 178.0)  # type: ignore[index]
        self.assertEqual(  # type: ignore[index]
            snapshot.config["max_single_module_current"],
            14.0,
        )
        self.assertEqual(snapshot.telemetry["module_count"], 2)  # type: ignore[index]
        self.assertEqual(len(snapshot.services), 1)
        self.assertIsNotNone(snapshot.topology)
        self.assertEqual(
            [record.opcode for record in fake.write_records],
            [0x02, 0x01, 0x04, 0x05, 0x06],
        )

    async def test_password_is_absent_from_events_and_simulator_history(self) -> None:
        fake = FakeTransport(expected_password="sensitive-password")
        session = charger_session(fake)
        events = []
        session.subscribe(events.append)
        await connect_session(session, "sensitive-password")
        self.addAsyncCleanup(session.disconnect)

        joined = repr(events) + repr(fake.write_records)
        self.assertNotIn("sensitive-password", joined)
        password_event = next(
            event
            for event in events
            if event.kind == EventKind.TX and event.details["opcode"] == 0x02
        )
        self.assertEqual(password_event.details["raw"], None)
        self.assertTrue(password_event.details["redacted"])
        self.assertIn(REDACTED, password_event.details["display"])
        self.assertIsNone(fake.write_records[0].raw)

    async def test_wrong_password_fails_without_leaking_it(self) -> None:
        fake = FakeTransport(expected_password="right")
        session = charger_session(fake)
        events = []
        session.subscribe(events.append)
        with self.assertRaises(AuthenticationError):
            await connect_session(session, "wrong-secret")
        self.assertEqual(session.state, SessionState.ERROR)
        self.assertFalse(fake.connected)
        self.assertEqual(fake.count_opcode(protocol.OP_CHECK_PASSWORD), 1)
        self.assertNotIn("wrong-secret", session.last_error or "")
        self.assertNotIn("wrong-secret", repr(events))

    async def test_manual_rejection_never_falls_back_or_retries(self) -> None:
        class CapturingAuthTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__(expected_password="right")
                self.auth_payloads: list[bytes] = []

            def _handle_request(self, decoded):  # type: ignore[no-untyped-def]
                if decoded["opcode"] == protocol.OP_CHECK_PASSWORD:
                    self.auth_payloads.append(bytes(decoded["payload"]))
                return super()._handle_request(decoded)

        fake = CapturingAuthTransport()
        session = charger_session(fake)
        with self.assertRaises(AuthenticationError):
            await connect_session(session, "wrong-secret")

        self.assertFalse(session.authenticated)
        self.assertEqual(fake.count_opcode(protocol.OP_CHECK_PASSWORD), 1)
        self.assertEqual(
            fake.auth_payloads,
            [
                protocol.derive_password_credential("wrong-secret").encode("ascii")
                + b"\x00"
            ],
        )
        self.assertNotEqual(
            fake.auth_payloads[0],
            protocol.APK_FALLBACK_CREDENTIAL.encode("ascii") + b"\x00",
        )

    async def test_unknown_auth_response_fails_closed_after_one_request(self) -> None:
        class UnknownAuthTransport(FakeTransport):
            def _handle_request(self, decoded):  # type: ignore[no-untyped-def]
                if decoded["opcode"] == protocol.OP_CHECK_PASSWORD:
                    return protocol.encode_packet(protocol.OP_CHECK_PASSWORD, b"\x02")
                return super()._handle_request(decoded)

        fake = UnknownAuthTransport()
        session = charger_session(fake)
        with self.assertRaises(UnexpectedResponseError):
            await connect_session(session)

        self.assertFalse(session.authenticated)
        self.assertEqual(fake.count_opcode(protocol.OP_CHECK_PASSWORD), 1)
        self.assertEqual(fake.count_opcode(protocol.OP_GET_FIRMWARE), 0)

    async def test_auth_timeout_fails_closed_without_retry(self) -> None:
        fake = FakeTransport()
        fake.drop_responses.add(protocol.OP_CHECK_PASSWORD)
        session = charger_session(
            fake,
            request_timeout=0.02,
        )
        with self.assertRaises(CommandTimeoutError):
            await connect_session(session)

        self.assertFalse(session.authenticated)
        self.assertEqual(fake.count_opcode(protocol.OP_CHECK_PASSWORD), 1)
        self.assertEqual(fake.count_opcode(protocol.OP_GET_FIRMWARE), 0)

    async def test_secret_is_scrubbed_from_a_leaking_connection_exception(self) -> None:
        secret = "sensitive-password"
        digest = protocol.derive_password_credential(secret)

        class LeakingTransport(FakeTransport):
            async def connect(self, identifier, disconnected_callback):  # type: ignore[no-untyped-def]
                # The typed transport never receives plaintext.  Model the
                # only credential material it could accidentally disclose.
                raise RuntimeError(f"transport accidentally echoed {digest}")

        session = charger_session(LeakingTransport())
        with self.assertRaises(BackendError) as captured:
            await connect_session(session, secret)
        self.assertNotIn(secret, str(captured.exception))
        self.assertNotIn(digest, str(captured.exception))
        self.assertIn(REDACTED, str(captured.exception))

    async def test_derived_credential_bytes_repr_is_scrubbed_end_to_end(self) -> None:
        secret = "пароль"
        digest = protocol.derive_password_credential(secret)

        class LeakingTransport(FakeTransport):
            async def connect(self, identifier, disconnected_callback):  # type: ignore[no-untyped-def]
                raise RuntimeError(f"native echoed {digest.encode('ascii')!r}")

        session = charger_session(LeakingTransport())
        events = []
        session.subscribe(events.append)
        with self.assertRaises(BackendError) as captured:
            await connect_session(session, secret)

        rendered = f"{captured.exception!s} {session.last_error!s} {events!r}"
        self.assertNotIn(secret, rendered)
        self.assertNotIn(digest, rendered)
        self.assertIn(REDACTED, rendered)

    async def test_disconnect_obsoletes_in_flight_connect_and_tears_down_late_success(self) -> None:
        class SlowConnectTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self.connect_entered = asyncio.Event()
                self.release_connect = asyncio.Event()

            async def connect(self, identifier, disconnected_callback):  # type: ignore[no-untyped-def]
                self.connect_entered.set()
                try:
                    await self.release_connect.wait()
                except asyncio.CancelledError:
                    # Model a native/platform connect that cannot be aborted
                    # immediately and reports success after cancellation.
                    await self.release_connect.wait()
                await super().connect(identifier, disconnected_callback)

        fake = SlowConnectTransport()
        session = charger_session(fake)
        connect_task = asyncio.create_task(
            connect_session(session)
        )
        await fake.connect_entered.wait()

        disconnect_task = asyncio.create_task(session.disconnect())
        await asyncio.sleep(0)
        fake.release_connect.set()
        await disconnect_task
        with self.assertRaises(asyncio.CancelledError):
            await connect_task

        self.assertEqual(session.state, SessionState.DISCONNECTED)
        self.assertFalse(session.authenticated)
        self.assertFalse(fake.connected)
        self.assertIsNone(session.topology)

    async def test_monitoring_mode_blocks_every_mutation_including_stop(self) -> None:
        fake, session = await self.make_session(controls=False)
        fake.output_enabled = True
        session.telemetry["output_enabled"] = True  # type: ignore[index]
        with self.assertRaises(SafetyInterlockError):
            await session.set_voltage(80, operator_confirmed=True)
        with self.assertRaises(SafetyInterlockError):
            await session.set_current(10, operator_confirmed=True)
        with self.assertRaises(SafetyInterlockError):
            await session.start(84.0, 10.0)
        with self.assertRaises(SafetyInterlockError):
            await session.stop()
        self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 0)
        self.assertEqual(fake.count_opcode(protocol.OP_SET_CURRENT), 0)
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_full_confirmed_control_cycle_uses_atomic_start_pair(self) -> None:
        fake, session = await self.make_session()
        with self.assertRaises(SafetyInterlockError):
            await session.set_voltage(80)
        await session.set_voltage(80, operator_confirmed=True)
        self.assertEqual(session.config["target_voltage"], 80.0)  # type: ignore[index]
        await session.set_current(14, operator_confirmed=True)
        self.assertEqual(session.config["target_current"], 14.0)  # type: ignore[index]
        started = await session.start(80.0, 14.0)
        self.assertTrue(started["output_enabled"])
        stopped = await session.stop()
        self.assertFalse(stopped["output_enabled"])
        self.assertFalse(session.control_outcome_unknown)
        self.assertTrue(fake.count_opcode(protocol.OP_GET_CONFIG) >= 3)
        self.assertTrue(fake.count_opcode(protocol.OP_GET_TELEMETRY) >= 3)

    async def test_atomic_start_valid_pair_has_contiguous_wire_order(self) -> None:
        fake, session = await self.make_session()
        fake.write_records.clear()

        telemetry = await session.start(84.0, 10.0)

        self.assertTrue(telemetry["output_enabled"])
        self.assertEqual(
            [record.opcode for record in fake.write_records],
            [
                protocol.OP_GET_CONFIG,
                protocol.OP_GET_TELEMETRY,
                protocol.OP_OUTPUT_CONTROL,
                protocol.OP_GET_TELEMETRY,
            ],
        )

    async def test_atomic_start_rejects_adjacent_float32_pair_without_start_write(self) -> None:
        fake, session = await self.make_session()
        bits = struct.unpack("<I", struct.pack("<f", 84.0))[0]
        adjacent = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
        self.assertLess(abs(adjacent - 84.0), 0.0001)
        self.assertNotEqual(struct.pack("<f", adjacent), struct.pack("<f", 84.0))
        fake.write_records.clear()

        with self.assertRaises(SafetyInterlockError):
            await session.start(adjacent, 10.0)

        self.assertEqual(
            [record.opcode for record in fake.write_records],
            [protocol.OP_GET_CONFIG, protocol.OP_GET_TELEMETRY],
        )
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_atomic_start_normalizes_decimal_pair_to_binary32(self) -> None:
        fake = FakeTransport()
        fake.target_voltage = 50.01
        _, session = await self.make_session(transport=fake)
        fake.write_records.clear()

        telemetry = await session.start(50.01, 10.0)

        self.assertTrue(telemetry["output_enabled"])
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 1)

    async def test_atomic_start_accepts_binary32_current_floor(self) -> None:
        fake = FakeTransport()
        fake.target_current = 0.01
        fake.max_single_module_current = 0.01
        _, session = await self.make_session(transport=fake)
        assert session.config is not None
        live_current = session.config["target_current"]
        self.assertEqual(
            struct.pack("<f", live_current),
            struct.pack("<f", MODEL_MIN_CURRENT_A),
        )
        fake.write_records.clear()

        telemetry = await session.start(84.0, MODEL_MIN_CURRENT_A)

        self.assertTrue(telemetry["output_enabled"])
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 1)

    async def test_atomic_start_rejects_invalid_or_unrepresentable_pair_before_reads(self) -> None:
        fake, session = await self.make_session()
        invalid_values = (
            math.nan,
            math.inf,
            -math.inf,
            0.0,
            -1.0,
            1e100,
            1e-100,
            True,
        )
        for field, pair_index in (("voltage", 0), ("current", 1)):
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid):
                    pair: list[object] = [84.0, 10.0]
                    pair[pair_index] = invalid
                    fake.write_records.clear()
                    with self.assertRaises(SafetyInterlockError):
                        await session.start(*pair)  # type: ignore[arg-type]
                    self.assertEqual(fake.write_records, [])

    async def test_atomic_start_rechecks_queued_device_target_and_limit(self) -> None:
        fake, session = await self.make_session()
        fake.write_records.clear()
        await session._serializer.acquire(10)
        try:
            queued = asyncio.create_task(session.start(84.0, 10.0))
            await asyncio.sleep(0)
            fake.target_voltage = 85.0
        finally:
            await session._serializer.release()

        with self.assertRaises(SafetyInterlockError):
            await queued
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)
        self.assertEqual(
            [record.opcode for record in fake.write_records],
            [protocol.OP_GET_CONFIG, protocol.OP_GET_TELEMETRY],
        )

        fake.target_voltage = 84.0
        fake.max_voltage = 80.0
        fake.write_records.clear()
        with self.assertRaises(SafetyInterlockError):
            await session.start(84.0, 10.0)
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_atomic_start_rejects_on_or_unknown_output_after_fresh_reads(self) -> None:
        fake, session = await self.make_session()
        fake.output_enabled = True
        fake.write_records.clear()
        with self.assertRaises(SafetyInterlockError):
            await session.start(84.0, 10.0)
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

        class UnknownOutputTransport(FakeTransport):
            def _telemetry_payload(self) -> bytes:
                payload = bytearray(super()._telemetry_payload())
                payload[36] = 2
                return bytes(payload)

        unknown = UnknownOutputTransport()
        _, unknown_session = await self.make_session(transport=unknown)
        unknown.write_records.clear()
        with self.assertRaises(SafetyInterlockError):
            await unknown_session.start(84.0, 10.0)
        self.assertEqual(unknown.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_atomic_start_rejects_stale_or_lost_fresh_read_context(self) -> None:
        now = [100.0]

        class AgingTelemetryTransport(FakeTransport):
            age_next_telemetry = False

            async def _deliver(self, packet: bytes) -> None:
                await super()._deliver(packet)
                decoded = protocol.decode_packet(packet)
                if (
                    self.age_next_telemetry
                    and decoded["opcode"] == protocol.OP_GET_TELEMETRY
                ):
                    self.age_next_telemetry = False
                    now[0] += 6.0

        aging = AgingTelemetryTransport()
        _, aging_session = await self.make_session(
            transport=aging,
            clock=lambda: now[0],
        )
        aging.write_records.clear()
        aging.age_next_telemetry = True
        with self.assertRaises(SafetyInterlockError):
            await aging_session.start(84.0, 10.0)
        self.assertEqual(aging.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

        lost, lost_session = await self.make_session()
        lost.write_records.clear()
        lost.disconnect_on_opcodes.add(protocol.OP_GET_TELEMETRY)
        with self.assertRaises(BackendError):
            await lost_session.start(84.0, 10.0)
        self.assertEqual(lost.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_atomic_start_precheck_blocks_unresolved_mutation_before_reads(self) -> None:
        fake, session = await self.make_session()
        session._ambiguities.append(
            AmbiguousOutcome("set_voltage", 84.0, "test unresolved mutation")
        )
        fake.write_records.clear()

        with self.assertRaises(AmbiguousCommandResultError):
            await session.start(84.0, 10.0)

        self.assertEqual(fake.write_records, [])

    async def test_model_voltage_boundaries_are_enforced_before_write(self) -> None:
        fake, session = await self.make_session()

        with self.assertRaises(SafetyInterlockError):
            await session.set_voltage(49.99, operator_confirmed=True)
        await session.set_voltage(50.0, operator_confirmed=True)
        await session.set_voltage(178.0, operator_confirmed=True)
        with self.assertRaises(SafetyInterlockError):
            await session.set_voltage(178.01, operator_confirmed=True)

        self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 2)

    async def test_model_current_boundaries_are_enforced_before_write(self) -> None:
        fake, session = await self.make_session()

        with self.assertRaises(SafetyInterlockError):
            await session.set_current(0.009, operator_confirmed=True)
        await session.set_current(0.01, operator_confirmed=True)
        await session.set_current(14.0, operator_confirmed=True)
        with self.assertRaises(SafetyInterlockError):
            await session.set_current(14.01, operator_confirmed=True)

        # Telemetry reports two modules, but the conservative limit remains
        # the single-module field capped by the HW178P profile.
        self.assertEqual(fake.count_opcode(protocol.OP_SET_CURRENT), 2)

    async def test_nonfinite_submitted_setpoints_never_write(self) -> None:
        fake, session = await self.make_session()

        for setter, opcode in (
            (session.set_voltage, protocol.OP_SET_VOLTAGE),
            (session.set_current, protocol.OP_SET_CURRENT),
        ):
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(setter=setter.__name__, value=value):
                    baseline = fake.count_opcode(opcode)
                    with self.assertRaises(SafetyInterlockError):
                        await setter(value, operator_confirmed=True)
                    self.assertEqual(fake.count_opcode(opcode), baseline)

    async def test_reported_maxima_are_intersected_with_model_ceiling(self) -> None:
        fake = FakeTransport()
        fake.max_voltage = 220.0
        fake.max_single_module_current = 20.0
        _, session = await self.make_session(transport=fake)

        await session.set_voltage(178.0, operator_confirmed=True)
        await session.set_current(14.0, operator_confirmed=True)
        with self.assertRaises(SafetyInterlockError):
            await session.set_voltage(178.01, operator_confirmed=True)
        with self.assertRaises(SafetyInterlockError):
            await session.set_current(14.01, operator_confirmed=True)

        self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 1)
        self.assertEqual(fake.count_opcode(protocol.OP_SET_CURRENT), 1)

    async def test_narrower_reported_maxima_are_authoritative(self) -> None:
        fake = FakeTransport()
        fake.max_voltage = 120.0
        fake.max_single_module_current = 5.0
        _, session = await self.make_session(transport=fake)

        await session.set_voltage(120.0, operator_confirmed=True)
        await session.set_current(5.0, operator_confirmed=True)
        with self.assertRaises(SafetyInterlockError):
            await session.set_voltage(120.01, operator_confirmed=True)
        with self.assertRaises(SafetyInterlockError):
            await session.set_current(5.01, operator_confirmed=True)

        self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 1)
        self.assertEqual(fake.count_opcode(protocol.OP_SET_CURRENT), 1)

    async def test_invalid_or_below_floor_reported_maxima_fail_closed(self) -> None:
        fake, session = await self.make_session()
        cases = (
            ("max_voltage", session.set_voltage, protocol.OP_SET_VOLTAGE, 50.0, 49.99),
            (
                "max_single_module_current",
                session.set_current,
                protocol.OP_SET_CURRENT,
                0.01,
                0.009,
            ),
        )

        for field, setter, opcode, candidate, below_floor in cases:
            for reported in (None, True, math.nan, math.inf, -math.inf, below_floor):
                with self.subTest(field=field, reported=reported):
                    assert session.config is not None
                    session.config[field] = reported
                    baseline = fake.count_opcode(opcode)
                    with self.assertRaises(SafetyInterlockError):
                        await setter(candidate, operator_confirmed=True)
                    self.assertEqual(fake.count_opcode(opcode), baseline)

    async def test_atomic_start_applies_model_profile_without_output_write(self) -> None:
        cases = (
            {"target_voltage": 49.99, "max_voltage": 220.0},
            {"target_voltage": 178.01, "max_voltage": 220.0},
            {"target_current": 14.01, "max_single_module_current": 20.0},
        )

        for overrides in cases:
            with self.subTest(overrides=overrides):
                fake = FakeTransport()
                for field, value in overrides.items():
                    setattr(fake, field, value)
                _, session = await self.make_session(transport=fake)
                fake.write_records.clear()

                with self.assertRaises(SafetyInterlockError):
                    await session.start(fake.target_voltage, fake.target_current)

                self.assertEqual(fake.write_records, [])
                self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_stop_only_needs_fresh_explicit_on_not_config_or_setpoints(self) -> None:
        fake, session = await self.make_session()
        fake.output_enabled = True
        session.telemetry["output_enabled"] = True  # type: ignore[index]
        session.telemetry["output_voltage"] = math.nan  # type: ignore[index]
        session.config = None
        session._config_at = None
        with self.assertRaises(SafetyInterlockError):
            await session.set_voltage(80, operator_confirmed=True)
        telemetry = await session.stop()
        self.assertFalse(telemetry["output_enabled"])

    async def test_stop_rejects_off_unknown_and_stale_telemetry_without_write(self) -> None:
        now = [100.0]
        fake, session = await self.make_session(clock=lambda: now[0])
        baseline = fake.count_opcode(protocol.OP_OUTPUT_CONTROL)

        with self.assertRaises(SafetyInterlockError):
            await session.stop()

        session.telemetry["output_enabled"] = None  # type: ignore[index]
        with self.assertRaises(SafetyInterlockError):
            await session.stop()

        session.telemetry["output_enabled"] = True  # type: ignore[index]
        fake.output_enabled = True
        now[0] += 6.0
        with self.assertRaises(SafetyInterlockError):
            await session.stop()

        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), baseline)

    async def test_stop_revalidates_output_after_acquiring_transaction_slot(self) -> None:
        fake, session = await self.make_session()
        fake.output_enabled = True
        session.telemetry["output_enabled"] = True  # type: ignore[index]
        fake.write_records.clear()

        await session._serializer.acquire(10)
        try:
            queued = asyncio.create_task(session.stop())
            await asyncio.sleep(0)
            fake.output_enabled = False
            session.telemetry["output_enabled"] = False  # type: ignore[index]
        finally:
            await session._serializer.release()

        with self.assertRaises(SafetyInterlockError):
            await queued
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_stop_matching_readback_confirms_output_off(self) -> None:
        fake, session = await self.make_session()
        fake.output_enabled = True
        session.telemetry["output_enabled"] = True  # type: ignore[index]
        events = []
        session.subscribe(events.append)

        telemetry = await session.stop()

        self.assertFalse(telemetry["output_enabled"])
        self.assertTrue(
            any(
                event.kind == EventKind.INFO
                and event.message == "Readback подтвердил output"
                for event in events
            )
        )

    async def test_stop_mismatched_readback_is_not_logged_as_confirmation(self) -> None:
        fake, session = await self.make_session()
        fake.output_enabled = True
        session.telemetry["output_enabled"] = True  # type: ignore[index]
        fake.mismatch_readback.add("output")
        events = []
        session.subscribe(events.append)

        with self.assertRaises(UnexpectedResponseError):
            await session.stop()

        self.assertFalse(
            any(
                event.kind == EventKind.INFO
                and event.message == "Readback подтвердил output"
                for event in events
            )
        )
        self.assertTrue(
            any(
                event.kind == EventKind.WARNING
                and event.message == "Readback не совпал для output"
                for event in events
            )
        )

    async def test_stop_ack_without_telemetry_readback_remains_ambiguous(self) -> None:
        fake, session = await self.make_session(timeout=0.02)
        fake.output_enabled = True
        session.telemetry["output_enabled"] = True  # type: ignore[index]
        fake.drop_responses.add(protocol.OP_GET_TELEMETRY)

        with self.assertRaises(AmbiguousCommandResultError):
            await session.stop()

        self.assertTrue(session.control_outcome_unknown)
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 1)

    async def test_mutating_timeout_is_not_replayed_and_invalidates_link(self) -> None:
        fake, session = await self.make_session(timeout=0.03)
        fake.drop_responses.add(protocol.OP_SET_VOLTAGE)
        with self.assertRaises(CommandTimeoutError):
            await session.set_voltage(79, operator_confirmed=True)
        self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 1)
        self.assertTrue(session.control_outcome_unknown)
        self.assertTrue(session.client_poisoned)
        self.assertEqual(session.state, SessionState.ERROR)
        with self.assertRaises(InvalidStateError):
            await session.set_current(12, operator_confirmed=True)
        with self.assertRaises(InvalidStateError):
            await session.refresh_config()
        self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 1)

    async def test_negative_ack_is_known_rejection_not_ambiguity(self) -> None:
        fake, session = await self.make_session()
        fake.reject_opcodes.add(protocol.OP_SET_CURRENT)
        with self.assertRaises(CommandRejectedError):
            await session.set_current(12, operator_confirmed=True)
        self.assertFalse(session.control_outcome_unknown)
        self.assertEqual(fake.count_opcode(protocol.OP_SET_CURRENT), 1)

    async def test_mismatched_readback_is_known_and_does_not_leave_lock(self) -> None:
        fake, session = await self.make_session()
        fake.mismatch_readback.add("set_voltage")
        events = []
        session.subscribe(events.append)
        with self.assertRaises(UnexpectedResponseError):
            await session.set_voltage(75, operator_confirmed=True)
        self.assertFalse(session.control_outcome_unknown)
        self.assertEqual(session.config["target_voltage"], 76.0)  # type: ignore[index]
        self.assertFalse(
            any(
                event.kind == EventKind.INFO
                and event.message == "Readback подтвердил set_voltage"
                for event in events
            )
        )
        self.assertTrue(
            any(
                event.kind == EventKind.WARNING
                and event.message == "Readback не совпал для set_voltage"
                for event in events
            )
        )

    async def test_disconnect_during_mutation_locks_control_without_replay(self) -> None:
        fake, session = await self.make_session()
        fake.disconnect_on_opcodes.add(protocol.OP_SET_CURRENT)
        with self.assertRaises(Exception):
            await session.set_current(11, operator_confirmed=True)
        self.assertEqual(session.state, SessionState.ERROR)
        self.assertTrue(session.control_outcome_unknown)
        self.assertEqual(fake.count_opcode(protocol.OP_SET_CURRENT), 1)

    async def test_corrupt_notification_forces_error_and_reconnect(self) -> None:
        _, session = await self.make_session()
        session._on_notification(
            session._connection_generation,
            b"\x00",
        )  # fail-closed stream boundary test
        self.assertEqual(session.state, SessionState.ERROR)
        self.assertFalse(session.authenticated)
        with self.assertRaises(Exception):
            await session.refresh_telemetry()
        self.assertTrue(await session.wait_until_reconnectable())
        self.assertFalse(session.snapshot.transport_connected)
        self.assertIsInstance(session.last_error, str)

    async def test_atomic_start_refreshes_stale_cached_context(self) -> None:
        now = [100.0]
        fake, session = await self.make_session(clock=lambda: now[0])
        now[0] += 6.0
        self.assertFalse(session.snapshot.config_fresh)
        self.assertFalse(session.snapshot.telemetry_fresh)
        telemetry = await session.start(84.0, 10.0)
        self.assertTrue(telemetry["output_enabled"])
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 1)

    async def test_requests_are_serialized_and_stop_jumps_waiting_read(self) -> None:
        fake, session = await self.make_session(
            transport=FakeTransport(response_delay=0.03),
            timeout=0.3,
        )
        fake.output_enabled = True
        session.telemetry["output_enabled"] = True  # type: ignore[index]
        fake.write_records.clear()
        first = asyncio.create_task(session.refresh_telemetry())
        await asyncio.sleep(0.005)
        queued_read = asyncio.create_task(session.refresh_config())
        prioritized_stop = asyncio.create_task(session.stop())
        await asyncio.gather(first, queued_read, prioritized_stop)
        opcodes = [record.opcode for record in fake.write_records]
        self.assertEqual(
            opcodes,
            [
                protocol.OP_GET_TELEMETRY,
                protocol.OP_OUTPUT_CONTROL,
                protocol.OP_GET_TELEMETRY,
                protocol.OP_GET_CONFIG,
            ],
        )

    async def test_queue_wait_is_not_counted_against_transaction_deadline(self) -> None:
        _, session = await self.make_session(timeout=0.04)
        await session._serializer.acquire(10)
        try:
            queued = asyncio.create_task(session.refresh_telemetry())
            await asyncio.sleep(0.06)
        finally:
            await session._serializer.release()
        telemetry = await queued
        self.assertEqual(telemetry["module_count"], 2)

    async def test_queued_start_revalidates_nonfinite_telemetry_inside_slot(self) -> None:
        class NonfiniteTelemetryTransport(FakeTransport):
            invalid_telemetry = False

            def _telemetry_payload(self) -> bytes:
                payload = bytearray(super()._telemetry_payload())
                if self.invalid_telemetry:
                    struct.pack_into("<f", payload, 20, math.nan)
                return bytes(payload)

        fake = NonfiniteTelemetryTransport()
        _, session = await self.make_session(transport=fake)
        fake.write_records.clear()
        await session._serializer.acquire(10)
        try:
            queued = asyncio.create_task(session.start(84.0, 10.0))
            await asyncio.sleep(0)
            fake.invalid_telemetry = True
        finally:
            await session._serializer.release()

        with self.assertRaises(SafetyInterlockError):
            await queued
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_queued_start_revalidates_live_limits_inside_slot(self) -> None:
        fake, session = await self.make_session()
        fake.write_records.clear()
        await session._serializer.acquire(10)
        try:
            queued = asyncio.create_task(session.start(84.0, 10.0))
            await asyncio.sleep(0)
            fake.max_voltage = 60.0
        finally:
            await session._serializer.release()

        with self.assertRaises(SafetyInterlockError):
            await queued
        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

    async def test_queued_setters_revalidate_changed_and_stale_limits(self) -> None:
        now = [100.0]
        fake, session = await self.make_session(clock=lambda: now[0])
        fake.write_records.clear()

        await session._serializer.acquire(10)
        try:
            voltage = asyncio.create_task(
                session.set_voltage(80.0, operator_confirmed=True)
            )
            await asyncio.sleep(0)
            session.config["max_voltage"] = 70.0  # type: ignore[index]
        finally:
            await session._serializer.release()
        with self.assertRaises(SafetyInterlockError):
            await voltage

        session.config["max_voltage"] = 120.0  # type: ignore[index]
        await session._serializer.acquire(10)
        try:
            current = asyncio.create_task(
                session.set_current(10.0, operator_confirmed=True)
            )
            await asyncio.sleep(0)
            now[0] += 6.0
        finally:
            await session._serializer.release()
        with self.assertRaises(SafetyInterlockError):
            await current

        self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 0)
        self.assertEqual(fake.count_opcode(protocol.OP_SET_CURRENT), 0)

    async def test_periodic_polling_skips_atomic_mutation_readback(self) -> None:
        fake, session = await self.make_session(
            transport=FakeTransport(response_delay=0.01),
            timeout=0.2,
        )
        fake.write_records.clear()
        session.start_periodic_telemetry(0.002)
        await asyncio.sleep(0.004)
        await session.set_voltage(78, operator_confirmed=True)
        await session.stop_periodic_telemetry()
        opcodes = [record.opcode for record in fake.write_records]
        set_index = opcodes.index(protocol.OP_SET_VOLTAGE)
        self.assertEqual(opcodes[set_index + 1], protocol.OP_GET_CONFIG)

    async def test_periodic_polling_cannot_interleave_atomic_start_sequence(self) -> None:
        fake, session = await self.make_session(
            transport=FakeTransport(response_delay=0.01),
            timeout=0.2,
        )
        fake.write_records.clear()
        session.start_periodic_telemetry(0.002, config_interval=0.003)
        try:
            await asyncio.sleep(0.004)
            await session.start(84.0, 10.0)
        finally:
            await session.stop_periodic_telemetry()

        opcodes = [record.opcode for record in fake.write_records]
        start_index = opcodes.index(protocol.OP_OUTPUT_CONTROL)
        self.assertEqual(
            opcodes[start_index - 2 : start_index + 2],
            [
                protocol.OP_GET_CONFIG,
                protocol.OP_GET_TELEMETRY,
                protocol.OP_OUTPUT_CONTROL,
                protocol.OP_GET_TELEMETRY,
            ],
        )

    async def test_start_negative_ack_remains_known_rejection(self) -> None:
        fake, session = await self.make_session()
        fake.reject_opcodes.add(protocol.OP_OUTPUT_CONTROL)
        fake.write_records.clear()

        with self.assertRaises(CommandRejectedError):
            await session.start(84.0, 10.0)

        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 1)
        self.assertFalse(session.control_outcome_unknown)

    async def test_start_timeout_is_not_replayed_and_preserves_ambiguity(self) -> None:
        fake, session = await self.make_session(timeout=0.02)
        fake.drop_responses.add(protocol.OP_OUTPUT_CONTROL)
        fake.write_records.clear()

        with self.assertRaises(CommandTimeoutError):
            await session.start(84.0, 10.0)

        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 1)
        self.assertTrue(session.control_outcome_unknown)
        self.assertTrue(session.client_poisoned)

    async def test_start_ack_without_readback_remains_ambiguous(self) -> None:
        class DropTelemetryAfterStartTransport(FakeTransport):
            output_request_seen = False

            def _handle_request(self, decoded):  # type: ignore[no-untyped-def]
                opcode = int(decoded["opcode"])
                if (
                    opcode == protocol.OP_GET_TELEMETRY
                    and self.output_request_seen
                ):
                    return None
                response = super()._handle_request(decoded)
                if opcode == protocol.OP_OUTPUT_CONTROL:
                    self.output_request_seen = True
                return response

        fake = DropTelemetryAfterStartTransport()
        _, session = await self.make_session(transport=fake, timeout=0.02)
        fake.write_records.clear()

        with self.assertRaises(AmbiguousCommandResultError):
            await session.start(84.0, 10.0)

        self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 1)
        self.assertTrue(session.control_outcome_unknown)

    async def test_response_timeout_blocks_late_same_opcode_on_old_link(self) -> None:
        class RetainedCallbackTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self.notify_callbacks = []

            async def start_notify(self, characteristic_uuid, callback):  # type: ignore[no-untyped-def]
                self.notify_callbacks.append(callback)
                await super().start_notify(characteristic_uuid, callback)

        fake = RetainedCallbackTransport()
        session = charger_session(
            fake,
            request_timeout=0.02,
            native_write_timeout=0.1,
        )
        snapshots = []
        session.subscribe(lambda event: snapshots.append(session.snapshot))
        await connect_session(session)
        self.addAsyncCleanup(session.disconnect)
        old_notify = fake.notify_callbacks[-1]
        baseline = fake.count_opcode(protocol.OP_GET_TELEMETRY)
        fake.drop_responses.add(protocol.OP_GET_TELEMETRY)

        with self.assertRaises(CommandTimeoutError):
            await session.refresh_telemetry()
        with self.assertRaises(InvalidStateError):
            await session.refresh_telemetry()
        self.assertEqual(
            fake.count_opcode(protocol.OP_GET_TELEMETRY),
            baseline + 1,
        )
        self.assertTrue(await session.wait_until_reconnectable())

        fake.output_enabled = True
        old_notify(
            protocol.encode_packet(
                protocol.OP_GET_TELEMETRY,
                fake._telemetry_payload(),
            )
        )
        await asyncio.sleep(0)
        # A poisoned/timed-out connection is fully reset.  The callback from
        # its retired generation must not repopulate stale telemetry.
        self.assertIsNone(session.telemetry)
        self.assertTrue(snapshots)
        self.assertFalse(snapshots[-1].transport_connected)

    async def test_callbacks_from_previous_generation_are_ignored(self) -> None:
        class RetainedCallbackTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self.disconnect_callbacks = []
                self.notify_callbacks = []

            async def connect(self, identifier, disconnected_callback):  # type: ignore[no-untyped-def]
                self.disconnect_callbacks.append(disconnected_callback)
                await super().connect(identifier, disconnected_callback)

            async def start_notify(self, characteristic_uuid, callback):  # type: ignore[no-untyped-def]
                self.notify_callbacks.append(callback)
                await super().start_notify(characteristic_uuid, callback)

        fake = RetainedCallbackTransport()
        session = charger_session(fake)
        await connect_session(session)
        self.addAsyncCleanup(session.disconnect)
        old_disconnect = fake.disconnect_callbacks[-1]
        old_notify = fake.notify_callbacks[-1]
        await session.disconnect()
        await connect_session(session)
        current_voltage = session.config["target_voltage"]  # type: ignore[index]

        fake.target_voltage = 99.0
        stale_config = protocol.encode_packet(
            protocol.OP_GET_CONFIG,
            fake._config_payload(),
        )
        fake.target_voltage = float(current_voltage)
        old_notify(stale_config)
        old_disconnect()
        await asyncio.sleep(0)

        self.assertEqual(session.state, SessionState.READY)
        self.assertTrue(session.authenticated)
        self.assertTrue(fake.connected)
        self.assertEqual(
            session.config["target_voltage"],  # type: ignore[index]
            current_voltage,
        )

    async def test_periodic_maintenance_waits_after_each_exchange(self) -> None:
        class TimedTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__(response_delay=0.004)
                self.tx_times = []
                self.rx_times = []

            async def write(self, characteristic_uuid, data, *, response):  # type: ignore[no-untyped-def]
                try:
                    opcode = int(protocol.decode_packet(data)["opcode"])
                except protocol.ProtocolError:
                    opcode = -1
                if opcode >= 0:
                    self.tx_times.append((opcode, asyncio.get_running_loop().time()))
                await super().write(characteristic_uuid, data, response=response)

            async def _deliver(self, packet):  # type: ignore[no-untyped-def]
                await super()._deliver(packet)
                opcode = int(protocol.decode_packet(packet)["opcode"])
                self.rx_times.append((opcode, asyncio.get_running_loop().time()))

        fake = TimedTransport()
        session = charger_session(
            fake,
            request_timeout=0.1,
        )
        await connect_session(session)
        self.addAsyncCleanup(session.disconnect)
        fake.tx_times.clear()
        fake.rx_times.clear()

        session.start_periodic_telemetry(0.012, config_interval=0.035)
        await asyncio.sleep(0.115)
        await session.stop_periodic_telemetry()

        maintenance = {
            protocol.OP_GET_CONFIG,
            protocol.OP_GET_TELEMETRY,
        }
        tx = [(opcode, at) for opcode, at in fake.tx_times if opcode in maintenance]
        rx = [(opcode, at) for opcode, at in fake.rx_times if opcode in maintenance]
        self.assertGreaterEqual(
            sum(opcode == protocol.OP_GET_TELEMETRY for opcode, _ in tx),
            3,
        )
        self.assertGreaterEqual(
            sum(opcode == protocol.OP_GET_CONFIG for opcode, _ in tx),
            2,
        )
        for (_, prior_rx), (_, next_tx) in zip(rx, tx[1:]):
            self.assertGreaterEqual(next_tx - prior_rx, 0.010)

    async def test_manual_exchange_restarts_full_maintenance_quiet_period(self) -> None:
        class TimedTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self.tx_times = []

            async def write(self, characteristic_uuid, data, *, response):  # type: ignore[no-untyped-def]
                try:
                    opcode = int(protocol.decode_packet(data)["opcode"])
                except protocol.ProtocolError:
                    opcode = -1
                if opcode >= 0:
                    self.tx_times.append((opcode, asyncio.get_running_loop().time()))
                await super().write(characteristic_uuid, data, response=response)

        fake = TimedTransport()
        session = charger_session(
            fake,
            request_timeout=0.1,
        )
        await connect_session(session)
        self.addAsyncCleanup(session.disconnect)
        fake.tx_times.clear()
        interval = 0.05
        session.start_periodic_telemetry(interval, config_interval=1.0)
        await asyncio.sleep(0.04)
        await session.refresh_config()
        manual_completed_at = asyncio.get_running_loop().time()

        deadline = manual_completed_at + 0.2
        while asyncio.get_running_loop().time() < deadline:
            telemetry_times = [
                at
                for opcode, at in fake.tx_times
                if opcode == protocol.OP_GET_TELEMETRY
            ]
            if telemetry_times:
                break
            await asyncio.sleep(0.002)
        await session.stop_periodic_telemetry()

        self.assertTrue(telemetry_times)
        self.assertGreaterEqual(
            telemetry_times[0] - manual_completed_at,
            interval - 0.005,
        )


class WnrSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_wnr_mode_uses_advertised_chunk_size(self) -> None:
        fake = FakeTransport(
            write_properties=("write-without-response",),
            max_write_without_response_size=3,
        )
        session = charger_session(fake)
        await connect_session(session)
        self.addAsyncCleanup(session.disconnect)
        self.assertFalse(session.topology.write_with_response)  # type: ignore[union-attr]
        password = fake.write_records[0]
        self.assertFalse(password.response)
        self.assertEqual(password.chunk_count, 12)
        self.assertIsNone(password.raw)

    async def test_response_deadline_starts_after_all_chunks(self) -> None:
        class SlowChunkTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__(
                    write_properties=("write-without-response",),
                    max_write_without_response_size=3,
                )
                self.slow = False

            async def write(self, characteristic_uuid, data, *, response):  # type: ignore[no-untyped-def]
                if self.slow:
                    await asyncio.sleep(0.012)
                await super().write(
                    characteristic_uuid,
                    data,
                    response=response,
                )

        fake = SlowChunkTransport()
        session = charger_session(
            fake,
            controls=True,
            request_timeout=0.055,
            native_write_timeout=0.2,
        )
        await connect_session(session)
        self.addAsyncCleanup(session.disconnect)
        fake.write_records.clear()
        fake.response_delay = 0.03
        fake.slow = True

        config = await session.set_voltage(80.0, operator_confirmed=True)

        self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 1)
        self.assertEqual(config["target_voltage"], 80.0)
        self.assertFalse(session.control_outcome_unknown)
        # Slow chunk transmission does not consume the independent response
        # deadline, and the ACK/readback exchanges each receive a fresh one.
        self.assertFalse(session.client_poisoned)


class PoisonedWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_resistant_write_poison_blocks_later_stop(self) -> None:
        class CancellationResistantTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self.hang = False
                self.write_entered = asyncio.Event()
                self.release_write = asyncio.Event()
                self.native_write_calls = 0

            async def write(self, characteristic_uuid, data, *, response):  # type: ignore[no-untyped-def]
                self.native_write_calls += 1
                if self.hang:
                    self.write_entered.set()
                    while not self.release_write.is_set():
                        try:
                            await self.release_write.wait()
                        except asyncio.CancelledError:
                            # Model a platform awaitable that ignores task
                            # cancellation while its native operation remains.
                            continue
                await super().write(
                    characteristic_uuid,
                    data,
                    response=response,
                )

        fake = CancellationResistantTransport()
        session = charger_session(
            fake,
            controls=True,
            request_timeout=0.1,
            native_write_timeout=0.02,
        )
        await connect_session(session)
        self.addAsyncCleanup(session.disconnect)
        fake.output_enabled = True
        session.telemetry["output_enabled"] = True  # type: ignore[index]
        fake.write_records.clear()
        baseline_calls = fake.native_write_calls
        fake.hang = True

        try:
            mutation = asyncio.create_task(
                session.set_voltage(80.0, operator_confirmed=True)
            )
            await fake.write_entered.wait()
            prioritized_stop = asyncio.create_task(session.stop())

            with self.assertRaises(CommandTimeoutError):
                await asyncio.wait_for(mutation, timeout=0.2)
            with self.assertRaises(InvalidStateError) as captured:
                await asyncio.wait_for(prioritized_stop, timeout=0.2)

            self.assertTrue(session.client_poisoned)
            self.assertTrue(session.control_outcome_unknown)
            self.assertIn("not transmitted", str(captured.exception))
            self.assertIn("reconnect", str(captured.exception))
            self.assertEqual(fake.native_write_calls, baseline_calls + 1)
            self.assertEqual(fake.count_opcode(protocol.OP_SET_VOLTAGE), 0)
            self.assertEqual(fake.count_opcode(protocol.OP_OUTPUT_CONTROL), 0)

            with self.assertRaises(InvalidStateError):
                await connect_session(session)
        finally:
            # Never leave the deliberately cancellation-resistant native write
            # alive when an assertion above fails.
            fake.release_write.set()
            fake.hang = False
        for _ in range(20):
            await asyncio.sleep(0)
            if not session._orphaned_write_tasks:
                break
        self.assertFalse(session._orphaned_write_tasks)

        # Only a fully torn-down link with no native orphan may clear poison.
        await connect_session(session)
        self.assertFalse(session.client_poisoned)
        self.assertEqual(session.state, SessionState.READY)


if __name__ == "__main__":
    unittest.main()
