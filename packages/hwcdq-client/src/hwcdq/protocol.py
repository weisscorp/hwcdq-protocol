"""Offline encoder and decoder for the confirmed HWCDQ application framing.

This module deliberately contains no Bluetooth or network code.  It only turns
values into packets, validates received packets, and exposes fields whose wire
representation has been recovered from the Android application.

The voltage and current encoders validate the *representation* (positive,
finite IEEE-754 binary32).  They cannot know the limits configured in a
particular charger.  A client must read and enforce the device's voltage and
current limits before calling these helpers.
"""

from __future__ import annotations

import hashlib
import math
import numbers
import struct
from typing import Any


MAX_PAYLOAD_LENGTH = 253

# Exact credential used by the recovered Android application when it has no
# saved password.  This is the MD5 digest of an empty byte string, but that
# observation is not evidence that the charger's human-readable password is
# empty.  Keep the original application's uppercase spelling on this path.
APK_FALLBACK_CREDENTIAL = "D41D8CD98F00B204E9800998ECF8427E"
AUTH_CREDENTIAL_LENGTH = 32

OP_GET_FIRMWARE = 0x01
OP_CHECK_PASSWORD = 0x02
OP_GET_SERIAL = 0x04
OP_GET_CONFIG = 0x05
OP_GET_TELEMETRY = 0x06
OP_SET_VOLTAGE = 0x07
OP_SET_CURRENT = 0x08
OP_OUTPUT_CONTROL = 0x0C

CONFIRMED_ACK_OPCODES = {
    OP_CHECK_PASSWORD,
    OP_SET_VOLTAGE,
    OP_SET_CURRENT,
    OP_OUTPUT_CONTROL,
}


class ProtocolError(ValueError):
    """Raised when a packet or encoder argument violates the wire format."""


