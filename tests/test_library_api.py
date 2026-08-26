from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_SOURCE = ROOT / "packages" / "hwcdq-client" / "src"
sys.path.insert(0, str(CORE_SOURCE))

import hwcdq  # noqa: E402
from hwcdq import (  # noqa: E402
    Credential,
    DeviceTarget,
    NumericRange,
    PIDZOOM_HW178P,
)


EXPECTED_ROOT_EXPORTS = {
    "__version__",
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
    "encode_set_current",
    "encode_set_voltage",
    "encode_start",
    "encode_stop",
    "verify_checksum",
    "DiagnosticLogger",
    "AccessMode",
    "ChargerProfile",
    "Credential",
    "DeviceTarget",
    "EffectiveLimits",
    "GattProfile",
    "NumericRange",
    "PIDZOOM_HW178P",
    "SessionOptions",
    "ChargerSession",
    "AmbiguousOutcome",
    "DeviceAdvertisement",
    "EventKind",
    "GattCharacteristic",
    "GattService",
    "SelectedGattTopology",
    "SessionEvent",
    "SessionSnapshot",
    "SessionState",
    "AsyncGattTransport",
    "AsyncScanner",
    "AmbiguousCommandResultError",
    "AuthenticationError",
    "BackendError",
    "CommandRejectedError",
    "CommandTimeoutError",
    "FrameStreamError",
    "GattTopologyError",
    "InvalidStateError",
    "SafetyInterlockError",
    "TransportDisconnectedError",
    "UnexpectedResponseError",
}


