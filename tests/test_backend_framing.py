from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from hwcdq_control.backend import (  # noqa: E402
    FrameAssembler,
    FrameStreamError,
    GattCharacteristic,
    GattService,
    GattTopologyError,
    chunks_for_write,
    resolve_wnr_chunk_size,
    select_hwcdq_topology,
    short_uuid,
)
from hwcdq_control.backend.redaction import (  # noqa: E402
    REDACTED,
    format_packet,
    redact_text,
    redact_value,
)
from tools import hwcdq_protocol as protocol  # noqa: E402


class FrameAssemblerTests(unittest.TestCase):
    def test_partial_and_concatenated_frames(self) -> None:
        first = protocol.encode_get_config()
        second = protocol.encode_get_telemetry()
        assembler = FrameAssembler()

        self.assertEqual(assembler.feed(first[:1]), ())
        self.assertEqual(assembler.feed(first[1:] + second), (first, second))
        self.assertEqual(assembler.buffered_bytes, 0)

    def test_corrupt_frame_fails_closed_and_clears_buffer(self) -> None:
        assembler = FrameAssembler()
        bad = bytearray(protocol.encode_get_config())
        bad[-1] ^= 1
        with self.assertRaises(FrameStreamError):
            assembler.feed(bad + protocol.encode_get_telemetry())
        self.assertEqual(assembler.buffered_bytes, 0)

    def test_invalid_length_fails_closed(self) -> None:
        assembler = FrameAssembler()
        for malformed in (b"\x00", b"\x01"):
            with self.subTest(malformed=malformed):
                with self.assertRaises(FrameStreamError):
                    assembler.feed(malformed)
                self.assertEqual(assembler.buffered_bytes, 0)


class GattSelectionTests(unittest.TestCase):
    @staticmethod
    def service(
        service_uuid: str = "0000ffe1-0000-1000-8000-00805f9b34fb",
        *,
        rx_properties: set[str] | None = None,
        tx_properties: set[str] | None = None,
        maximum: int | None = None,
    ) -> GattService:
        return GattService(
            service_uuid,
            [
                GattCharacteristic(
                    "0000ffe2-0000-1000-8000-00805f9b34fb",
                    (
                        {
                            "indicate",
                            "notify",
                            "read",
                            "write",
                            "write-without-response",
                        }
                        if rx_properties is None
                        else rx_properties
                    ),
                    253,
                ),
                GattCharacteristic(
                    "0000ffe3-0000-1000-8000-00805f9b34fb",
                    (
                        {"write", "write-without-response"}
                        if tx_properties is None
                        else tx_properties
                    ),
                    253 if maximum is None else maximum,
                ),
            ],
        )

    def test_full_and_short_uuid_forms_are_equivalent(self) -> None:
        self.assertEqual(
            short_uuid("0000FFE2-0000-1000-8000-00805F9B34FB"),
            "ffe2",
        )
        topology = select_hwcdq_topology([self.service()])
        self.assertEqual(
            topology.service_uuid,
            "0000ffe1-0000-1000-8000-00805f9b34fb",
        )
        self.assertEqual(
            topology.rx_uuid,
            "0000ffe2-0000-1000-8000-00805f9b34fb",
        )
        self.assertEqual(
            topology.tx_uuid,
            "0000ffe3-0000-1000-8000-00805f9b34fb",
        )
        self.assertFalse(topology.write_with_response)
        self.assertEqual(topology.wnr_chunk_size, 253)

    def test_wnr_is_preferred_when_both_write_modes_are_available(self) -> None:
        topology = select_hwcdq_topology(
            [self.service(tx_properties={"write", "write-without-response"})]
        )
        self.assertFalse(topology.write_with_response)

    def test_confirmed_write_is_fallback_when_wnr_is_unavailable(self) -> None:
        topology = select_hwcdq_topology(
            [self.service(tx_properties={"write"}, maximum=61)]
        )
        self.assertTrue(topology.write_with_response)

    def test_wnr_uses_advertised_chunk_size(self) -> None:
        topology = select_hwcdq_topology(
            [self.service(tx_properties={"write-without-response"}, maximum=61)]
        )
        self.assertFalse(topology.write_with_response)
        self.assertEqual(topology.wnr_chunk_size, 61)

    def test_ffe0_only_is_rejected(self) -> None:
        with self.assertRaises(GattTopologyError):
            select_hwcdq_topology([self.service("FFE0")])

    def test_missing_and_duplicate_ffe1_service_instances_are_rejected(self) -> None:
        with self.assertRaisesRegex(GattTopologyError, "no FFE1 service"):
            select_hwcdq_topology([])
        with self.assertRaises(GattTopologyError):
            select_hwcdq_topology(
                [
                    self.service("FFE1"),
                    self.service("0000FFE1-0000-1000-8000-00805F9B34FB"),
                ]
            )

    def test_missing_or_duplicate_characteristics_are_rejected(self) -> None:
        live = self.service()
        rx, tx = live.characteristics
        for characteristics in ([tx], [rx], [rx, rx, tx], [rx, tx, tx]):
            with self.subTest(characteristics=characteristics):
                with self.assertRaises(GattTopologyError):
                    select_hwcdq_topology([GattService(live.uuid, characteristics)])

    def test_indicate_only_rx_is_rejected(self) -> None:
        with self.assertRaisesRegex(GattTopologyError, "does not advertise notify"):
            select_hwcdq_topology(
                [self.service(rx_properties={"indicate"})]
            )

    def test_unwritable_tx_is_rejected(self) -> None:
        with self.assertRaisesRegex(GattTopologyError, "FFE3 is not writable"):
            select_hwcdq_topology(
                [self.service(tx_properties={"read"})]
            )

    def test_wnr_limit_fallback_and_chunks(self) -> None:
        self.assertEqual(resolve_wnr_chunk_size(None), 20)
        self.assertEqual(resolve_wnr_chunk_size(0), 20)
        self.assertEqual(resolve_wnr_chunk_size(513), 20)
        self.assertEqual(
            chunks_for_write(
                b"abcdefg",
                write_with_response=False,
                wnr_chunk_size=3,
            ),
            (b"abc", b"def", b"g"),
        )
        self.assertEqual(
            chunks_for_write(
                b"abcdefg",
                write_with_response=True,
                wnr_chunk_size=3,
            ),
            (b"abcdefg",),
        )

    def test_known_packets_fit_or_split_at_the_advertised_wnr_limit(self) -> None:
        packets = (
            protocol.encode_get_firmware(),
            protocol.encode_get_serial(),
            protocol.encode_get_config(),
            protocol.encode_get_telemetry(),
            protocol.encode_check_password(),
            protocol.encode_set_voltage(84.0),
            protocol.encode_set_current(3.0),
            protocol.encode_start(),
            protocol.encode_stop(),
        )
        for limit in (20, 253):
            for packet in packets:
                with self.subTest(limit=limit, packet=packet):
                    chunks = chunks_for_write(
                        packet,
                        write_with_response=False,
                        wnr_chunk_size=limit,
                    )
                    self.assertEqual(b"".join(chunks), packet)
                    self.assertTrue(all(0 < len(chunk) <= limit for chunk in chunks))


