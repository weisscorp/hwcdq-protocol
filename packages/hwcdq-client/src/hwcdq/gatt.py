"""Fail-closed selection of the HWCDQ GATT transport characteristics."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .errors import GattTopologyError
from .models import GattCharacteristic, GattService, SelectedGattTopology
from .profile import GattProfile, PIDZOOM_HW178P


_BASE_UUID_RE = re.compile(
    r"^0000([0-9a-f]{4})-0000-1000-8000-00805f9b34fb$",
    re.IGNORECASE,
)


def short_uuid(uuid: str) -> str:
    """Return a lowercase assigned-number form when a UUID is reducible."""

    normalized = uuid.strip().lower()
    if re.fullmatch(r"[0-9a-f]{4}", normalized):
        return normalized
    match = _BASE_UUID_RE.fullmatch(normalized)
    if match:
        return match.group(1).lower()
    return normalized


def resolve_wnr_chunk_size(
    advertised: int | None,
    *,
    fallback: int = 20,
    maximum: int = 512,
) -> int:
    """Resolve a conservative write-without-response payload limit.

    CoreBluetooth/Bleak can expose the current peripheral-specific maximum.
    Until it does, the legacy ATT payload of 20 bytes is the safe fallback.
    Impossible or suspicious values are ignored rather than trusted.
    """

    if isinstance(advertised, int) and not isinstance(advertised, bool):
        if 1 <= advertised <= maximum:
            return advertised
    if not 1 <= fallback <= maximum:
        raise ValueError("fallback WNR chunk size must be in 1..maximum")
    return fallback


def chunks_for_write(
    packet: bytes,
    *,
    write_with_response: bool,
    wnr_chunk_size: int,
) -> tuple[bytes, ...]:
    """Split only WNR writes; confirmed writes accept a full HWCDQ frame."""

    if not packet:
        raise ValueError("packet must not be empty")
    if write_with_response:
        return (bytes(packet),)
    size = resolve_wnr_chunk_size(wnr_chunk_size)
    return tuple(bytes(packet[offset : offset + size]) for offset in range(0, len(packet), size))


def _by_short_uuid(
    characteristics: Iterable[GattCharacteristic],
    target: str,
) -> list[GattCharacteristic]:
    return [item for item in characteristics if short_uuid(item.uuid) == target]


def select_hwcdq_topology(
    services: Sequence[GattService],
    *,
    gatt_profile: GattProfile = PIDZOOM_HW178P.gatt,
    wnr_fallback: int = 20,
) -> SelectedGattTopology:
    """Select the unique FFE1 service containing sibling FFE2 and FFE3.

    Live HWCDQ discovery and the Android APK agree on FFE1 as the parent
    service. Generic FFE2/FFE3 UUIDs can occur under unrelated vendor services,
    so the desktop-client profile never treats a matching pair outside FFE1 as
    HWCDQ. Exactly one FFE1 parent must have one notifying FFE2 and one writable
    FFE3.
    """

    service_target = short_uuid(gatt_profile.service_uuid)
    rx_target = short_uuid(gatt_profile.rx_uuid)
    tx_target = short_uuid(gatt_profile.tx_uuid)
    ffe1_services = [
        service
        for service in services
        if short_uuid(service.uuid) == service_target
    ]
    if len(ffe1_services) != 1:
        reason = (
            "no FFE1 service was found"
            if not ffe1_services
            else "multiple FFE1 service instances were found"
        )
        raise GattTopologyError(f"cannot select HWCDQ GATT topology: {reason}")

    service = ffe1_services[0]
    rx_matches = _by_short_uuid(service.characteristics, rx_target)
    tx_matches = _by_short_uuid(service.characteristics, tx_target)
    if len(rx_matches) != 1 or len(tx_matches) != 1:
        raise GattTopologyError(
            "cannot select HWCDQ GATT topology: FFE1 must contain exactly one FFE2 and one FFE3"
        )
    rx = rx_matches[0]
    tx = tx_matches[0]
    if "notify" not in rx.properties:
        raise GattTopologyError(
            "cannot select HWCDQ GATT topology: FFE2 does not advertise notify"
        )
    if not tx.properties & {"write", "write-without-response"}:
        raise GattTopologyError(
            "cannot select HWCDQ GATT topology: FFE3 is not writable"
        )
    # Live CoreBluetooth traces showed acknowledged writes occasionally never
    # completing even though the FFE2 application response arrived.  Prefer
    # WNR when FFE3 advertises it; acknowledged writes remain the compatibility
    # fallback for peripherals which expose only ``write``.
    write_with_response = "write-without-response" not in tx.properties
    wnr_size = resolve_wnr_chunk_size(
        tx.max_write_without_response_size,
        fallback=wnr_fallback,
    )
    return SelectedGattTopology(
        service_uuid=service.uuid,
        rx_uuid=rx.uuid,
        tx_uuid=tx.uuid,
        write_with_response=write_with_response,
        wnr_chunk_size=wnr_size,
    )


__all__ = [
    "chunks_for_write",
    "resolve_wnr_chunk_size",
    "select_hwcdq_topology",
    "short_uuid",
]
