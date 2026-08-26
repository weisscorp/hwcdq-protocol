"""Deprecated desktop namespace; reusable behavior lives in :mod:`hwcdq`."""

from hwcdq import (
    APK_FALLBACK_CREDENTIAL,
    ChargerSession,
    DiagnosticLogger,
    ProtocolError,
    decode_packet,
    derive_password_credential,
    encode_check_password,
    encode_check_password_credential,
    encode_get_config,
    encode_get_firmware,
    encode_get_serial,
    encode_get_telemetry,
    encode_set_current,
    encode_set_voltage,
    encode_start,
    encode_stop,
    verify_checksum,
)


__version__ = "0.1.0"


__all__ = [
    "__version__",
    "APK_FALLBACK_CREDENTIAL",
    "ChargerSession",
    "DiagnosticLogger",
    "ProtocolError",
    "decode_packet",
    "derive_password_credential",
    "encode_check_password",
    "encode_check_password_credential",
    "encode_get_config",
    "encode_get_firmware",
    "encode_get_serial",
    "encode_get_telemetry",
    "encode_set_current",
    "encode_set_voltage",
    "encode_start",
    "encode_stop",
    "verify_checksum",
]
