"""Abstract asynchronous BLE seams used by the charger session."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import math
import numbers
from typing import Protocol, runtime_checkable

from .models import DeviceAdvertisement, GattService


NotificationHandler = Callable[[bytes], None]
DisconnectHandler = Callable[[], None]
AdvertisementHandler = Callable[[DeviceAdvertisement], None]


def _validated_scan_duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError("scan duration must be a positive finite number")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("scan duration must be a positive finite number")
    return duration


@runtime_checkable
class AsyncGattTransport(Protocol):
    """One connected BLE peripheral, owned by a single asyncio event loop."""

    @property
    def connected(self) -> bool: ...

    async def connect(
        self,
        identifier: str,
        disconnected_callback: DisconnectHandler,
    ) -> None: ...

    async def disconnect(self) -> None: ...

    async def discover_gatt(self) -> Sequence[GattService]: ...

    async def start_notify(
        self,
        characteristic_uuid: str,
        callback: NotificationHandler,
    ) -> None: ...

    async def stop_notify(self, characteristic_uuid: str) -> None: ...

    async def write(
        self,
        characteristic_uuid: str,
        data: bytes,
        *,
        response: bool,
    ) -> None: ...


@runtime_checkable
class AsyncScanner(Protocol):
    """Scanner contract kept separate from a connected transport."""

    async def scan(
        self,
        duration: float,
        callback: AdvertisementHandler | None = None,
    ) -> Sequence[DeviceAdvertisement]: ...


# Compatibility aliases for the pre-library backend names.  The canonical
# public names describe the abstraction rather than a particular BLE client.
AsyncBleTransport = AsyncGattTransport
AsyncBleScanner = AsyncScanner


__all__ = [
    "AdvertisementHandler",
    "AsyncGattTransport",
    "AsyncScanner",
    "AsyncBleScanner",
    "AsyncBleTransport",
    "DisconnectHandler",
    "NotificationHandler",
]
