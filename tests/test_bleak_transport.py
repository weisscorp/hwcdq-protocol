from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT))

from hwcdq import (  # noqa: E402
    ChargerSession,
    Credential,
    DeviceTarget,
    SessionOptions,
    SessionState,
)
from hwcdq.bleak import BleakScanner, BleakTransport  # noqa: E402


class _FakeScanner:
    def __init__(self, detection_callback=None, **_kwargs):
        self.callback = detection_callback
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True
        assert self.callback is not None
        self.callback(
            SimpleNamespace(address="DEVICE-B", name="Fallback"),
            SimpleNamespace(
                local_name="HWCDQ-B",
                rssi=-70,
                service_uuids=["FFE1"],
                manufacturer_data={7: bytearray(b"B")},
            ),
        )
        self.callback(
            SimpleNamespace(address="DEVICE-A", name=None),
            SimpleNamespace(
                local_name="HWCDQ-A",
                rssi=-42,
                service_uuids=[],
                manufacturer_data={},
            ),
        )

    async def stop(self) -> None:
        self.stopped = True


class _FakeCharacteristic:
    def __init__(self, uuid: str, properties: list[str], maximum: int = 20):
        self.uuid = uuid
        self.properties = properties
        self.max_write_without_response_size = maximum


class _FakeClient:
    last: "_FakeClient | None" = None

    def __init__(self, identifier, disconnected_callback=None, **kwargs):
        type(self).last = self
        self.identifier = identifier
        self.disconnected_callback = disconnected_callback
        self.kwargs = kwargs
        self.is_connected = False
        self.writes: list[tuple[str, bytes, bool]] = []
        self.notifies: dict[str, object] = {}
        self.services = [
            SimpleNamespace(
                uuid="0000ffe1-0000-1000-8000-00805f9b34fb",
                characteristics=[
                    _FakeCharacteristic("FFE2", ["notify"]),
                    _FakeCharacteristic(
                        "FFE3", ["write", "write-without-response"], 117
                    ),
                ],
            )
        ]

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False
        if self.disconnected_callback is not None:
            self.disconnected_callback(self)

    async def start_notify(self, uuid, callback) -> None:
        self.notifies[uuid] = callback

    async def stop_notify(self, uuid) -> None:
        self.notifies.pop(uuid, None)

    async def write_gatt_char(self, uuid, data, response=None) -> None:
        self.writes.append((uuid, bytes(data), bool(response)))


class _CancelledAfterConnectClient(_FakeClient):
    async def connect(self) -> None:
        self.is_connected = True
        raise asyncio.CancelledError

    async def disconnect(self) -> None:
        self.disconnect_calls = getattr(self, "disconnect_calls", 0) + 1
        await asyncio.sleep(0)
        self.is_connected = False


class _StubbornCancelledClient(_FakeClient):
    async def connect(self) -> None:
        self.is_connected = True
        raise asyncio.CancelledError

    async def disconnect(self) -> None:
        self.disconnect_calls = getattr(self, "disconnect_calls", 0) + 1
        await asyncio.sleep(0)
        # Adapter setup cleanup and Session setup cleanup both fail to close
        # the native link.  A later explicit operator retry succeeds.
        if self.disconnect_calls >= 3:
            self.is_connected = False


class BleakScannerAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_scanner_translates_and_sorts_advertisements(self) -> None:
        seen = []
        with patch("hwcdq.bleak._BleakScanner", _FakeScanner):
            devices = await BleakScanner().scan(0.001, seen.append)

        self.assertEqual([item.identifier for item in devices], ["DEVICE-A", "DEVICE-B"])
        self.assertEqual(devices[1].manufacturer_data, {7: b"B"})
        self.assertEqual(len(seen), 2)

    async def test_non_positive_scan_duration_is_rejected_without_scanning(self) -> None:
        with self.assertRaises(ValueError):
            await BleakScanner().scan(0)


class BleakTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_native_connect_is_disconnected_before_reference_clears(self) -> None:
        with patch(
            "hwcdq.bleak.BleakClient",
            _CancelledAfterConnectClient,
        ):
            transport = BleakTransport(connection_timeout=3)
            with self.assertRaises(asyncio.CancelledError):
                await transport.connect("DEVICE-A", lambda: None)

        client = _CancelledAfterConnectClient.last
        assert client is not None
        self.assertEqual(client.disconnect_calls, 1)
        self.assertFalse(client.is_connected)
        self.assertFalse(transport.connected)

    async def test_failed_native_teardown_stays_visible_and_explicit_retry_closes(self) -> None:
        with patch(
            "hwcdq.bleak.BleakClient",
            _StubbornCancelledClient,
        ):
            transport = BleakTransport(connection_timeout=3)
            session = ChargerSession(
                transport,
                options=SessionOptions(notification_settle_delay=0),
            )
            with self.assertRaises(asyncio.CancelledError):
                await session.connect(
                    DeviceTarget("DEVICE-A"),
                    Credential.from_password("0000"),
                )

            self.assertEqual(session.state, SessionState.ERROR)
            self.assertTrue(session.snapshot.transport_connected)
            self.assertFalse(session.authenticated)
            await session.disconnect()

        client = _StubbornCancelledClient.last
        assert client is not None
        self.assertEqual(client.disconnect_calls, 3)
        self.assertEqual(session.state, SessionState.DISCONNECTED)
        self.assertFalse(session.snapshot.transport_connected)

    async def test_transport_maps_gatt_and_preserves_selected_write_type(self) -> None:
        disconnected: list[bool] = []
        with patch("hwcdq.bleak.BleakClient", _FakeClient):
            transport = BleakTransport(connection_timeout=3)
            await transport.connect("DEVICE-A", lambda: disconnected.append(True))
            services = await transport.discover_gatt()
            await transport.start_notify("FFE2", lambda _data: None)
            await transport.write("FFE3", b"\x02\x06\x06", response=True)

            client = _FakeClient.last
            assert client is not None
            self.assertTrue(transport.connected)
            self.assertEqual(client.kwargs["timeout"], 3)
            self.assertIs(client.kwargs["pair"], False)
            self.assertEqual(services[0].characteristics[1].max_write_without_response_size, 117)
            self.assertEqual(client.writes, [("FFE3", b"\x02\x06\x06", True)])

            notify = client.notifies["FFE2"]
            received: list[bytes] = []
            await transport.stop_notify("FFE2")
            await transport.start_notify("FFE2", received.append)
            notify = client.notifies["FFE2"]
            notify(None, bytearray(b"\x03\x07\x01\x08"))  # type: ignore[operator]
            self.assertEqual(received, [b"\x03\x07\x01\x08"])

            client.disconnected_callback(client)
            self.assertEqual(disconnected, [True])
            await transport.disconnect()

    async def test_write_and_discovery_require_a_connection(self) -> None:
        transport = BleakTransport()
        with self.assertRaises(RuntimeError):
            await transport.discover_gatt()
        with self.assertRaises(RuntimeError):
            await transport.write("FFE3", b"x", response=True)


if __name__ == "__main__":
    unittest.main()
