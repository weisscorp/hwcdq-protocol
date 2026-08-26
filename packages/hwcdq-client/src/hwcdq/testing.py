"""Deterministic in-process HWCDQ peripheral for UI and backend testing."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Any

from .framing import FrameAssembler
from .models import DeviceAdvertisement, GattCharacteristic, GattService
from . import protocol as codec
from .profile import PIDZOOM_HW178P
from .redaction import format_packet, is_password_packet
from .transport import (
    DisconnectHandler,
    NotificationHandler,
    _validated_scan_duration,
)


SIMULATED_IDENTIFIER = "HWCDQ-SIMULATOR"


@dataclass(frozen=True, slots=True)
class SimulatedWriteRecord:
    opcode: int
    display: str
    response: bool
    chunk_count: int
    raw: bytes | None


class FakeScanner:
    """Return a stable simulated charger without touching Bluetooth."""

    def __init__(self, devices: list[DeviceAdvertisement] | None = None) -> None:
        self.devices = devices or [
            DeviceAdvertisement(
                identifier=SIMULATED_IDENTIFIER,
                name="HWCDQBLE_NIUB (симулятор)",
                rssi=-42,
                service_uuids=("FFE1",),
                manufacturer_data={},
            )
        ]

    async def scan(self, duration: float, callback=None):  # type: ignore[no-untyped-def]
        _validated_scan_duration(duration)
        await asyncio.sleep(0)
        result = tuple(self.devices)
        if callback is not None:
            for device in result:
                callback(device)
        return result


class FakeTransport:
    """A protocol-faithful transport with configurable deterministic faults."""

    def __init__(
        self,
        *,
        expected_password: str = "",
        write_properties: tuple[str, ...] = ("write", "write-without-response"),
        max_write_without_response_size: int | None = 253,
        notification_fragment_size: int | None = None,
        response_delay: float = 0.0,
    ) -> None:
        # Retain only the derived simulator comparison value, never the
        # caller's synthetic plaintext password.
        self._expected_credential = codec.derive_password_credential(
            expected_password
        )
        self.response_delay = response_delay
        self.notification_fragment_size = notification_fragment_size
        self.services: tuple[GattService, ...] = (
            GattService(
                "0000FFE1-0000-1000-8000-00805F9B34FB",
                [
                    GattCharacteristic(
                        "FFE2",
                        {
                            "indicate",
                            "notify",
                            "read",
                            "write",
                            "write-without-response",
                        },
                        253,
                    ),
                    GattCharacteristic(
                        "FFE3",
                        set(write_properties),
                        max_write_without_response_size,
                    ),
                ],
            ),
        )
        self._connected = False
        self.identifier: str | None = None
        self._disconnect_callback: DisconnectHandler | None = None
        self._notification_callback: NotificationHandler | None = None
        self._tx_assembler = FrameAssembler()
        self._frame_chunk_count = 0
        self._tasks: set[asyncio.Task[None]] = set()

        self.target_voltage = 84.0
        self.target_current = 10.0
        self.max_voltage = PIDZOOM_HW178P.voltage.maximum
        self.max_single_module_current = PIDZOOM_HW178P.current.maximum
        self.output_enabled = False
        self.firmware = b"SIM-1.0\x00"
        self.serial_number = b"HWCDQ-SIM-0001\x00"

        self.drop_responses: set[int] = set()
        self.reject_opcodes: set[int] = set()
        self.disconnect_on_opcodes: set[int] = set()
        self.mismatch_readback: set[str] = set()
        self.write_records: list[SimulatedWriteRecord] = []

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(
        self,
        identifier: str,
        disconnected_callback: DisconnectHandler,
    ) -> None:
        if identifier != SIMULATED_IDENTIFIER:
            raise LookupError(f"unknown simulated device {identifier!r}")
        await asyncio.sleep(0)
        self.identifier = identifier
        self._disconnect_callback = disconnected_callback
        self._connected = True

    async def disconnect(self) -> None:
        was_connected = self._connected
        self._connected = False
        self._notification_callback = None
        self._tx_assembler.reset()
        for task in tuple(self._tasks):
            task.cancel()
        self._tasks.clear()
        await asyncio.sleep(0)
        if was_connected and self._disconnect_callback is not None:
            self._disconnect_callback()

    async def discover_gatt(self) -> tuple[GattService, ...]:
        self._require_connected()
        await asyncio.sleep(0)
        return self.services

    async def start_notify(
        self,
        characteristic_uuid: str,
        callback: NotificationHandler,
    ) -> None:
        self._require_connected()
        if characteristic_uuid.lower() not in {
            "ffe2",
            "0000ffe2-0000-1000-8000-00805f9b34fb",
        }:
            raise LookupError("simulator notifications are only available on FFE2")
        self._notification_callback = callback
        await asyncio.sleep(0)

    async def stop_notify(self, characteristic_uuid: str) -> None:
        self._require_connected()
        self._notification_callback = None
        await asyncio.sleep(0)

    async def write(
        self,
        characteristic_uuid: str,
        data: bytes,
        *,
        response: bool,
    ) -> None:
        self._require_connected()
        if characteristic_uuid.lower() not in {
            "ffe3",
            "0000ffe3-0000-1000-8000-00805f9b34fb",
        }:
            raise LookupError("simulator accepts writes only on FFE3")
        self._frame_chunk_count += 1
        frames = self._tx_assembler.feed(data)
        for frame in frames:
            decoded = codec.decode_packet(frame)
            secret = is_password_packet(frame)
            self.write_records.append(
                SimulatedWriteRecord(
                    opcode=int(decoded["opcode"]),
                    display=format_packet(frame),
                    response=response,
                    chunk_count=self._frame_chunk_count,
                    raw=None if secret else frame,
                )
            )
            self._frame_chunk_count = 0
            reply = self._handle_request(decoded)
            opcode = int(decoded["opcode"])
            if opcode in self.disconnect_on_opcodes:
                self._connected = False
                callback = self._disconnect_callback
                if callback is not None:
                    callback()
                continue
            if reply is not None and opcode not in self.drop_responses:
                task = asyncio.create_task(self._deliver(reply))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        await asyncio.sleep(0)

    async def send_unsolicited_telemetry(self) -> None:
        self._require_connected()
        await self._deliver(
            codec.encode_packet(codec.OP_GET_TELEMETRY, self._telemetry_payload())
        )

    def count_opcode(self, opcode: int) -> int:
        return sum(record.opcode == opcode for record in self.write_records)

    def _handle_request(self, decoded: dict[str, Any]) -> bytes | None:
        opcode = int(decoded["opcode"])
        payload = bytes(decoded["payload"])
        if opcode == codec.OP_CHECK_PASSWORD:
            supplied: str | None = None
            if payload.endswith(b"\x00"):
                try:
                    supplied = payload[:-1].decode("utf-8")
                except UnicodeDecodeError:
                    supplied = None
            accepted = supplied == self._expected_credential
            return codec.encode_packet(opcode, bytes((int(accepted),)))

        if opcode == codec.OP_GET_FIRMWARE:
            return codec.encode_packet(opcode, self.firmware)
        if opcode == codec.OP_GET_SERIAL:
            return codec.encode_packet(opcode, self.serial_number)
        if opcode == codec.OP_GET_CONFIG:
            return codec.encode_packet(opcode, self._config_payload())
        if opcode == codec.OP_GET_TELEMETRY:
            return codec.encode_packet(opcode, self._telemetry_payload())

        accepted = opcode not in self.reject_opcodes
        if opcode == codec.OP_SET_VOLTAGE and len(payload) == 4:
            if accepted:
                self.target_voltage = struct.unpack("<f", payload)[0]
            return codec.encode_packet(opcode, bytes((int(accepted),)))
        if opcode == codec.OP_SET_CURRENT and len(payload) == 4:
            if accepted:
                self.target_current = struct.unpack("<f", payload)[0]
            return codec.encode_packet(opcode, bytes((int(accepted),)))
        if opcode == codec.OP_OUTPUT_CONTROL and len(payload) == 4:
            state = struct.unpack("<i", payload)[0]
            if accepted and state in (0, 1):
                self.output_enabled = {0: True, 1: False}[state]
            return codec.encode_packet(opcode, bytes((int(accepted),)))
        return None

    def _config_payload(self) -> bytes:
        payload = bytearray(103)
        voltage = self.target_voltage
        current = self.target_current
        if "set_voltage" in self.mismatch_readback:
            voltage += 1.0
        if "set_current" in self.mismatch_readback:
            current += 1.0
        struct.pack_into("<f", payload, 0, voltage)
        struct.pack_into("<f", payload, 4, current)
        struct.pack_into("<f", payload, 8, voltage)
        struct.pack_into("<f", payload, 12, current)
        struct.pack_into("<f", payload, 33, self.max_voltage)
        struct.pack_into("<f", payload, 37, self.max_single_module_current)
        struct.pack_into("<f", payload, 42, 0.5)
        payload[47] = 1
        payload[49] = 90
        payload[50] = 55
        payload[51] = 75
        payload[52:75] = b"HWCDQ SIMULATOR".ljust(23, b"\x00")
        struct.pack_into("<H", payload, 87, 1200)
        struct.pack_into("<H", payload, 89, 1500)
        payload[91:99] = b"ru\x00\x00\x00\x00\x00\x00"
        return bytes(payload)

    def _telemetry_payload(self) -> bytes:
        payload = bytearray(46)
        values = (
            (0, 230.0),
            (4, 4.0 if self.output_enabled else 0.2),
            (8, 50.0),
            (12, 36.5),
            (16, 42.0),
            (20, self.target_voltage if self.output_enabled else 0.0),
            (24, self.target_current if self.output_enabled else 0.0),
            (28, self.target_current),
            (32, 0.93 if self.output_enabled else 0.0),
            (37, 12.5),
            (41, 1000.0),
        )
        for offset, value in values:
            struct.pack_into("<f", payload, offset, value)
        output = self.output_enabled
        if "output" in self.mismatch_readback:
            output = not output
        # Telemetry and opcode 0x0C both use 0=Open/ON and 1=Close/OFF.
        payload[36] = int(not output)
        payload[45] = 2
        return bytes(payload)

    async def _deliver(self, packet: bytes) -> None:
        if self.response_delay:
            await asyncio.sleep(self.response_delay)
        else:
            await asyncio.sleep(0)
        if not self._connected or self._notification_callback is None:
            return
        size = self.notification_fragment_size
        if size is None or size <= 0:
            self._notification_callback(packet)
            return
        for offset in range(0, len(packet), size):
            if not self._connected or self._notification_callback is None:
                return
            self._notification_callback(packet[offset : offset + size])
            await asyncio.sleep(0)

    def _require_connected(self) -> None:
        if not self._connected:
            raise ConnectionError("simulated transport is disconnected")


__all__ = [
    "FakeScanner",
    "FakeTransport",
    "SIMULATED_IDENTIFIER",
    "SimulatedWriteRecord",
]
