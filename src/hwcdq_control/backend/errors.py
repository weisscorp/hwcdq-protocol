"""Deprecated import shim for :mod:`hwcdq.errors`."""

from hwcdq.errors import (
    AmbiguousCommandResultError,
    AuthenticationError,
    BackendError,
    CommandRejectedError,
    CommandTimeoutError,
    FrameStreamError,
    GattTopologyError,
    InvalidStateError,
    SafetyInterlockError,
    TransportDisconnectedError,
    UnexpectedResponseError,
)


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
