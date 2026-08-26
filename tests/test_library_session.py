from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_SOURCE = ROOT / "packages" / "hwcdq-client" / "src"
sys.path.insert(0, str(CORE_SOURCE))

from hwcdq import (  # noqa: E402
    AccessMode,
    AsyncGattTransport,
    AsyncScanner,
    AuthenticationError,
    ChargerSession,
    CommandTimeoutError,
    Credential,
    DeviceAdvertisement,
    DeviceTarget,
    DiagnosticLogger,
    InvalidStateError,
    SessionOptions,
    SessionState,
)
from hwcdq import protocol  # noqa: E402
from hwcdq.testing import (  # noqa: E402
    FakeScanner,
    FakeTransport,
    SIMULATED_IDENTIFIER,
)


class ThirdPartyTransport:
    """Composition-only transport: intentionally not derived from project code."""

    def __init__(self, inner: FakeTransport | None = None) -> None:
        self.inner = inner or FakeTransport()
        self.received_identifiers: list[str] = []

    @property
    def connected(self) -> bool:
        return self.inner.connected

    async def connect(self, identifier, disconnected_callback):  # type: ignore[no-untyped-def]
        self.received_identifiers.append(identifier)
        await self.inner.connect(SIMULATED_IDENTIFIER, disconnected_callback)

    async def disconnect(self) -> None:
        await self.inner.disconnect()

    async def discover_gatt(self):  # type: ignore[no-untyped-def]
        return await self.inner.discover_gatt()

    async def start_notify(self, characteristic_uuid, callback):  # type: ignore[no-untyped-def]
        await self.inner.start_notify(characteristic_uuid, callback)

    async def stop_notify(self, characteristic_uuid):  # type: ignore[no-untyped-def]
        await self.inner.stop_notify(characteristic_uuid)

    async def write(self, characteristic_uuid, data, *, response):  # type: ignore[no-untyped-def]
        await self.inner.write(characteristic_uuid, data, response=response)


class MultiTargetTransport(ThirdPartyTransport):
    def __init__(self) -> None:
        super().__init__(FakeTransport())

    async def connect(self, identifier, disconnected_callback):  # type: ignore[no-untyped-def]
        self.received_identifiers.append(identifier)
        if identifier == "DEVICE-B":
            self.inner.drop_responses.add(protocol.OP_GET_CONFIG)
        else:
            self.inner.drop_responses.discard(protocol.OP_GET_CONFIG)
        await self.inner.connect(SIMULATED_IDENTIFIER, disconnected_callback)


class BlockingThirdPartyScanner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def scan(self, duration, callback=None):  # type: ignore[no-untyped-def]
        if duration <= 0:
            raise ValueError("scan duration must be positive")
        self.started.set()
        await asyncio.Event().wait()
        return ()


def options(*, request_timeout: float = 0.1) -> SessionOptions:
    return SessionOptions(
        request_timeout=request_timeout,
        freshness_seconds=5.0,
        notification_settle_delay=0.0,
    )


class TransportContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_structural_third_party_transport_drives_a_complete_session(self) -> None:
        transport = ThirdPartyTransport()
        self.assertIsInstance(transport, AsyncGattTransport)
        target_identifier = "  opaque-CoreBluetooth-identifier  "
        session = ChargerSession(transport, options=options())
        self.addAsyncCleanup(session.disconnect)

        snapshot = await session.connect(
            DeviceTarget(target_identifier),
            Credential.apk_fallback(),
        )

        self.assertEqual(snapshot.state, SessionState.READY)
        self.assertTrue(snapshot.authenticated)
        self.assertEqual(transport.received_identifiers, [target_identifier])
        self.assertEqual(snapshot.config["target_voltage"], 84.0)  # type: ignore[index]
        self.assertEqual(snapshot.telemetry["module_count"], 2)  # type: ignore[index]

    async def test_scanner_contract_duration_and_cancellation_are_caller_controlled(self) -> None:
        scanner = BlockingThirdPartyScanner()
        self.assertIsInstance(scanner, AsyncScanner)
        with self.assertRaises(ValueError):
            await scanner.scan(0)

        task = asyncio.create_task(scanner.scan(30.0))
        await scanner.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_library_fake_scanner_validates_duration_and_delivers_results(self) -> None:
        advertisement = DeviceAdvertisement(
            identifier="opaque-device-id",
            name="HWCDQ",
            rssi=-42,
        )
        scanner = FakeScanner([advertisement])
        self.assertIsInstance(scanner, AsyncScanner)
        seen: list[DeviceAdvertisement] = []
        result = await scanner.scan(0.01, seen.append)
        self.assertEqual(tuple(result), (advertisement,))
        self.assertEqual(seen, [advertisement])
        for duration in (0, -1):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    await scanner.scan(duration)


class CredentialBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_password_and_explicit_apk_fallback_both_authenticate(self) -> None:
        for credential in (
            Credential.from_password(""),
            Credential.apk_fallback(),
        ):
            with self.subTest(credential=repr(credential)):
                transport = ThirdPartyTransport(FakeTransport(expected_password=""))
                session = ChargerSession(transport, options=options())
                snapshot = await session.connect(
                    DeviceTarget("DEVICE-A"),
                    credential,
                )
                self.assertTrue(snapshot.authenticated)
                await session.disconnect()

    async def test_secret_and_digest_are_absent_from_events_errors_records_and_logs(self) -> None:
        secret = "session-secret-redaction-sentinel"
        digest = hashlib.md5(secret.encode("utf-8")).hexdigest().upper()
        transport = ThirdPartyTransport(FakeTransport(expected_password=secret))
        events = []
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory).resolve() / "logs" / "library.jsonl"
            diagnostics = DiagnosticLogger(log_path, enabled=True)
            session = ChargerSession(
                transport,
                options=options(),
                diagnostics=diagnostics,
            )
            session.subscribe(events.append)
            await session.connect(
                DeviceTarget("DEVICE-A"),
                Credential.from_password(secret),
            )
            await session.disconnect()
            diagnostics.close()
            log_text = log_path.read_text(encoding="utf-8")

        rendered = " ".join(
            (
                repr(events),
                repr(transport.inner.write_records),
                repr(session.snapshot),
                log_text,
            )
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn(digest, rendered)

    async def test_authentication_error_and_diagnostics_do_not_disclose_credential(self) -> None:
        secret = "rejected-session-secret-sentinel"
        digest = hashlib.md5(secret.encode("utf-8")).hexdigest().upper()
        transport = ThirdPartyTransport(FakeTransport(expected_password="different"))
        events = []
        with tempfile.TemporaryDirectory() as directory:
            log_path = (
                Path(directory).resolve() / "logs" / "rejected-library.jsonl"
            )
            diagnostics = DiagnosticLogger(log_path, enabled=True)
            session = ChargerSession(
                transport,
                options=options(),
                diagnostics=diagnostics,
            )
            session.subscribe(events.append)
            with self.assertRaises(AuthenticationError) as captured:
                await session.connect(
                    DeviceTarget("DEVICE-A"),
                    Credential.from_password(secret),
                )
            diagnostics.close()
            log_text = log_path.read_text(encoding="utf-8")

        rendered = f"{captured.exception!s} {events!r} {session.snapshot!r} {log_text}"
        self.assertNotIn(secret, rendered)
        self.assertNotIn(digest, rendered)

    async def test_prederived_digest_authenticates_without_public_digest_access(self) -> None:
        secret = "prederived-secret"
        digest = hashlib.md5(secret.encode("utf-8")).hexdigest().upper()
        transport = ThirdPartyTransport(FakeTransport(expected_password=secret))
        session = ChargerSession(transport, options=options())
        self.addAsyncCleanup(session.disconnect)
        snapshot = await session.connect(
            DeviceTarget("DEVICE-A"),
            Credential.from_digest(digest),
        )
        self.assertTrue(snapshot.authenticated)


class CrossTargetIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_second_target_cannot_reuse_first_target_configuration(self) -> None:
        transport = MultiTargetTransport()
        session = ChargerSession(
            transport,
            access=AccessMode.CONTROL,
            options=options(request_timeout=0.02),
        )
        self.addAsyncCleanup(session.disconnect)

        first = await session.connect(
            DeviceTarget("DEVICE-A"),
            Credential.apk_fallback(),
        )
        self.assertTrue(first.config_fresh)
        self.assertIsNotNone(first.config)
        await session.disconnect()

        with self.assertRaises(CommandTimeoutError):
            await session.connect(
                DeviceTarget("DEVICE-B"),
                Credential.apk_fallback(),
            )

        failed = session.snapshot
        self.assertEqual(failed.state, SessionState.ERROR)
        self.assertIsNone(failed.config)
        self.assertIsNone(failed.telemetry)
        self.assertFalse(failed.config_fresh)
        self.assertFalse(failed.telemetry_fresh)

        mutating_opcodes = {
            protocol.OP_SET_VOLTAGE,
            protocol.OP_SET_CURRENT,
            protocol.OP_OUTPUT_CONTROL,
        }
        before = sum(
            record.opcode in mutating_opcodes
            for record in transport.inner.write_records
        )
        with self.assertRaises(InvalidStateError):
            await session.set_voltage(84.0, operator_confirmed=True)
        with self.assertRaises(InvalidStateError):
            await session.start(84.0, 10.0)
        after = sum(
            record.opcode in mutating_opcodes
            for record in transport.inner.write_records
        )
        self.assertEqual(after, before, "stale target data allowed a mutating write")


if __name__ == "__main__":
    unittest.main()
