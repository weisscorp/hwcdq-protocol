"""Deprecated import shim for :mod:`hwcdq.redaction`."""

from hwcdq.redaction import (
    REDACTED,
    format_packet,
    is_password_packet,
    redact_text,
    redact_value,
)


__all__ = [
    "REDACTED",
    "format_packet",
    "is_password_packet",
    "redact_text",
    "redact_value",
]