class RedactionTests(unittest.TestCase):
    def test_password_packet_hides_payload_and_checksum(self) -> None:
        packet = protocol.encode_check_password("never-log-this")
        rendered = format_packet(packet)
        self.assertEqual(rendered, f"{packet[0]:02X} 02 {REDACTED}")
        self.assertNotIn("never", rendered)
        self.assertNotIn(f"{packet[-1]:02X}", rendered.split()[-1])

    def test_non_secret_packet_remains_available_for_diagnostics(self) -> None:
        packet = protocol.encode_set_voltage(84.0)
        self.assertEqual(format_packet(packet), packet.hex(" ").upper())

    def test_recursive_and_text_redaction(self) -> None:
        value = {
            "password": "hello",
            "nested": {"cloudToken": "abc", "safe": 3},
        }
        self.assertEqual(redact_value(value)["password"], REDACTED)
        self.assertEqual(redact_value(value)["nested"]["cloudToken"], REDACTED)
        self.assertEqual(redact_value(value)["nested"]["safe"], 3)
        text = "failure hello / 68 65 6c 6c 6f / 68656C6C6F"
        rendered = redact_text(text, ["hello"])
        self.assertNotIn("hello", rendered)
        self.assertNotIn("68 65 6c 6c 6f", rendered)
        self.assertNotIn("68656C6C6F", rendered)

    def test_non_ascii_python_bytes_repr_is_redacted(self) -> None:
        secret = "пароль"
        leaked = f"native error echoed {secret.encode('utf-8')!r}"
        rendered = redact_text(leaked, [secret])
        self.assertEqual(rendered, f"native error echoed {REDACTED}")
        self.assertNotIn("\\xd0", rendered.lower())


if __name__ == "__main__":
    unittest.main()
