"""Narrow controller contract consumed by the Qt user interface.

The UI deliberately knows nothing about Bleak, asyncio, or packet encoding.
The application controller owns those concerns and exposes the signals and
methods below.  Signal payloads may be dataclass instances or mappings; this
keeps the UI usable with both the production controller and a tiny simulator.

Expected signals
----------------
``mode_changed(payload)``
    ``{"kind": "simulation|monitoring|control", "label": str}``.
``devices_changed(payload)``
    Iterable of advertisements with ``identifier``, ``name``, ``rssi``, and
    optional ``service_uuids``.
``snapshot_changed(payload)``
    Session state.  See :class:`SnapshotPayload` for the accepted keys.
    Safety decisions require explicit ``config_fresh is True`` and
    ``telemetry_fresh is True``; merely having dictionaries is insufficient.
``gatt_changed(payload)``
    ``{"services": iterable, "selected": topology-or-None,
    "error": str-or-None}``.
``packet_logged(payload)``
    Redacted log entry with ``timestamp``, ``direction``, ``opcode``,
    ``summary``, ``raw_hex``, and optional ``decoded``.  The controller must
    redact secrets; the UI applies an additional defensive pass.
``operation_changed(payload)``
    ``{"busy": bool, "name": str, "message": str}``.

Expected methods
----------------
``start_scan()``, ``stop_scan()``,
``connect_device(identifier, password)``, ``disconnect_device()``,
``refresh()``, ``set_voltage(volts)``, ``set_current(amps)``,
``start_output(volts, amps)``, ``stop_output()``.

All methods must return quickly.  Long-running work belongs to the
controller's BLE worker.  The UI never retries a mutating operation.
For ``connect_device``, an empty password explicitly selects the one-shot
fallback credential recovered from the Android APK; it is not interpreted as
proof that the charger's human-readable password is empty.
"""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import QObject


class SnapshotPayload(Protocol):
    """Documented shape of a ``snapshot_changed`` payload.

    Required production fields are intentionally represented as attributes in
    this structural protocol.  A mapping with the same keys is equally valid.
    ``state`` accepts either a string or ``SessionState`` enum value.
    """

    state: Any
    transport_connected: bool
    authenticated: bool
    output_controls_enabled: bool
    control_outcome_unknown: bool
    config_fresh: bool
    telemetry_fresh: bool
    config: dict[str, Any] | None
    telemetry: dict[str, Any] | None
    last_error: str | None


class ControllerProtocol(Protocol):
    """Structural method surface expected by :class:`MainWindow`.

    The concrete controller is also a :class:`~PySide6.QtCore.QObject` with
    the six signals described in this module's docstring.
    """

    def start_scan(self) -> None: ...

    def stop_scan(self) -> None: ...

    def connect_device(self, identifier: str, password: str) -> None: ...

    def disconnect_device(self) -> None: ...

    def refresh(self) -> None: ...

    def set_voltage(self, volts: float) -> None: ...

    def set_current(self, amps: float) -> None: ...

    def start_output(self, volts: float, amps: float) -> None: ...

    def stop_output(self) -> None: ...


ControllerQObject = QObject


__all__ = ["ControllerProtocol", "ControllerQObject", "SnapshotPayload"]
