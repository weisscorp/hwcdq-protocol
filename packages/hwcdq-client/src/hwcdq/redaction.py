"""Safe formatting helpers for packet logs and exported diagnostics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import protocol as codec


REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = ("password", "passwd", "secret", "token")


def is_password_packet(data: bytes | bytearray | memoryview) -> bool:
    try:
        decoded = codec.decode_packet(data)
    except (codec.ProtocolError, TypeError, ValueError):
        return False
    return decoded["opcode"] == codec.OP_CHECK_PASSWORD and len(decoded["payload"]) != 1


def format_packet(
    data: bytes | bytearray | memoryview,
    *,
    redact_secrets: bool = True,
) -> str:
    raw = bytes(data)
    if redact_secrets and is_password_packet(raw):
        # Do not retain the checksum either: for short passwords it leaks a
        # constraint over the secret bytes.
        return f"{raw[0]:02X} {raw[1]:02X} {REDACTED}"
    return raw.hex(" ").upper()


def redact_value(value: Any) -> Any:
    """Recursively redact common secret-bearing mapping keys."""

    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            sensitive = isinstance(key, str) and any(
                part in key.lower() for part in _SENSITIVE_KEY_PARTS
            )
            result[key] = REDACTED if sensitive else redact_value(item)
        return result
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_text(text: str, secrets: Sequence[str]) -> str:
    """Remove common literal/encoded renderings of in-memory secrets."""

    redacted = text
    for secret in secrets:
        if not secret:
            continue
        redacted = redacted.replace(secret, REDACTED)
        try:
            encoded = secret.encode("utf-8")
        except UnicodeEncodeError:
            continue
        bytes_repr = repr(encoded)
        escaped_bytes = "".join(f"\\x{value:02x}" for value in encoded)
        unicode_escape = secret.encode("unicode_escape").decode("ascii")
        renderings = (
            encoded.hex(),
            encoded.hex(" "),
            bytes_repr,
            bytes_repr[2:-1],
            escaped_bytes,
            unicode_escape,
        )
        for rendered in renderings:
            if not rendered:
                continue
            redacted = redacted.replace(rendered, REDACTED)
            redacted = redacted.replace(rendered.upper(), REDACTED)
    return redacted


__all__ = [
    "REDACTED",
    "format_packet",
    "is_password_packet",
    "redact_text",
    "redact_value",
]
