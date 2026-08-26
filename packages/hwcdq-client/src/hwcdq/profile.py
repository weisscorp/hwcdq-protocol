"""Public identity, credentials, options, and safety profile for HW178P."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import math
import numbers
import struct
import time
from collections.abc import Callable
from typing import Any

from . import protocol


class AccessMode(str, Enum):
    """Process-level opt-in for commands which can energize charger output."""

    MONITOR_ONLY = "monitor_only"
    CONTROL = "control"


@dataclass(frozen=True, slots=True)
class DeviceTarget:
    """An opaque platform BLE identifier and optional display-only name.

    On macOS ``identifier`` is commonly a CoreBluetooth UUID, not a MAC
    address.  The library deliberately passes it to the transport unchanged.
    """

    identifier: str
    advertised_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier:
            raise ValueError("device identifier must be a non-empty string")
        if self.advertised_name is not None and not isinstance(
            self.advertised_name, str
        ):
            raise TypeError("advertised_name must be a string or None")


class Credential:
    """Validated wire credential which never renders its digest.

    Password factories derive the Android application's MD5 representation
    immediately.  The plaintext is not retained on the object.
    """

    __slots__ = ("_digest",)

    def __init__(self, digest: str) -> None:
        # The encoder is the single canonical validation path for the exact
        # 32-character ASCII hexadecimal credential used on the wire.
        protocol.encode_check_password_credential(digest)
        self._digest = (
            protocol.APK_FALLBACK_CREDENTIAL
            if digest.casefold() == protocol.APK_FALLBACK_CREDENTIAL.casefold()
            else digest.lower()
        )

    @classmethod
    def apk_fallback(cls) -> Credential:
        return cls(protocol.APK_FALLBACK_CREDENTIAL)

    @classmethod
    def from_password(cls, password: str) -> Credential:
        return cls(protocol.derive_password_credential(password))

    @classmethod
    def from_digest(cls, digest: str) -> Credential:
        return cls(digest)

    def _wire_value(self) -> str:
        """Return the validated digest for package-internal framing only."""

        return self._digest

    def __repr__(self) -> str:
        return "Credential([REDACTED])"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Credential) and self._digest == other._digest

    def __hash__(self) -> int:
        return hash(self._digest)


def _finite_positive(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _canonical_float32(value: float) -> float:
    """Return the exact finite positive value carried by the HWCDQ wire."""

    try:
        canonical = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError("value must fit positive IEEE-754 binary32") from exc
    if not math.isfinite(canonical) or canonical <= 0:
        raise ValueError("value must fit positive IEEE-754 binary32")
    return canonical


@dataclass(frozen=True, slots=True)
class NumericRange:
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        minimum = _finite_positive(self.minimum, name="minimum")
        maximum = _finite_positive(self.maximum, name="maximum")
        if maximum < minimum:
            raise ValueError("maximum must not be below minimum")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def contains(self, value: object) -> bool:
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            return False
        candidate = float(value)
        if not math.isfinite(candidate):
            return False
        try:
            candidate = _canonical_float32(candidate)
            minimum = _canonical_float32(self.minimum)
            maximum = _canonical_float32(self.maximum)
        except ValueError:
            return False
        return (
            minimum <= candidate <= maximum
        )


@dataclass(frozen=True, slots=True)
class EffectiveLimits:
    voltage: NumericRange
    current: NumericRange


@dataclass(frozen=True, slots=True)
class GattProfile:
    service_uuid: str
    rx_uuid: str
    tx_uuid: str


@dataclass(frozen=True, slots=True)
class ChargerProfile:
    model: str
    display_name: str
    gatt: GattProfile
    voltage: NumericRange
    current: NumericRange

    def effective_limits(
        self,
        config: Mapping[str, Any] | None,
    ) -> EffectiveLimits | None:
        """Intersect device maxima with the model envelope, or fail closed.

        The recovered configuration has maximum fields only.  Model minima
        therefore remain authoritative.  Missing, Boolean, non-finite,
        non-positive, or below-minimum maxima invalidate the complete result.
        """

        if not isinstance(config, Mapping):
            return None
        reported_voltage = self._reported_maximum(config.get("max_voltage"))
        reported_current = self._reported_maximum(
            config.get("max_single_module_current")
        )
        if reported_voltage is None or reported_current is None:
            return None
        try:
            voltage_minimum_wire = _canonical_float32(self.voltage.minimum)
            current_minimum_wire = _canonical_float32(self.current.minimum)
            voltage_maximum = min(
                _canonical_float32(reported_voltage),
                _canonical_float32(self.voltage.maximum),
            )
            current_maximum = min(
                _canonical_float32(reported_current),
                _canonical_float32(self.current.maximum),
            )
        except ValueError:
            return None
        if (
            voltage_maximum < voltage_minimum_wire
            or current_maximum < current_minimum_wire
        ):
            return None
        # Preserve the human-facing decimal floor when the device maximum is
        # the same binary32 value (notably 0.01 A -> 0.009999999776...).
        if voltage_maximum == voltage_minimum_wire:
            voltage_maximum = self.voltage.minimum
        if current_maximum == current_minimum_wire:
            current_maximum = self.current.minimum
        return EffectiveLimits(
            voltage=NumericRange(self.voltage.minimum, voltage_maximum),
            current=NumericRange(self.current.minimum, current_maximum),
        )

    @staticmethod
    def _reported_maximum(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            return None
        candidate = float(value)
        if not math.isfinite(candidate) or candidate <= 0:
            return None
        return candidate


@dataclass(frozen=True, slots=True)
class SessionOptions:
    request_timeout: float = 8.0
    native_write_timeout: float | None = None
    freshness_seconds: float = 10.0
    notification_settle_delay: float = 1.0
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        request_timeout = _finite_positive(
            self.request_timeout,
            name="request_timeout",
        )
        freshness_seconds = _finite_positive(
            self.freshness_seconds,
            name="freshness_seconds",
        )
        native_write_timeout = self.native_write_timeout
        if native_write_timeout is not None:
            native_write_timeout = _finite_positive(
                native_write_timeout,
                name="native_write_timeout",
            )
        if (
            isinstance(self.notification_settle_delay, bool)
            or not isinstance(self.notification_settle_delay, numbers.Real)
        ):
            raise ValueError(
                "notification_settle_delay must be a finite non-negative number"
            )
        notification_settle_delay = float(self.notification_settle_delay)
        if not math.isfinite(notification_settle_delay) or notification_settle_delay < 0:
            raise ValueError(
                "notification_settle_delay must be a finite non-negative number"
            )
        object.__setattr__(self, "request_timeout", request_timeout)
        object.__setattr__(self, "native_write_timeout", native_write_timeout)
        object.__setattr__(self, "freshness_seconds", freshness_seconds)
        object.__setattr__(
            self,
            "notification_settle_delay",
            notification_settle_delay,
        )
        if not callable(self.clock):
            raise TypeError("clock must be callable")


PIDZOOM_HW178P = ChargerProfile(
    model="HW178P",
    display_name="Pidzoom Portable charger HW178P",
    gatt=GattProfile(service_uuid="FFE1", rx_uuid="FFE2", tx_uuid="FFE3"),
    voltage=NumericRange(50.0, 178.0),
    current=NumericRange(0.01, 14.0),
)

# Compatibility constants for the original desktop package.  Their values are
# aliases of the canonical profile, so safety ranges have one source of truth.
APP_DISPLAY_NAME = PIDZOOM_HW178P.display_name
MODEL_MIN_VOLTAGE_V = PIDZOOM_HW178P.voltage.minimum
MODEL_MAX_VOLTAGE_V = PIDZOOM_HW178P.voltage.maximum
MODEL_MIN_CURRENT_A = PIDZOOM_HW178P.current.minimum
MODEL_MAX_CURRENT_A = PIDZOOM_HW178P.current.maximum

# Preserve the existing QSettings namespace across the visible product rename.
LEGACY_SETTINGS_ORGANIZATION_NAME = "HWCDQ interoperability"
LEGACY_SETTINGS_APPLICATION_NAME = "HWCDQ Bench Control"


__all__ = [
    "APP_DISPLAY_NAME",
    "AccessMode",
    "ChargerProfile",
    "Credential",
    "DeviceTarget",
    "EffectiveLimits",
    "GattProfile",
    "LEGACY_SETTINGS_APPLICATION_NAME",
    "LEGACY_SETTINGS_ORGANIZATION_NAME",
    "MODEL_MAX_CURRENT_A",
    "MODEL_MAX_VOLTAGE_V",
    "MODEL_MIN_CURRENT_A",
    "MODEL_MIN_VOLTAGE_V",
    "NumericRange",
    "PIDZOOM_HW178P",
    "SessionOptions",
]