def _bytes_like(value: object, *, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ProtocolError(f"{name} must be bytes-like")
    try:
        return bytes(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{name} must be a one-dimensional byte sequence") from exc


def _uint8(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{name} must be an integer in 0..255")
    if not 0 <= value <= 0xFF:
        raise ProtocolError(f"{name} must be an integer in 0..255")
    return value


def _positive_float32(value: object, *, name: str) -> bytes:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ProtocolError(f"{name} must be a real number")

    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ProtocolError(f"{name} is not representable as a finite float32") from exc

    if not math.isfinite(converted):
        raise ProtocolError(f"{name} must be finite")
    if converted <= 0.0:
        raise ProtocolError(f"{name} must be greater than zero")

    try:
        encoded = struct.pack("<f", converted)
    except (OverflowError, struct.error) as exc:
        raise ProtocolError(f"{name} is not representable as a finite float32") from exc

    rounded = struct.unpack("<f", encoded)[0]
    if not math.isfinite(rounded) or rounded <= 0.0:
        raise ProtocolError(f"{name} is not representable as a positive finite float32")
    return encoded


def _utf8(value: object, *, name: str) -> bytes:
    if not isinstance(value, str):
        raise ProtocolError(f"{name} must be a string")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProtocolError(f"{name} must be valid UTF-8 text") from exc


def _auth_credential(value: object) -> bytes:
    """Validate the 32-character ASCII hexadecimal value sent on the wire."""

    encoded = _utf8(value, name="credential")
    if len(encoded) != AUTH_CREDENTIAL_LENGTH or any(
        byte not in b"0123456789abcdefABCDEF" for byte in encoded
    ):
        raise ProtocolError(
            "credential must be exactly 32 ASCII hexadecimal characters"
        )
    return encoded


def encode_packet(opcode: int, payload: bytes | bytearray | memoryview = b"") -> bytes:
    """Frame one application packet.

    The first byte counts every following byte: opcode, payload, and checksum.
    The checksum is the modulo-256 sum of opcode and payload; the length byte is
    not included.  This low-level primitive does not establish that an opcode
    is safe or supported; clients should send only packets made by a confirmed
    named encoder.
    """

    checked_opcode = _uint8(opcode, name="opcode")
    checked_payload = _bytes_like(payload, name="payload")
    if len(checked_payload) > MAX_PAYLOAD_LENGTH:
        raise ProtocolError(
            f"payload is too long ({len(checked_payload)} bytes; maximum is "
            f"{MAX_PAYLOAD_LENGTH})"
        )

    remaining_length = len(checked_payload) + 2
    checksum = (checked_opcode + sum(checked_payload)) & 0xFF
    return bytes((remaining_length, checked_opcode)) + checked_payload + bytes((checksum,))


def _telemetry_fields(payload: bytes) -> dict[str, int | float | bool | None]:
    """Decode the confirmed 46-byte telemetry response layout.

    The app labels two values as temperatures but does not establish which
    physical sensors they represent, so they intentionally remain numbered.
    Input and output power are derived exactly as in the app rather than read
    from independent wire fields.
    """

    result: dict[str, int | float | bool | None] = {
        "input_voltage": struct.unpack_from("<f", payload, 0)[0],
        "input_current": struct.unpack_from("<f", payload, 4)[0],
        "input_frequency": struct.unpack_from("<f", payload, 8)[0],
        "temperature_1": struct.unpack_from("<f", payload, 12)[0],
        "temperature_2": struct.unpack_from("<f", payload, 16)[0],
        "output_voltage": struct.unpack_from("<f", payload, 20)[0],
        "output_current": struct.unpack_from("<f", payload, 24)[0],
        "current_point": struct.unpack_from("<f", payload, 28)[0],
        "efficiency": struct.unpack_from("<f", payload, 32)[0],
        "current_output": payload[36],
        "accumulated_capacity_ah": struct.unpack_from("<f", payload, 37)[0],
        "accumulated_energy_wh": struct.unpack_from("<f", payload, 41)[0],
        "module_count": payload[45],
    }
    # The telemetry status byte uses the same Open/Close state encoding as
    # opcode 0x0C: 0 means the output is open/enabled, while 1 means
    # closed/disabled.  Unknown values remain unknown so callers fail closed
    # instead of inventing an ON state.
    result["output_enabled"] = {0: True, 1: False}.get(payload[36])
    result["input_power_w"] = result["input_voltage"] * result["input_current"]
    result["output_power_w"] = result["output_voltage"] * result["output_current"]
    return result


def _config_fields(payload: bytes) -> dict[str, int | float | bytes]:
    """Decode the confirmed 103-byte configuration response layout.

    Fields without a defensible application-level meaning retain offset-based
    names.  Fixed-width text-like regions remain bytes so decoding does not
    discard NUL padding or invent a character encoding.
    """

    return {
        "target_voltage": struct.unpack_from("<f", payload, 0)[0],
        "target_current": struct.unpack_from("<f", payload, 4)[0],
        "offline_voltage": struct.unpack_from("<f", payload, 8)[0],
        "offline_current": struct.unpack_from("<f", payload, 12)[0],
        "power_on_output": payload[16],
        "voltage_calibration": struct.unpack_from("<f", payload, 17)[0],
        "voltage_feedback_calibration": struct.unpack_from("<f", payload, 21)[0],
        "current_calibration": struct.unpack_from("<f", payload, 25)[0],
        "current_feedback_calibration": struct.unpack_from("<f", payload, 29)[0],
        "max_voltage": struct.unpack_from("<f", payload, 33)[0],
        "max_single_module_current": struct.unpack_from("<f", payload, 37)[0],
        "auto_stop": payload[41],
        "shutdown_current": struct.unpack_from("<f", payload, 42)[0],
        "raw_u8_46": payload[46],
        "temperature_protection": payload[47],
        "raw_u8_48": payload[48],
        "protection_cutoff_temperature": payload[49],
        "fan_boost_temperature": payload[50],
        "fan_max_temperature": payload[51],
        "raw_ascii_23": payload[52:75],
        "two_stage_charging": payload[75],
        "secondary_voltage": struct.unpack_from("<f", payload, 76)[0],
        "secondary_current": struct.unpack_from("<f", payload, 80)[0],
        "offline_control": payload[84],
        "raw_u8_85": payload[85],
        "soft_start_coefficient": payload[86],
        "power_limit": struct.unpack_from("<H", payload, 87)[0],
        "max_power": struct.unpack_from("<H", payload, 89)[0],
        "display_language_raw": payload[91:99],
        "raw_u8_99": payload[99],
        "raw_u8_100": payload[100],
        "raw_u8_101": payload[101],
        "raw_u8_102": payload[102],
    }


def _add_semantics(decoded: dict[str, Any]) -> None:
    opcode = decoded["opcode"]
    payload = decoded["payload"]

    if opcode in CONFIRMED_ACK_OPCODES and len(payload) == 1 and payload[0] in (0, 1):
        decoded["acknowledged"] = bool(payload[0])

    if opcode == OP_GET_FIRMWARE:
        decoded["command"] = "get_firmware" if not payload else "firmware_response"
    elif opcode == OP_CHECK_PASSWORD:
        if len(payload) == 1 and payload[0] in (0, 1):
            decoded["command"] = "check_password_ack"
        elif payload.endswith(b"\x00"):
            decoded["command"] = "check_password"
            # The payload is an authentication credential, not plaintext.
            # Do not copy it into a decoded text field which may later be
            # rendered or logged.  Direction-neutral callers that explicitly
            # need wire bytes already have ``payload``.
            decoded["credential_format_valid"] = (
                len(payload) == AUTH_CREDENTIAL_LENGTH + 1
                and all(
                    byte in b"0123456789abcdefABCDEF"
                    for byte in payload[:-1]
                )
            )
        else:
            decoded["command"] = "check_password_unknown_payload"
    elif opcode == OP_GET_SERIAL:
        decoded["command"] = "get_serial" if not payload else "serial_response"
    elif opcode == OP_GET_CONFIG:
        decoded["command"] = "get_config" if not payload else "config_response"
        if len(payload) == 103:
            decoded["config"] = _config_fields(payload)
    elif opcode == OP_GET_TELEMETRY:
        if not payload:
            decoded["command"] = "get_telemetry"
        elif len(payload) == 46:
            decoded["command"] = "telemetry_response"
            decoded["telemetry"] = _telemetry_fields(payload)
        else:
            decoded["command"] = "telemetry_opcode_unknown_payload"
    elif opcode == OP_SET_VOLTAGE:
        decoded["command"] = "set_voltage"
        if len(payload) == 4:
            decoded["volts"] = struct.unpack("<f", payload)[0]
    elif opcode == OP_SET_CURRENT:
        decoded["command"] = "set_current"
        if len(payload) == 4:
            decoded["amps"] = struct.unpack("<f", payload)[0]
    elif opcode == OP_OUTPUT_CONTROL:
        decoded["command"] = "output_control"
        if len(payload) == 4:
            state = struct.unpack("<i", payload)[0]
            decoded["state"] = state
            output_by_state = {0: True, 1: False}
            decoded["state_valid"] = state in output_by_state
            decoded["enabled"] = output_by_state.get(state)
    else:
        decoded["command"] = "unknown"


def decode_packet(data: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Validate and decode exactly one complete application packet.

    The returned dictionary is direction-neutral because requests and responses
    reuse the same opcode.  Unknown or not-yet-understood payloads remain
    available in ``payload`` without interpretation.
    """

    raw = _bytes_like(data, name="data")
    if len(raw) < 3:
        raise ProtocolError("packet is truncated; at least 3 bytes are required")

    declared_length = raw[0]
    if declared_length < 2:
        raise ProtocolError("length byte must include at least opcode and checksum")

    expected_total = declared_length + 1
    if len(raw) != expected_total:
        raise ProtocolError(
            f"length mismatch: header declares {expected_total} total bytes, "
            f"received {len(raw)}"
        )

    opcode = raw[1]
    payload = raw[2:-1]
    expected_checksum = (opcode + sum(payload)) & 0xFF
    checksum = raw[-1]
    if checksum != expected_checksum:
        raise ProtocolError(
            f"checksum mismatch: packet has 0x{checksum:02X}, "
            f"expected 0x{expected_checksum:02X}"
        )

    decoded: dict[str, Any] = {
        "raw": raw,
        "declared_length": declared_length,
        "opcode": opcode,
        "payload": payload,
        "checksum": checksum,
        "checksum_valid": True,
    }
    _add_semantics(decoded)
    return decoded


def verify_checksum(data: bytes | bytearray | memoryview) -> bool:
    """Return ``True`` only for a complete, structurally valid packet."""

    try:
        decode_packet(data)
    except ProtocolError:
        return False
    return True


def encode_get_serial() -> bytes:
    return encode_packet(OP_GET_SERIAL)


def encode_get_firmware() -> bytes:
    return encode_packet(OP_GET_FIRMWARE)


def derive_password_credential(password: str) -> str:
    """Reproduce the Android application's password-to-credential step.

    An empty value selects the APK's exact static fallback.  A non-empty
    plaintext password is UTF-8 encoded and MD5-hashed; Dart's
    ``Digest.toString`` renders that digest as lowercase hexadecimal.
    """

    encoded = _utf8(password, name="password")
    if not encoded:
        return APK_FALLBACK_CREDENTIAL
    return hashlib.md5(encoded, usedforsecurity=False).hexdigest()


def encode_check_password_credential(credential: str) -> bytes:
    """Frame an already-derived APK authentication credential."""

    return encode_packet(
        OP_CHECK_PASSWORD,
        _auth_credential(credential) + b"\x00",
    )


def encode_check_password(password: str = "") -> bytes:
    """Encode a user password exactly as the recovered Android application.

    Passing ``""`` does not send an empty plaintext password: it sends the
    APK's static fallback credential.  Non-empty values are never sent as
    plaintext; their lowercase UTF-8 MD5 digest is sent instead.
    """

    return encode_check_password_credential(derive_password_credential(password))


def encode_get_config() -> bytes:
    return encode_packet(OP_GET_CONFIG)


def encode_get_telemetry() -> bytes:
    return encode_packet(OP_GET_TELEMETRY)


def encode_set_voltage(volts: float) -> bytes:
    """Encode target voltage; the caller must enforce device-configured limits."""

    return encode_packet(OP_SET_VOLTAGE, _positive_float32(volts, name="volts"))


def encode_set_current(amps: float) -> bytes:
    """Encode target current; the caller must enforce device-configured limits."""

    return encode_packet(OP_SET_CURRENT, _positive_float32(amps, name="amps"))


def encode_start() -> bytes:
    return encode_packet(OP_OUTPUT_CONTROL, struct.pack("<i", 0))


def encode_stop() -> bytes:
    return encode_packet(OP_OUTPUT_CONTROL, struct.pack("<i", 1))


__all__ = [
    "APK_FALLBACK_CREDENTIAL",
    "AUTH_CREDENTIAL_LENGTH",
    "ProtocolError",
    "decode_packet",
    "derive_password_credential",
    "encode_check_password",
    "encode_check_password_credential",
    "encode_get_config",
    "encode_get_firmware",
    "encode_get_serial",
    "encode_get_telemetry",
    "encode_packet",
    "encode_set_current",
    "encode_set_voltage",
    "encode_start",
    "encode_stop",
    "verify_checksum",
]
