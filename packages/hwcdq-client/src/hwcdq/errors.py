"""Backend-specific error hierarchy."""

from __future__ import annotations


class BackendError(Exception):
    """Base class for expected charger backend failures."""


class InvalidStateError(BackendError):
    """The requested operation is not valid in the current session state."""


class TransportDisconnectedError(BackendError):
    """The BLE transport disconnected before the operation completed."""


class CommandTimeoutError(BackendError):
    """No matching application response arrived before the deadline."""


class CommandRejectedError(BackendError):
    """The charger returned an explicit negative acknowledgement."""


class UnexpectedResponseError(BackendError):
    """The response used the expected opcode but not the expected payload."""


class AuthenticationError(BackendError):
    """The charger rejected the supplied session password."""


class SafetyInterlockError(BackendError):
    """A fail-closed safety condition blocks an output-changing command."""


class AmbiguousCommandResultError(SafetyInterlockError):
    """A mutating command may have reached the charger, but was not verified."""


class GattTopologyError(BackendError):
    """The expected HWCDQ GATT topology could not be selected safely."""


class FrameStreamError(BackendError):
    """A notification stream could not be reassembled into valid frames."""


__all__ = [
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
]