class PublicApiTests(unittest.TestCase):
    def test_root_all_is_the_deliberate_supported_surface(self) -> None:
        exports = tuple(hwcdq.__all__)
        self.assertEqual(len(exports), len(set(exports)), "duplicate public export")
        self.assertEqual(set(exports), EXPECTED_ROOT_EXPORTS)
        for name in exports:
            self.assertTrue(hasattr(hwcdq, name), name)

        # These are deliberately explicit, lower-level module APIs.  Keeping
        # them off the root prevents an accidental compatibility promise.
        for internal_name in (
            "encode_packet",
            "FrameAssembler",
            "FakeScanner",
            "FakeTransport",
            "BleakScanner",
            "BleakTransport",
            "AsyncBleScanner",
            "AsyncBleTransport",
        ):
            self.assertFalse(hasattr(hwcdq, internal_name), internal_name)

    def test_all_nonoptional_modules_import_without_bleak_or_qt(self) -> None:
        modules = (
            "hwcdq",
            "hwcdq.protocol",
            "hwcdq.models",
            "hwcdq.gatt",
            "hwcdq.transport",
            "hwcdq.session",
            "hwcdq.profile",
            "hwcdq.diagnostics",
            "hwcdq.errors",
            "hwcdq.framing",
            "hwcdq.serialization",
            "hwcdq.redaction",
            "hwcdq.testing",
        )
        script = """
import importlib
import sys

sys.path.insert(0, sys.argv[1])
for module_name in sys.argv[2:]:
    importlib.import_module(module_name)
for forbidden in ("bleak", "PySide6"):
    if forbidden in sys.modules or any(
        name.startswith(forbidden + ".") for name in sys.modules
    ):
        raise SystemExit(f"unexpected optional import: {forbidden}")
"""
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    script,
                    str(CORE_SOURCE),
                    *modules,
                ],
                cwd=working_directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_optional_bleak_import_explains_the_extra_when_dependency_is_absent(self) -> None:
        script = """
import sys

sys.path.insert(0, sys.argv[1])
try:
    import hwcdq.bleak  # noqa: F401
except (ImportError, ModuleNotFoundError) as error:
    rendered = str(error)
    if "hwcdq-client[bleak]" not in rendered:
        raise SystemExit(f"unhelpful optional dependency error: {rendered}")
else:
    raise SystemExit("hwcdq.bleak unexpectedly imported without site packages")
"""
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run(
                [sys.executable, "-S", "-c", script, str(CORE_SOURCE)],
                cwd=working_directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DeviceTargetTests(unittest.TestCase):
    def test_identifier_is_opaque_and_preserved_byte_for_byte(self) -> None:
        identifier = "  00000000-0000-0000-0000-000000000001  "
        target = DeviceTarget(identifier, advertised_name="HWCDQ_TEST_0001")
        self.assertEqual(target.identifier, identifier)
        self.assertEqual(target.advertised_name, "HWCDQ_TEST_0001")

    def test_empty_or_non_string_identifier_is_rejected(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            DeviceTarget("")
        for value in (None, b"DEVICE", 123):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    DeviceTarget(value)  # type: ignore[arg-type]


class CredentialTests(unittest.TestCase):
    def test_password_digest_and_fallback_constructors_validate_without_disclosure(self) -> None:
        secret = "credential-redaction-sentinel"
        digest = hashlib.md5(secret.encode("utf-8")).hexdigest().upper()
        credentials = (
            Credential.from_password(secret),
            Credential.from_digest(digest),
            Credential.apk_fallback(),
        )
        for credential in credentials:
            rendered = f"{credential!s} {credential!r}"
            self.assertNotIn(secret, rendered)
            self.assertNotIn(digest, rendered)
            self.assertIn("REDACT", rendered.upper())
            self.assertFalse(hasattr(credential, "digest"))
            self.assertFalse(hasattr(credential, "__dict__"))

        # An empty password and the explicit fallback are both accepted public
        # constructors.  Their wire equivalence is covered through a session
        # in test_library_session without exposing the private digest here.
        empty_password = Credential.from_password("")
        self.assertIn("REDACT", repr(empty_password).upper())

    def test_malformed_digest_is_rejected_without_echoing_input(self) -> None:
        invalid_values = (
            "digest-error-redaction-sentinel",
            "0" * 31,
            "0" * 33,
            "G" * 32,
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)) as captured:
                    Credential.from_digest(value)
                self.assertNotIn(value, str(captured.exception))

        with self.assertRaises((TypeError, ValueError)):
            Credential.from_digest(None)  # type: ignore[arg-type]
        with self.assertRaises((TypeError, ValueError)):
            Credential.from_password(None)  # type: ignore[arg-type]


class ProductProfileTests(unittest.TestCase):
    def test_pidzoom_hw178p_model_envelope(self) -> None:
        self.assertEqual(PIDZOOM_HW178P.model, "HW178P")
        self.assertEqual(
            (PIDZOOM_HW178P.voltage.minimum, PIDZOOM_HW178P.voltage.maximum),
            (50.0, 178.0),
        )
        self.assertEqual(
            (PIDZOOM_HW178P.current.minimum, PIDZOOM_HW178P.current.maximum),
            (0.01, 14.0),
        )

    def test_effective_limits_intersect_reported_device_maxima(self) -> None:
        lower = PIDZOOM_HW178P.effective_limits(
            {
                "max_voltage": 120.0,
                "max_single_module_current": 7.5,
            }
        )
        self.assertIsNotNone(lower)
        assert lower is not None
        self.assertEqual((lower.voltage.minimum, lower.voltage.maximum), (50.0, 120.0))
        self.assertEqual((lower.current.minimum, lower.current.maximum), (0.01, 7.5))

        capped = PIDZOOM_HW178P.effective_limits(
            {
                "max_voltage": 999.0,
                "max_single_module_current": 999.0,
            }
        )
        self.assertIsNotNone(capped)
        assert capped is not None
        self.assertEqual(capped.voltage.maximum, 178.0)
        self.assertEqual(capped.current.maximum, 14.0)

    def test_effective_limits_fail_closed_on_incomplete_or_invalid_device_data(self) -> None:
        invalid_configs = (
            None,
            {},
            {"max_voltage": 178.0},
            {"max_single_module_current": 14.0},
            {"max_voltage": True, "max_single_module_current": 14.0},
            {"max_voltage": 178.0, "max_single_module_current": False},
            {"max_voltage": math.nan, "max_single_module_current": 14.0},
            {"max_voltage": math.inf, "max_single_module_current": 14.0},
            {"max_voltage": 0.0, "max_single_module_current": 14.0},
            {"max_voltage": 49.99, "max_single_module_current": 14.0},
            {"max_voltage": 178.0, "max_single_module_current": 0.0},
            {"max_voltage": 178.0, "max_single_module_current": 0.009},
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                self.assertIsNone(PIDZOOM_HW178P.effective_limits(config))

    def test_numeric_range_contains_only_finite_real_values(self) -> None:
        voltage = NumericRange(50.0, 178.0)
        self.assertTrue(voltage.contains(50.0))
        self.assertTrue(voltage.contains(178.0))
        for value in (49.999, 178.001, True, math.nan, math.inf, "84"):
            with self.subTest(value=value):
                self.assertFalse(voltage.contains(value))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
