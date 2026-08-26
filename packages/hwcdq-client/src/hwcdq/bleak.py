"""Bleak/CoreBluetooth adapters for the dependency-free backend contracts.

All objects in this module are designed to be created and used from one
dedicated asyncio event loop.  Importing the module never scans, connects, or
requests Bluetooth permission.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

try:
    from bleak import BleakClient, BleakScanner as _BleakScanner
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
    if exc.name == "bleak":
        raise ImportError(
            "hwcdq.bleak requires the optional 'bleak' dependency; "
            "install 'hwcdq-client[bleak]'"
        ) from exc
    raise

from .models import DeviceAdvertisement, GattCharacteristic, GattService
from .transport import (
    AdvertisementHandler,
    DisconnectHandler,
    NotificationHandler,
    _validated_scan_duration,
)


class BleakScanner:
    """Translate Bleak advertisements into stable application data objects."""

    async def scan(
        self,
        duration: float,
        callback: AdvertisementHandler | None = None,
    ) -> Sequence[DeviceAdvertisement]:
        checked_duration = _validated_scan_duration(duration)

        discovered: dict[str, DeviceAdvertisement] = {}

        def on_detection(device: Any, advertisement: Any) -> None:
            item = DeviceAdvertisement(
                identifier=str(device.address),
                name=advertisement.local_name or device.name,
                rssi=int(advertisement.rssi) if advertisement.rssi is not None else None,
                service_uuids=tuple(str(value) for value in advertisement.service_uuids),
                manufacturer_data={
                    int(key): bytes(value)
                    for key, value in advertisement.manufacturer_data.items()
                },
            )
            discovered[item.identifier] = item
            if callback is not None:
                callback(item)

        scanner = _BleakScanner(detection_callback=on_detection)
        await scanner.start()
        try:
            await asyncio.sleep(checked_duration)
        finally:
            await scanner.stop()

        return tuple(
            sorted(
                discovered.values(),
                key=lambda item: (
                    item.rssi is None,
                    -(item.rssi or -999),
                    (item.name or "").casefold(),
                    item.identifier,
                ),
            )
        )


class BleakTransport:
    """One Bleak peripheral implementing ``AsyncBleTransport``."""

    def __init__(self, *, connection_timeout: float = 15.0) -> None:
        if connection_timeout <= 0:
            raise ValueError("connection timeout must be positive")
        self._connection_timeout = connection_timeout
        self._client: BleakClient | None = None
        self._disconnect_handler: DisconnectHandler | None = None
        self._notification_wrappers: dict[str, Callable[..., None]] = {}

    @property
    def connected(self) -> bool:
        return bool(self._client is not None and self._client.is_connected)

    def _require_client(self) -> BleakClient:
        if self._client is None or not self._client.is_connected:
            raise RuntimeError("BLE peripheral is not connected")
        return self._client

    def _on_disconnected(self, _client: BleakClient) -> None:
        handler = self._disconnect_handler
        if handler is not None:
            handler()

    @staticmethod
    async def _disconnect_native(client: Any) -> bool:
        """Finish native teardown even if the owning task is cancelled.

        CoreBluetooth can report a successful connection at the same moment
        that the asyncio connect task is cancelled.  Run disconnect in its own
        shielded task and remember any cancellation so the caller can re-raise
        it only after the native link is no longer hidden.
        """

        if not bool(client.is_connected):
            return False
        disconnect_task = asyncio.create_task(client.disconnect())
        cancellation_seen = False
        while True:
            try:
                await asyncio.shield(disconnect_task)
            except asyncio.CancelledError:
                cancellation_seen = True
                if disconnect_task.done():
                    break
                continue
            except BaseException:
                break
            else:
                break
        return cancellation_seen

    async def connect(
        self,
        identifier: str,
        disconnected_callback: DisconnectHandler,
    ) -> None:
        if not identifier:
            raise ValueError("device identifier must not be empty")
        if self.connected:
            raise RuntimeError("BLE peripheral is already connected")

        client = BleakClient(
            identifier,
            disconnected_callback=self._on_disconnected,
            timeout=self._connection_timeout,
            pair=False,
        )
        self._client = client
        self._disconnect_handler = disconnected_callback
        try:
            await client.connect()
        except BaseException as exc:
            # Suppress the expected native disconnect callback during failed
            # setup.  Never discard the client reference while it still owns
            # a live link: Session can then expose and retry Disconnect.
            self._disconnect_handler = None
            cancellation_seen = await self._disconnect_native(client)
            if client.is_connected:
                self._client = client
                self._disconnect_handler = disconnected_callback
            else:
                self._client = None
            if isinstance(exc, asyncio.CancelledError) or cancellation_seen:
                raise asyncio.CancelledError from None
            raise

    async def disconnect(self) -> None:
        client = self._client
        self._notification_wrappers.clear()
        if client is None:
            self._disconnect_handler = None
            return

        handler = self._disconnect_handler
        self._disconnect_handler = None
        cancellation_seen = await self._disconnect_native(client)
        if client.is_connected:
            # Keep the live client reachable so state snapshots remain honest
            # and a subsequent explicit Disconnect can retry the teardown.
            self._client = client
            self._disconnect_handler = handler
            if cancellation_seen:
                raise asyncio.CancelledError
            raise RuntimeError("native BLE link remained connected after disconnect")

        self._client = None
        if cancellation_seen:
            raise asyncio.CancelledError

    async def discover_gatt(self) -> Sequence[GattService]:
        client = self._require_client()
        services: list[GattService] = []
        for service in client.services:
            characteristics: list[GattCharacteristic] = []
            for characteristic in service.characteristics:
                properties = frozenset(str(value).lower() for value in characteristic.properties)
                maximum: int | None = None
                if "write-without-response" in properties:
                    try:
                        candidate = characteristic.max_write_without_response_size
                    except (AttributeError, NotImplementedError, RuntimeError):
                        candidate = None
                    if isinstance(candidate, int) and not isinstance(candidate, bool):
                        maximum = candidate
                characteristics.append(
                    GattCharacteristic(
                        uuid=str(characteristic.uuid),
                        properties=properties,
                        max_write_without_response_size=maximum,
                    )
                )
            services.append(GattService(str(service.uuid), characteristics))
        return tuple(services)

    async def start_notify(
        self,
        characteristic_uuid: str,
        callback: NotificationHandler,
    ) -> None:
        client = self._require_client()

        def on_notification(_sender: Any, data: bytearray) -> None:
            callback(bytes(data))

        self._notification_wrappers[characteristic_uuid] = on_notification
        await client.start_notify(characteristic_uuid, on_notification)

    async def stop_notify(self, characteristic_uuid: str) -> None:
        client = self._require_client()
        try:
            await client.stop_notify(characteristic_uuid)
        finally:
            self._notification_wrappers.pop(characteristic_uuid, None)

    async def write(
        self,
        characteristic_uuid: str,
        data: bytes,
        *,
        response: bool,
    ) -> None:
        if not data:
            raise ValueError("BLE write must not be empty")
        client = self._require_client()
        await client.write_gatt_char(
            characteristic_uuid,
            bytes(data),
            response=response,
        )


# Compatibility spelling retained for the desktop adapter during migration.
BleakScannerAdapter = BleakScanner


__all__ = ["BleakScanner", "BleakScannerAdapter", "BleakTransport"]
