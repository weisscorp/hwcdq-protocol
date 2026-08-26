"""Dependency-free data types shared by transports, sessions, and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    DISCOVERING = "discovering"
    AUTHENTICATING = "authenticating"
    LOADING = "loading"
    READY = "ready"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


class EventKind(str, Enum):
    STATE = "state"
    TX = "tx"
    RX = "rx"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    DATA = "data"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    kind: EventKind
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeviceAdvertisement:
    identifier: str
    name: str | None = None
    rssi: int | None = None
    service_uuids: tuple[str, ...] = ()
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GattCharacteristic:
    uuid: str
    properties: frozenset[str]
    max_write_without_response_size: int | None = None

    def __init__(
        self,
        uuid: str,
        properties: frozenset[str] | set[str] | tuple[str, ...] | list[str],
        max_write_without_response_size: int | None = None,
    ) -> None:
        object.__setattr__(self, "uuid", uuid)
        object.__setattr__(
            self,
            "properties",
            frozenset(str(value).lower() for value in properties),
        )
        object.__setattr__(
            self,
            "max_write_without_response_size",
            max_write_without_response_size,
        )


@dataclass(frozen=True, slots=True)
class GattService:
    uuid: str
    characteristics: tuple[GattCharacteristic, ...]

    def __init__(
        self,
        uuid: str,
        characteristics: tuple[GattCharacteristic, ...]
        | list[GattCharacteristic],
    ) -> None:
        object.__setattr__(self, "uuid", uuid)
        object.__setattr__(self, "characteristics", tuple(characteristics))


@dataclass(frozen=True, slots=True)
class SelectedGattTopology:
    service_uuid: str
    rx_uuid: str
    tx_uuid: str
    write_with_response: bool
    wnr_chunk_size: int


@dataclass(frozen=True, slots=True)
class AmbiguousOutcome:
    operation: str
    expected_value: float | bool | None
    reason: str


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    state: SessionState
    transport_connected: bool
    authenticated: bool
    control_outcome_unknown: bool
    ambiguous_outcomes: tuple[AmbiguousOutcome, ...]
    output_controls_enabled: bool
    config_fresh: bool
    telemetry_fresh: bool
    config_age_s: float | None
    telemetry_age_s: float | None
    services: tuple[GattService, ...]
    topology: SelectedGattTopology | None
    firmware: bytes | None
    serial_number: bytes | None
    config: dict[str, Any] | None
    telemetry: dict[str, Any] | None
    last_error: str | None


__all__ = [
    "AmbiguousOutcome",
    "DeviceAdvertisement",
    "EventKind",
    "GattCharacteristic",
    "GattService",
    "SelectedGattTopology",
    "SessionEvent",
    "SessionSnapshot",
    "SessionState",
]
