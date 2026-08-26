from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_SOURCE = ROOT / "packages" / "hwcdq-client" / "src"
sys.path.insert(0, str(CORE_SOURCE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import hwcdq  # noqa: E402
import hwcdq.protocol as canonical_protocol  # noqa: E402
from hwcdq.testing import (  # noqa: E402
    SIMULATED_IDENTIFIER,
    FakeScanner,
    FakeTransport,
)
import hwcdq_control  # noqa: E402
import hwcdq_control.backend as legacy_backend  # noqa: E402
import hwcdq_control.diagnostics as legacy_diagnostics  # noqa: E402
from tools import hwcdq_protocol as legacy_protocol  # noqa: E402


class CompatibilityIdentityTests(unittest.TestCase):
    def test_legacy_protocol_module_reexports_canonical_objects_by_identity(self) -> None:
        public_names = (
            "APK_FALLBACK_CREDENTIAL",
            "ProtocolError",
            "decode_packet",
            "derive_password_credential",
            "encode_check_password",
            "encode_check_password_credential",
            "encode_get_config",
            "encode_get_firmware",
            "encode_get_serial",
            "encode_get_telemetry",
            "encode_packet",
            "encode_set_current",
            "encode_set_voltage",
            "encode_start",
            "encode_stop",
            "verify_checksum",
        )
        for name in public_names:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(legacy_protocol, name),
                    getattr(canonical_protocol, name),
                )

    def test_legacy_backend_is_only_a_canonical_compatibility_surface(self) -> None:
        identity_pairs = {
            "ChargerSession": hwcdq.ChargerSession,
            "SessionState": hwcdq.SessionState,
            "SessionSnapshot": hwcdq.SessionSnapshot,
            "SessionEvent": hwcdq.SessionEvent,
            "DeviceAdvertisement": hwcdq.DeviceAdvertisement,
            "AsyncBleTransport": hwcdq.AsyncGattTransport,
            "AsyncBleScanner": hwcdq.AsyncScanner,
            "FakeTransport": FakeTransport,
            "FakeScanner": FakeScanner,
            "SafetyInterlockError": hwcdq.SafetyInterlockError,
            "AuthenticationError": hwcdq.AuthenticationError,
        }
        for legacy_name, canonical in identity_pairs.items():
            with self.subTest(name=legacy_name):
                self.assertIs(getattr(legacy_backend, legacy_name), canonical)

    def test_top_level_legacy_exports_and_diagnostics_keep_identity(self) -> None:
        self.assertIs(hwcdq_control.ChargerSession, hwcdq.ChargerSession)
        self.assertIs(hwcdq_control.decode_packet, hwcdq.decode_packet)
        self.assertIs(
            legacy_diagnostics.DiagnosticLogger,
            hwcdq.DiagnosticLogger,
        )

    def test_legacy_bleak_adapter_reexports_optional_canonical_implementation(self) -> None:
        try:
            import hwcdq.bleak as canonical_bleak
        except (ImportError, ModuleNotFoundError) as error:
            self.skipTest(str(error))
        import hwcdq_control.bleak_transport as legacy_bleak

        self.assertIs(legacy_bleak.BleakTransport, canonical_bleak.BleakTransport)
        self.assertIs(legacy_bleak.BleakScannerAdapter, canonical_bleak.BleakScanner)


class LegacySessionCallCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_constructor_maps_to_typed_access_and_options(self) -> None:
        clock = lambda: 42.0
        session = legacy_backend.ChargerSession(
            FakeTransport(),
            output_controls_enabled=True,
            request_timeout=0.25,
            native_write_timeout=None,
            freshness_seconds=7.0,
            notification_settle_delay=0.0,
            clock=clock,
        )

        self.assertIs(session.__class__, hwcdq.ChargerSession)
        self.assertIs(session.access, hwcdq.AccessMode.CONTROL)
        self.assertIs(session.profile, hwcdq.PIDZOOM_HW178P)
        self.assertEqual(session.request_timeout, 0.25)
        self.assertEqual(session.native_write_timeout, 0.25)
        self.assertEqual(session.freshness_seconds, 7.0)
        self.assertEqual(session.notification_settle_delay, 0.0)
        self.assertIs(session.options.clock, clock)

    def test_constructor_rejects_mixed_typed_and_legacy_options(self) -> None:
        with self.assertRaisesRegex(TypeError, "cannot be combined"):
            legacy_backend.ChargerSession(
                FakeTransport(),
                access=hwcdq.AccessMode.CONTROL,
                output_controls_enabled=True,
            )

    async def test_legacy_positional_connect_uses_plaintext_once(self) -> None:
        secret = "legacy-password"
        transport = FakeTransport(expected_password=secret)
        session = legacy_backend.ChargerSession(
            transport,
            notification_settle_delay=0.0,
        )
        self.addAsyncCleanup(session.disconnect)

        snapshot = await session.connect(SIMULATED_IDENTIFIER, secret)

        self.assertIs(snapshot.state, hwcdq.SessionState.READY)
        self.assertEqual(transport.identifier, SIMULATED_IDENTIFIER)
        self.assertNotIn(secret, repr(session.__dict__))

    async def test_legacy_keyword_connect_accepts_private_derived_credential(self) -> None:
        secret = "reconnect-password"
        digest = canonical_protocol.derive_password_credential(secret)
        transport = FakeTransport(expected_password=secret)
        session = legacy_backend.ChargerSession(
            transport,
            notification_settle_delay=0.0,
        )
        self.addAsyncCleanup(session.disconnect)

        snapshot = await session.connect(
            identifier=SIMULATED_IDENTIFIER,
            password="",
            _credential=digest,
        )

        self.assertIs(snapshot.state, hwcdq.SessionState.READY)

    async def test_legacy_mixed_positional_and_password_keyword_still_binds(self) -> None:
        secret = "mixed-call-password"
        session = legacy_backend.ChargerSession(
            FakeTransport(expected_password=secret),
            notification_settle_delay=0.0,
        )
        self.addAsyncCleanup(session.disconnect)

        snapshot = await session.connect(SIMULATED_IDENTIFIER, password=secret)

        self.assertIs(snapshot.state, hwcdq.SessionState.READY)

    async def test_legacy_password_and_digest_are_exclusive_before_ble(self) -> None:
        secret = "exclusive-password"
        transport = FakeTransport(expected_password=secret)
        session = legacy_backend.ChargerSession(
            transport,
            notification_settle_delay=0.0,
        )

        with self.assertRaisesRegex(ValueError, "exclusive"):
            await session.connect(
                SIMULATED_IDENTIFIER,
                secret,
                _credential=canonical_protocol.derive_password_credential(secret),
            )

        self.assertFalse(transport.connected)
        self.assertIs(session.state, hwcdq.SessionState.DISCONNECTED)
        self.assertIsNone(session.config)
        self.assertIsNone(session.telemetry)

    async def test_legacy_connect_error_redacts_plaintext_and_digest(self) -> None:
        secret = "пароль-legacy"
        digest = canonical_protocol.derive_password_credential(secret)

        class LeakingTransport(FakeTransport):
            async def connect(self, identifier, disconnected_callback):  # type: ignore[no-untyped-def]
                raise RuntimeError(f"{secret} {secret.encode('utf-8')!r} {digest}")

        session = legacy_backend.ChargerSession(
            LeakingTransport(),
            notification_settle_delay=0.0,
        )

        with self.assertRaises(hwcdq.BackendError) as raised:
            await session.connect(SIMULATED_IDENTIFIER, secret)

        rendered = f"{raised.exception} {session.last_error}"
        self.assertNotIn(secret, rendered)
        self.assertNotIn(repr(secret.encode("utf-8")), rendered)
        self.assertNotIn(digest, rendered.casefold())
        self.assertIn("[REDACTED]", rendered)


if __name__ == "__main__":
    unittest.main()
