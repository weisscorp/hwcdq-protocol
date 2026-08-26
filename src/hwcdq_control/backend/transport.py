"""Deprecated transport aliases for :mod:`hwcdq.transport`."""

from hwcdq.transport import (
    AdvertisementHandler,
    AsyncGattTransport,
    AsyncScanner,
    DisconnectHandler,
    NotificationHandler,
)


AsyncBleTransport = AsyncGattTransport
AsyncBleScanner = AsyncScanner


__all__ = [
    "AdvertisementHandler",
    "AsyncBleScanner",
    "AsyncBleTransport",
    "DisconnectHandler",
    "NotificationHandler",
]
