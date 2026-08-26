from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contract" / "v1"
sys.path.insert(0, str(ROOT / "packages" / "hwcdq-client" / "src"))

import hwcdq  # noqa: E402
from hwcdq.framing import FrameAssembler  # noqa: E402
from hwcdq.errors import FrameStreamError  # noqa: E402
from tools import validate_contract as contract_validator  # noqa: E402


EXPECTED_PUBLIC_API = (
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
)


def contract_json(relative: str) -> dict[str, Any]:
    return json.loads((CONTRACT / relative).read_text(encoding="utf-8"))


def token_number(token: dict[str, Any]) -> object:
    kind = token.get("kind", "decimal")
    if kind == "nan":
        return math.nan
    if kind == "positive_infinity":
        return math.inf
    if kind == "negative_infinity":
        return -math.inf
    if kind == "boolean":
        return token["value"]
    return float(token["decimal"])


def resolve_path(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        current = current[part]
    return current


def python_encoder(case: dict[str, Any]) -> bytes:
    operation = case["operation"]
    arguments = case["arguments"]
    if operation == "encode_authentication_apk_fallback":
        return hwcdq.encode_check_password_credential(hwcdq.APK_FALLBACK_CREDENTIAL)
    if operation == "encode_authentication_credential":
        credential = hwcdq.Credential.from_digest(arguments["credential"])
        return hwcdq.encode_check_password_credential(credential._wire_value())
    functions = {
        "encode_get_firmware": hwcdq.encode_get_firmware,
        "encode_get_serial": hwcdq.encode_get_serial,
        "encode_get_config": hwcdq.encode_get_config,
        "encode_get_telemetry": hwcdq.encode_get_telemetry,
        "encode_set_voltage": hwcdq.encode_set_voltage,
        "encode_set_current": hwcdq.encode_set_current,
        "encode_start": hwcdq.encode_start,
        "encode_stop": hwcdq.encode_stop,
    }
    function = functions[operation]
    if "value" in arguments:
        return function(float(arguments["value"]["decimal"]))
    return function()


class ContractValidatorTests(unittest.TestCase):
    def test_stdlib_validator_accepts_exact_manifest_and_semantics(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "validate_contract.py"), "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "HWCDQ contract 1.0.0: OK")

    def test_evidence_validator_rejects_a_missing_markdown_anchor(self) -> None:
        documents = {
            "synthetic.json": {
                "evidence": ["docs/protocol.md#this-anchor-does-not-exist"]
            }
        }
        with self.assertRaises(contract_validator.ContractValidationError):
            contract_validator.validate_evidence_references(ROOT, documents)

    def test_existing_public_python_api_is_unchanged(self) -> None:
        self.assertEqual(tuple(hwcdq.__all__), EXPECTED_PUBLIC_API)
        self.assertNotIn("encode_packet", hwcdq.__all__)

    def test_profile_and_gatt_declarations_match_public_objects(self) -> None:
        profile = contract_json("profiles/hw178p.json")
        gatt = contract_json("gatt.json")
        runtime = hwcdq.PIDZOOM_HW178P
        self.assertEqual(runtime.model, profile["model"])
        self.assertEqual(runtime.display_name, profile["display_name"])
        self.assertEqual(runtime.voltage.minimum, float(profile["voltage"]["minimum"]["decimal"]))
        self.assertEqual(runtime.voltage.maximum, float(profile["voltage"]["maximum"]["decimal"]))
        self.assertEqual(runtime.current.minimum, float(profile["current"]["minimum"]["decimal"]))
        self.assertEqual(runtime.current.maximum, float(profile["current"]["maximum"]["decimal"]))
        self.assertEqual(runtime.gatt.service_uuid.casefold(), gatt["service"]["short_uuid"])
        self.assertEqual(runtime.gatt.rx_uuid.casefold(), gatt["rx"]["short_uuid"])
        self.assertEqual(runtime.gatt.tx_uuid.casefold(), gatt["tx"]["short_uuid"])


class CodecConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.vectors = contract_json("vectors/codec.json")

    def test_every_shared_named_encoder_matches_exact_bytes(self) -> None:
        for case in self.vectors["encode_cases"]:
            with self.subTest(case=case["id"]):
                actual = python_encoder(case)
                self.assertEqual(actual.hex(), case["expected"]["frame_hex"])
                self.assertTrue(hwcdq.verify_checksum(actual))
                decoded = hwcdq.decode_packet(actual)
                self.assertEqual(decoded["opcode"], case["expected"]["opcode"])
                self.assertEqual(decoded["payload"].hex(), case["expected"]["payload_hex"])
                self.assertEqual(f"{decoded['checksum']:02x}", case["expected"]["checksum_hex"])

    def test_float_arguments_match_exact_binary32(self) -> None:
        for case in self.vectors["encode_cases"]:
            value = case["arguments"].get("value")
            if value is None:
                continue
            with self.subTest(case=case["id"]):
                self.assertEqual(struct.pack("<f", float(value["decimal"])).hex(), value["f32le_hex"])

    def test_credential_canonicalization(self) -> None:
        for case in self.vectors["credential_cases"]:
            with self.subTest(case=case["id"]):
                operation = case["operation"]
                if operation == "apk_fallback_credential":
                    actual = hwcdq.Credential.apk_fallback()
                else:
                    actual = hwcdq.Credential.from_digest(case["arguments"]["digest"])
                self.assertEqual(actual._wire_value(), case["expected"]["credential_text"])
                if "frame_hex" in case["expected"]:
                    self.assertEqual(
                        hwcdq.encode_check_password_credential(actual._wire_value()).hex(),
                        case["expected"]["frame_hex"],
                    )

    def test_python_legacy_password_api_remains_compatible_but_is_not_native_contract(self) -> None:
        for case in self.vectors["python_legacy_cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["implementations"], ["python"])
                self.assertIs(case["shared_native_requirement"], False)
                password = case["arguments"]["password"]
                self.assertEqual(hwcdq.derive_password_credential(password), case["expected"]["credential_text"])
                self.assertEqual(hwcdq.encode_check_password(password).hex(), case["expected"]["frame_hex"])


class DecodeConformanceTests(unittest.TestCase):
    def test_all_decode_vectors(self) -> None:
        for case in contract_json("vectors/decode.json")["decode_cases"]:
            with self.subTest(case=case["id"]):
                packet = bytes.fromhex(case["packet_hex"])
                decoded = hwcdq.decode_packet(packet)
                expected = case["expected"]
                self.assertEqual(decoded["opcode"], expected["opcode"])
                self.assertEqual(decoded["command"], expected["command"])
                self.assertEqual(len(decoded["payload"]), expected["payload_length"])
                self.assertTrue(decoded["checksum_valid"])
                for field in expected["fields"]:
                    actual = resolve_path(decoded, field["path"])
                    kind = field["type"]
                    if kind == "f32":
                        self.assertEqual(struct.pack("<f", actual).hex(), field["f32le_hex"])
                    elif kind == "bytes":
                        self.assertEqual(actual.hex(), field["hex"])
                    elif kind == "number":
                        self.assertEqual(actual, float(field["decimal"]))
                    else:
                        self.assertEqual(actual, field["value"])

    def test_derived_power_uses_promoted_binary32_operands_and_binary64_multiply(self) -> None:
        case = next(
            item
            for item in contract_json("vectors/decode.json")["decode_cases"]
            if item["id"] == "telemetry_binary64_derived_power"
        )
        telemetry = hwcdq.decode_packet(bytes.fromhex(case["packet_hex"]))["telemetry"]
        expected = {field["path"]: field for field in case["expected"]["fields"]}
        for path in ("telemetry.input_power_w", "telemetry.output_power_w"):
            name = path.split(".", 1)[1]
            binary64_product = telemetry[name]
            binary32_product = struct.unpack("<f", struct.pack("<f", binary64_product))[0]
            self.assertEqual(binary64_product, float(expected[path]["decimal"]))
            self.assertNotEqual(binary64_product, binary32_product)


class InvalidConformanceTests(unittest.TestCase):
    @staticmethod
    def classify(case: dict[str, Any], exception: hwcdq.ProtocolError) -> str:
        operation = case["operation"]
        message = str(exception)
        if operation == "decode_packet":
            if "truncated" in message:
                return "packet.truncated"
            if "length byte" in message:
                return "packet.length.minimum"
            if "length mismatch" in message:
                return "packet.length.mismatch"
            if "checksum mismatch" in message:
                return "packet.checksum.mismatch"
        if operation in {"encode_set_voltage", "encode_set_current"}:
            if "real number" in message:
                return "scalar.type"
            if "must be finite" in message:
                return "scalar.non_finite"
            if "greater than zero" in message:
                return "scalar.non_positive"
            if "not representable" in message:
                return "scalar.not_float32"
        if operation == "encode_authentication_credential":
            arguments = case["arguments"]
            if arguments.get("credential_kind") == "integer":
                return "credential.type"
            encoded = arguments["credential"].encode("utf-8")
            return "credential.length" if len(encoded) != 32 else "credential.non_hex"
        raise AssertionError(f"unmapped ProtocolError for {case['id']}: {message}")

    def test_invalid_vectors_map_runtime_errors_to_stable_codes(self) -> None:
        for case in contract_json("vectors/invalid.json")["invalid_cases"]:
            with self.subTest(case=case["id"]):
                operation = case["operation"]
                arguments = case["arguments"]
                with self.assertRaises(hwcdq.ProtocolError) as caught:
                    if operation == "decode_packet":
                        hwcdq.decode_packet(bytes.fromhex(arguments["packet_hex"]))
                    elif operation == "encode_set_voltage":
                        hwcdq.encode_set_voltage(token_number(arguments["value"]))
                    elif operation == "encode_set_current":
                        hwcdq.encode_set_current(token_number(arguments["value"]))
                    elif operation == "encode_authentication_credential":
                        value: object = arguments.get("credential", arguments.get("credential_value"))
                        hwcdq.encode_check_password_credential(value)  # type: ignore[arg-type]
                    else:
                        self.fail(f"unsupported invalid operation: {operation}")
                self.assertEqual(self.classify(case, caught.exception), case["expected_code"])


class FramingConformanceTests(unittest.TestCase):
    @staticmethod
    def stream_code(exception: FrameStreamError) -> str:
        return "stream.length.invalid" if "invalid frame length" in str(exception) else "stream.frame.invalid"

    def test_incremental_stream_vectors(self) -> None:
        for case in contract_json("vectors/framing.json")["cases"]:
            with self.subTest(case=case["id"]):
                assembler = FrameAssembler(maximum_frame_size=case.get("maximum_frame_size", 256))
                frames: list[bytes] = []
                caught: FrameStreamError | None = None
                try:
                    for chunk in case["chunks_hex"]:
                        frames.extend(assembler.feed(bytes.fromhex(chunk)))
                except FrameStreamError as exception:
                    caught = exception
                    frames.clear()
                expected_code = case.get("expected_code")
                if expected_code is None:
                    self.assertIsNone(caught)
                else:
                    self.assertIsNotNone(caught)
                    self.assertEqual(self.stream_code(caught), expected_code)  # type: ignore[arg-type]
                self.assertEqual([value.hex() for value in frames], case["expected_frames_hex"])
                self.assertEqual(assembler.buffered_bytes, case["expected_buffered_bytes"])
                if "post_error_chunks_hex" in case:
                    recovered: list[bytes] = []
                    for chunk in case["post_error_chunks_hex"]:
                        recovered.extend(assembler.feed(bytes.fromhex(chunk)))
                    self.assertEqual([value.hex() for value in recovered], case["post_error_frames_hex"])


class ProfileConformanceTests(unittest.TestCase):
    def test_contains_vectors_use_wire_canonical_binary32(self) -> None:
        profile = hwcdq.PIDZOOM_HW178P
        for case in contract_json("vectors/profile.json")["contains_cases"]:
            with self.subTest(case=case["id"]):
                value = token_number(case["value"])
                numeric_range = profile.voltage if case["quantity"] == "voltage" else profile.current
                self.assertIs(numeric_range.contains(value), case["accepted"])

    def test_effective_device_limits_fail_closed_and_never_expand_profile(self) -> None:
        profile = hwcdq.PIDZOOM_HW178P
        for case in contract_json("vectors/profile.json")["effective_limits_cases"]:
            with self.subTest(case=case["id"]):
                config = {key: token_number(value) for key, value in case["config"].items()}
                actual = profile.effective_limits(config)
                expected = case["expected"]
                if expected is None:
                    self.assertIsNone(actual)
                    continue
                self.assertIsNotNone(actual)
                self.assertEqual(actual.voltage.minimum, float(expected["voltage_minimum"]))
                self.assertEqual(actual.voltage.maximum, float(expected["voltage_maximum"]))
                self.assertEqual(actual.current.minimum, float(expected["current_minimum"]))
                self.assertEqual(actual.current.maximum, float(expected["current_maximum"]))


if __name__ == "__main__":
    unittest.main()
