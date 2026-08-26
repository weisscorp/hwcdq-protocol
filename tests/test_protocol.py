from __future__ import annotations

import ast
import math
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import hwcdq_protocol as protocol  # noqa: E402
from hwcdq_control.backend.simulator import (  # noqa: E402
    FakeTransport,
    SIMULATED_IDENTIFIER,
)


class EncoderVectorTests(unittest.TestCase):
    def test_empty_request_vectors(self) -> None:
        self.assertEqual(protocol.encode_get_firmware(), bytes.fromhex("02 01 01"))
        self.assertEqual(protocol.encode_get_serial(), bytes.fromhex("02 04 04"))
        self.assertEqual(protocol.encode_get_config(), bytes.fromhex("02 05 05"))
        self.assertEqual(protocol.encode_get_telemetry(), bytes.fromhex("02 06 06"))

    def test_apk_fallback_credential_vector(self) -> None:
        self.assertEqual(
            protocol.derive_password_credential(""),
            "D41D8CD98F00B204E9800998ECF8427E",
        )
        self.assertEqual(
            protocol.encode_check_password(""),
            bytes.fromhex(
                "23 02 44 34 31 44 38 43 44 39 38 46 30 30 42 32 30 34 "
                "45 39 38 30 30 39 39 38 45 43 46 38 34 32 37 45 00 45"
            ),
        )

    def test_plaintext_password_is_utf8_md5_lowercase_before_framing(self) -> None:
        self.assertEqual(
            protocol.derive_password_credential("test"),
            "098f6bcd4621d373cade4e832627b4f6",
        )
        self.assertEqual(
            protocol.encode_check_password("test"),
            bytes.fromhex(
                "23 02 30 39 38 66 36 62 63 64 34 36 32 31 64 33 37 33 "
                "63 61 64 65 34 65 38 33 32 36 32 37 62 34 66 36 00 CA"
            ),
        )

    def test_voltage_vectors(self) -> None:
        self.assertEqual(
            protocol.encode_set_voltage(84.0),
            bytes.fromhex("06 07 00 00 A8 42 F1"),
        )
        self.assertEqual(
            protocol.encode_set_voltage(48.0),
            bytes.fromhex("06 07 00 00 40 42 89"),
        )

    def test_current_vectors(self) -> None:
        self.assertEqual(
            protocol.encode_set_current(10.0),
            bytes.fromhex("06 08 00 00 20 41 69"),
        )
        self.assertEqual(
            protocol.encode_set_current(20.0),
            bytes.fromhex("06 08 00 00 A0 41 E9"),
        )

    def test_output_control_vectors(self) -> None:
        start = protocol.encode_start()
        stop = protocol.encode_stop()
        self.assertEqual(start, bytes.fromhex("06 0C 00 00 00 00 0C"))
        self.assertEqual(stop, bytes.fromhex("06 0C 01 00 00 00 0D"))
        self.assertEqual(protocol.decode_packet(start)["state"], 0)
        self.assertEqual(protocol.decode_packet(stop)["state"], 1)

    def test_general_packet_encoder(self) -> None:
        self.assertEqual(
            protocol.encode_packet(0x02, b"ABC\x00"),
            bytes.fromhex("06 02 41 42 43 00 C8"),
        )


class SimulatorOutputPolarityTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_control_requests_match_reported_telemetry(self) -> None:
        notifications: list[bytes] = []
        transport = FakeTransport()
        await transport.connect(SIMULATED_IDENTIFIER, lambda: None)
        self.addAsyncCleanup(transport.disconnect)
        await transport.start_notify("FFE2", notifications.append)

        await transport.write("FFE3", protocol.encode_start(), response=True)
        await transport.send_unsolicited_telemetry()
        started = protocol.decode_packet(
            next(
                packet
                for packet in reversed(notifications)
                if protocol.decode_packet(packet)["opcode"]
                == protocol.OP_GET_TELEMETRY
            )
        )["telemetry"]
        self.assertEqual(started["current_output"], 0)
        self.assertIs(started["output_enabled"], True)

        notifications.clear()
        await transport.write("FFE3", protocol.encode_stop(), response=True)
        await transport.send_unsolicited_telemetry()
        stopped = protocol.decode_packet(
            next(
                packet
                for packet in reversed(notifications)
                if protocol.decode_packet(packet)["opcode"]
                == protocol.OP_GET_TELEMETRY
            )
        )["telemetry"]
        self.assertEqual(stopped["current_output"], 1)
        self.assertIs(stopped["output_enabled"], False)


