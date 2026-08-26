"""Deprecated backend facade re-exporting the canonical :mod:`hwcdq` API."""

from hwcdq import (
    AmbiguousCommandResultError,
    AmbiguousOutcome,
    AuthenticationError,
    BackendError,
    ChargerSession,
    CommandRejectedError,
    CommandTimeoutError,
    DeviceAdvertisement,
    EventKind,
    FrameStreamError,
    GattCharacteristic,
    GattService,
    GattTopologyError,
    InvalidStateError,
    SafetyInterlockError,
    SelectedGattTopology,
    SessionEvent,
    SessionSnapshot,
    SessionState,
    TransportDisconnectedError,
    UnexpectedResponseError,
)
from hwcdq.framing import FrameAssembler
from hwcdq.gatt import (
    chunks_for_write,
    resolve_wnr_chunk_size,
    select_hwcdq_topology,
    short_uuid,
)
from hwcdq.testing import (
    FakeScanner,
    FakeTransport,
    SIMULATED_IDENTIFIER,
    SimulatedWriteRecord,
)
from hwcdq.transport import AsyncGattTransport, AsyncScanner


AsyncBleTransport = AsyncGattTransport
AsyncBleScanner = AsyncScanner


__all__ = [
    "AmbiguousCommandResultError",
    "AmbiguousOutcome",
    "AsyncBleScanner",
    "AsyncBleTransport",
    "AuthenticationError",
    "BackendError",
    "ChargerSession",
    "CommandRejectedError",
    "CommandTimeoutError",
    "DeviceAdvertisement",
    "EventKind",
    "FakeScanner",
    "FakeTransport",
    "FrameAssembler",
    "FrameStreamError",
    "GattCharacteristic",
    "GattService",
    "GattTopologyError",
    "InvalidStateError",
    "SIMULATED_IDENTIFIER",
    "SafetyInterlockError",
    "SelectedGattTopology",
    "SessionEvent",
    "SessionSnapshot",
    "SessionState",
    "SimulatedWriteRecord",
    "TransportDisconnectedError",
    "UnexpectedResponseError",
    "chunks_for_write",
    "resolve_wnr_chunk_size",
    "select_hwcdq_topology",
    "short_uuid",
]
