"""Stable macOS bundle metadata and frozen-module audit helpers.

This module deliberately has no Qt, Bleak, or PyInstaller imports.  The spec,
the build verifier, and unit tests can therefore share one authoritative
bundle contract without initializing CoreBluetooth.
"""

from __future__ import annotations

import re
from collections.abc import Mapping


BUNDLE_IDENTIFIER = "cc.hwcdq.bench-control"
BUNDLE_EXECUTABLE = "Pidzoom Portable charger HW178P"
BUNDLE_NAME = "Pidzoom Portable charger HW178P"
MINIMUM_MACOS_VERSION = "26.0"

BLUETOOTH_USAGE_DESCRIPTION = (
    "Bluetooth is used to discover and communicate with your HWCDQ charger."
)

_MISSING_MODULE_RE = re.compile(
    r"(?:missing|hidden import).*?(?:module named\s+)?['\"]?"
    r"(?P<module>[A-Za-z_][A-Za-z0-9_.]*)",
    re.IGNORECASE,
)
_LOAD_COMMAND_RE = re.compile(
    r"(?=^\s*Load command\s+\d+\s*$)",
    re.MULTILINE,
)
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,2}$")


def info_plist_entries() -> dict[str, object]:
    """Return the additional, deterministic ``Info.plist`` contract."""

    return {
        "CFBundleDisplayName": BUNDLE_NAME,
        "CFBundleName": BUNDLE_NAME,
        "LSMinimumSystemVersion": MINIMUM_MACOS_VERSION,
        "NSBluetoothAlwaysUsageDescription": BLUETOOTH_USAGE_DESCRIPTION,
        # Kept as well as NSBluetoothAlwaysUsageDescription for compatibility
        # with older CoreBluetooth permission checks.
        "NSBluetoothPeripheralUsageDescription": BLUETOOTH_USAGE_DESCRIPTION,
        "NSHighResolutionCapable": True,
    }


def validate_info_plist(plist: Mapping[str, object]) -> tuple[str, ...]:
    """Return human-readable violations of the final app metadata contract."""

    expected = {
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleExecutable": BUNDLE_EXECUTABLE,
        **info_plist_entries(),
    }
    issues: list[str] = []
    for key, expected_value in expected.items():
        actual = plist.get(key)
        if actual != expected_value:
            issues.append(
                f"{key} must be {expected_value!r}; found {actual!r}"
            )
    return tuple(issues)


def parse_macos_deployment_targets(text: str) -> tuple[str, ...]:
    """Extract macOS deployment targets from ``vtool -show-build`` output."""

    targets: list[str] = []
    for block in _LOAD_COMMAND_RE.split(text):
        if re.search(r"^\s*cmd\s+LC_BUILD_VERSION\s*$", block, re.MULTILINE):
            if not re.search(r"^\s*platform\s+MACOS\s*$", block, re.MULTILINE):
                continue
            match = re.search(
                r"^\s*minos\s+(?P<version>\d+(?:\.\d+){0,2})\s*$",
                block,
                re.MULTILINE,
            )
        elif re.search(
            r"^\s*cmd\s+LC_VERSION_MIN_MACOSX\s*$",
            block,
            re.MULTILINE,
        ):
            match = re.search(
                r"^\s*version\s+(?P<version>\d+(?:\.\d+){0,2})\s*$",
                block,
                re.MULTILINE,
            )
        else:
            continue
        if match is not None:
            targets.append(match.group("version"))
    return tuple(targets)


def _macos_version_key(value: str) -> tuple[int, int, int]:
    if _VERSION_RE.fullmatch(value) is None:
        raise ValueError(f"invalid macOS version: {value!r}")
    components = [int(component) for component in value.split(".")]
    components.extend([0] * (3 - len(components)))
    return tuple(components)  # type: ignore[return-value]


def is_macos_version_newer(candidate: str, declared: str) -> bool:
    """Return whether ``candidate`` requires a newer macOS than ``declared``."""

    return _macos_version_key(candidate) > _macos_version_key(declared)


def required_corebluetooth_modules() -> tuple[str, ...]:
    """Return pinned Bleak 3.0.2 modules required by the frozen backend."""

    return (
        "bleak.backends.corebluetooth.scanner",
        "bleak.backends.corebluetooth.client",
        "bleak.backends.service",
        "bleak.backends.corebluetooth.CentralManagerDelegate",
        "bleak.backends.corebluetooth.PeripheralDelegate",
        "bleak.backends.corebluetooth.utils",
    )


def audit_warning_text(text: str) -> tuple[str, ...]:
    """Return PyInstaller warning lines that omit required BLE modules."""

    required = set(required_corebluetooth_modules())
    issues: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.casefold()
        if "missing" not in lowered and "hidden import" not in lowered:
            continue
        match = _MISSING_MODULE_RE.search(line)
        module = match.group("module") if match is not None else None
        if module in required:
            issues.append(line)
    return tuple(issues)


__all__ = [
    "BLUETOOTH_USAGE_DESCRIPTION",
    "BUNDLE_EXECUTABLE",
    "BUNDLE_IDENTIFIER",
    "BUNDLE_NAME",
    "MINIMUM_MACOS_VERSION",
    "audit_warning_text",
    "info_plist_entries",
    "is_macos_version_newer",
    "parse_macos_deployment_targets",
    "required_corebluetooth_modules",
    "validate_info_plist",
]