class ValidationTests(unittest.TestCase):
    def test_voltage_and_current_must_be_positive(self) -> None:
        for value in (0, -1, -0.001):
            with self.subTest(value=value):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_set_voltage(value)
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_set_current(value)

    def test_voltage_and_current_must_be_finite(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_set_voltage(value)
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_set_current(value)

    def test_voltage_and_current_must_fit_positive_float32(self) -> None:
        for value in (1e100, 1e-100):
            with self.subTest(value=value):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_set_voltage(value)
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_set_current(value)

    def test_bool_and_non_real_values_are_rejected(self) -> None:
        for value in (True, False, "84", None, complex(1, 0)):
            with self.subTest(value=value):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_set_voltage(value)  # type: ignore[arg-type]

    def test_opcode_validation(self) -> None:
        for opcode in (-1, 256, True, 1.5):
            with self.subTest(opcode=opcode):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_packet(opcode)  # type: ignore[arg-type]

    def test_payload_validation(self) -> None:
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode_packet(1, [1, 2])  # type: ignore[arg-type]
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode_packet(1, bytes(254))

    def test_password_validation(self) -> None:
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode_check_password("\ud800")
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode_check_password(1234)  # type: ignore[arg-type]

        # APK hashing consumes UTF-8 bytes before framing, so embedded NUL and
        # non-ASCII plaintext remain unambiguous fixed-size credentials.
        for password in ("12\x0034", "пароль"):
            with self.subTest(password=password):
                self.assertEqual(len(protocol.encode_check_password(password)), 36)

    def test_direct_credential_validation(self) -> None:
        self.assertEqual(
            protocol.encode_check_password_credential(
                protocol.APK_FALLBACK_CREDENTIAL
            ),
            protocol.encode_check_password(""),
        )
        for credential in ("", "0" * 31, "0" * 33, "g" * 32, "é" * 32):
            with self.subTest(credential=credential):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.encode_check_password_credential(credential)


class DecoderTests(unittest.TestCase):
    def test_confirmed_config_layout(self) -> None:
        # Synthetic parser vector, not a packet captured from a charger.
        raw = bytes.fromhex(
            "69 05 "
            "00 00 80 3F 00 00 00 40 00 00 40 40 00 00 80 40 01 "
            "00 00 A0 40 00 00 C0 40 00 00 E0 40 00 00 00 41 "
            "00 00 10 41 00 00 20 41 01 00 00 30 41 AA 01 BB 50 3C 64 "
            "41 42 43 44 45 46 47 48 49 4A 4B 4C 4D 4E 4F 50 51 52 "
            "53 54 55 56 57 01 00 00 40 41 00 00 50 41 01 CC 07 "
            "F4 01 E8 03 65 6E 00 00 00 00 00 00 DD EE FE FF 36"
        )
        decoded = protocol.decode_packet(raw)
        config = decoded["config"]
        self.assertEqual(len(decoded["payload"]), 103)
        self.assertEqual(config["target_voltage"], 1.0)
        self.assertEqual(config["target_current"], 2.0)
        self.assertEqual(config["offline_voltage"], 3.0)
        self.assertEqual(config["offline_current"], 4.0)
        self.assertEqual(config["power_on_output"], 1)
        self.assertEqual(config["voltage_calibration"], 5.0)
        self.assertEqual(config["voltage_feedback_calibration"], 6.0)
        self.assertEqual(config["current_calibration"], 7.0)
        self.assertEqual(config["current_feedback_calibration"], 8.0)
        self.assertEqual(config["max_voltage"], 9.0)
        self.assertEqual(config["max_single_module_current"], 10.0)
        self.assertEqual(config["auto_stop"], 1)
        self.assertEqual(config["shutdown_current"], 11.0)
        self.assertEqual(config["raw_u8_46"], 0xAA)
        self.assertEqual(config["temperature_protection"], 1)
        self.assertEqual(config["raw_u8_48"], 0xBB)
        self.assertEqual(config["protection_cutoff_temperature"], 80)
        self.assertEqual(config["fan_boost_temperature"], 60)
        self.assertEqual(config["fan_max_temperature"], 100)
        self.assertEqual(config["raw_ascii_23"], b"ABCDEFGHIJKLMNOPQRSTUVW")
        self.assertEqual(config["two_stage_charging"], 1)
        self.assertEqual(config["secondary_voltage"], 12.0)
        self.assertEqual(config["secondary_current"], 13.0)
        self.assertEqual(config["offline_control"], 1)
        self.assertEqual(config["raw_u8_85"], 0xCC)
        self.assertEqual(config["soft_start_coefficient"], 7)
        self.assertEqual(config["power_limit"], 500)
        self.assertEqual(config["max_power"], 1000)
        self.assertEqual(config["display_language_raw"], b"en\x00\x00\x00\x00\x00\x00")
        self.assertEqual(config["raw_u8_99"], 0xDD)
        self.assertEqual(config["raw_u8_100"], 0xEE)
        self.assertEqual(config["raw_u8_101"], 0xFE)
        self.assertEqual(config["raw_u8_102"], 0xFF)

    def test_config_unknown_length_falls_back_to_raw_payload(self) -> None:
        payload = bytes(102)
        decoded = protocol.decode_packet(protocol.encode_packet(0x05, payload))
        self.assertEqual(decoded["command"], "config_response")
        self.assertEqual(decoded["payload"], payload)
        self.assertNotIn("config", decoded)

    def test_decode_voltage(self) -> None:
        decoded = protocol.decode_packet(bytes.fromhex("06 07 00 00 A8 42 F1"))
        self.assertEqual(decoded["opcode"], 0x07)
        self.assertEqual(decoded["payload"], bytes.fromhex("00 00 A8 42"))
        self.assertEqual(decoded["command"], "set_voltage")
        self.assertEqual(decoded["volts"], 84.0)
        self.assertTrue(decoded["checksum_valid"])

    def test_decode_output_control(self) -> None:
        start = protocol.decode_packet(protocol.encode_start())
        stop = protocol.decode_packet(protocol.encode_stop())
        self.assertEqual(start["state"], 0)
        self.assertIs(start["state_valid"], True)
        self.assertIs(start["enabled"], True)
        self.assertEqual(stop["state"], 1)
        self.assertIs(stop["state_valid"], True)
        self.assertIs(stop["enabled"], False)

    def test_decode_output_control_unknown_state_fails_closed(self) -> None:
        for state in (-1, 2, 2**31 - 1):
            with self.subTest(state=state):
                packet = protocol.encode_packet(
                    protocol.OP_OUTPUT_CONTROL,
                    state.to_bytes(4, byteorder="little", signed=True),
                )
                decoded = protocol.decode_packet(packet)
                self.assertEqual(decoded["state"], state)
                self.assertIs(decoded["state_valid"], False)
                self.assertIsNone(decoded["enabled"])

    def test_decode_direction_neutral_ack(self) -> None:
        success = protocol.decode_packet(bytes.fromhex("03 07 01 08"))
        failure = protocol.decode_packet(bytes.fromhex("03 08 00 08"))
        request = protocol.decode_packet(protocol.encode_get_config())
        self.assertIs(success["acknowledged"], True)
        self.assertIs(failure["acknowledged"], False)
        self.assertNotIn("acknowledged", request)

    def test_one_byte_unknown_or_read_response_is_not_invented_as_ack(self) -> None:
        unknown = protocol.decode_packet(protocol.encode_packet(0xE1, b"\x01"))
        firmware = protocol.decode_packet(protocol.encode_packet(0x01, b"\x01"))
        self.assertNotIn("acknowledged", unknown)
        self.assertNotIn("acknowledged", firmware)

    def test_decode_auth_credential_does_not_create_visible_secret_field(self) -> None:
        raw = protocol.encode_check_password("тест")
        decoded = protocol.decode_packet(raw)
        self.assertEqual(decoded["command"], "check_password")
        self.assertTrue(decoded["credential_format_valid"])
        self.assertNotIn("password", decoded)
        self.assertNotIn("credential", decoded)
        self.assertEqual(
            decoded["payload"],
            protocol.derive_password_credential("тест").encode("ascii") + b"\x00",
        )

    def test_unknown_opcode_preserves_payload(self) -> None:
        raw = bytes.fromhex("06 E1 DE AD BE EF 19")
        decoded = protocol.decode_packet(raw)
        self.assertEqual(decoded["opcode"], 0xE1)
        self.assertEqual(decoded["payload"], bytes.fromhex("DE AD BE EF"))
        self.assertEqual(decoded["command"], "unknown")

    def test_confirmed_telemetry_layout_and_derived_power(self) -> None:
        # Synthetic parser vector built from the independently recovered layout;
        # it is explicitly not a packet captured from a charger.
        raw = bytes.fromhex(
            "30 06 "
            "00 00 80 3F 00 00 00 40 00 00 40 40 "
            "00 00 80 40 00 00 A0 40 00 00 C0 40 "
            "00 00 E0 40 00 00 00 41 00 00 10 41 "
            "A5 00 00 20 41 00 00 30 41 5A A8"
        )
        decoded = protocol.decode_packet(raw)
        payload = decoded["payload"]
        telemetry = decoded["telemetry"]
        self.assertEqual(len(payload), 46)
        self.assertEqual(telemetry["input_voltage"], 1.0)
        self.assertEqual(telemetry["input_current"], 2.0)
        self.assertEqual(telemetry["input_frequency"], 3.0)
        self.assertEqual(telemetry["temperature_1"], 4.0)
        self.assertEqual(telemetry["temperature_2"], 5.0)
        self.assertEqual(telemetry["output_voltage"], 6.0)
        self.assertEqual(telemetry["output_current"], 7.0)
        self.assertEqual(telemetry["current_point"], 8.0)
        self.assertEqual(telemetry["efficiency"], 9.0)
        self.assertEqual(telemetry["current_output"], 0xA5)
        self.assertIsNone(telemetry["output_enabled"])
        self.assertEqual(telemetry["accumulated_capacity_ah"], 10.0)
        self.assertEqual(telemetry["accumulated_energy_wh"], 11.0)
        self.assertEqual(telemetry["module_count"], 0x5A)
        self.assertEqual(telemetry["input_power_w"], 2.0)
        self.assertEqual(telemetry["output_power_w"], 42.0)

    def test_live_telemetry_output_status_and_strict_unknown_mapping(self) -> None:
        # Captured from the owned charger on 2026-08-25 while its display and
        # operator both confirmed that output was OFF.  The frame checksum is
        # the original on-wire checksum, not regenerated by this test.
        off_frame = bytes.fromhex(
            "30 06 00 30 5D 43 00 00 00 00 00 85 48 42 "
            "00 CD 06 42 00 00 18 42 8D 6A 82 40 00 00 "
            "00 00 00 00 00 00 00 00 00 00 01 00 00 00 "
            "00 00 00 00 00 01 0F"
        )
        self.assertTrue(protocol.verify_checksum(off_frame))
        off = protocol.decode_packet(off_frame)["telemetry"]
        self.assertEqual(off["current_output"], 1)
        self.assertIs(off["output_enabled"], False)

        payload = bytearray(protocol.decode_packet(off_frame)["payload"])
        payload[36] = 0
        on = protocol.decode_packet(
            protocol.encode_packet(protocol.OP_GET_TELEMETRY, payload)
        )["telemetry"]
        self.assertEqual(on["current_output"], 0)
        self.assertIs(on["output_enabled"], True)

        payload[36] = 2
        unknown = protocol.decode_packet(
            protocol.encode_packet(protocol.OP_GET_TELEMETRY, payload)
        )["telemetry"]
        self.assertEqual(unknown["current_output"], 2)
        self.assertIsNone(unknown["output_enabled"])

    def test_sanitized_live_incident_proves_flag_zero_energizes_output(self) -> None:
        # Sanitized 2026-08-25 incident facts only: the measurements, device
        # identity, timestamps, and full debug log are intentionally omitted.
        pre_payload = bytearray(46)
        pre_payload[36] = 1
        pre = protocol.decode_packet(
            protocol.encode_packet(protocol.OP_GET_TELEMETRY, pre_payload)
        )["telemetry"]

        request = protocol.decode_packet(bytes.fromhex("06 0C 00 00 00 00 0C"))

        post_payload = bytearray(46)
        post_payload[36] = 0
        post = protocol.decode_packet(
            protocol.encode_packet(protocol.OP_GET_TELEMETRY, post_payload)
        )["telemetry"]

        self.assertIs(pre["output_enabled"], False)
        self.assertEqual(request["state"], 0)
        self.assertIs(request["enabled"], True)
        self.assertIs(post["output_enabled"], True)

    def test_truncated_packet_is_rejected(self) -> None:
        for raw in (b"", b"\x02", b"\x02\x04"):
            with self.subTest(raw=raw):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.decode_packet(raw)
                self.assertFalse(protocol.verify_checksum(raw))

    def test_bad_declared_length_is_rejected(self) -> None:
        for raw in (
            bytes.fromhex("00 04 04"),
            bytes.fromhex("01 04 04"),
            bytes.fromhex("03 04 04"),
            bytes.fromhex("02 04 00 04"),
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.decode_packet(raw)
                self.assertFalse(protocol.verify_checksum(raw))

    def test_bad_checksum_is_rejected(self) -> None:
        raw = bytes.fromhex("02 04 05")
        with self.assertRaisesRegex(protocol.ProtocolError, "checksum mismatch"):
            protocol.decode_packet(raw)
        self.assertFalse(protocol.verify_checksum(raw))

    def test_non_bytes_input_is_rejected(self) -> None:
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode_packet([2, 4, 4])  # type: ignore[arg-type]
        self.assertFalse(protocol.verify_checksum([2, 4, 4]))  # type: ignore[arg-type]

    def test_checksum_accepts_mutable_bytes_like_inputs(self) -> None:
        raw = bytes.fromhex("02 04 04")
        self.assertTrue(protocol.verify_checksum(raw))
        self.assertTrue(protocol.verify_checksum(bytearray(raw)))
        self.assertTrue(protocol.verify_checksum(memoryview(raw)))


class OfflineBoundaryTests(unittest.TestCase):
    def test_codec_has_no_network_ble_or_io_imports(self) -> None:
        source_path = REPOSITORY_ROOT / "tools" / "hwcdq_protocol.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        forbidden = {
            "asyncio",
            "bleak",
            "bluetooth",
            "http",
            "io",
            "os",
            "pathlib",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        self.assertEqual(imported_roots & forbidden, set())

        forbidden_calls = {"open", "exec", "eval", "compile", "__import__"}
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(called_names & forbidden_calls, set())


if __name__ == "__main__":
    unittest.main()
