"""Local, fail-closed diagnostic logging for HWCDQ clients.

The logger deliberately has no network integration.  It accepts structured
events from UI and BLE worker threads, sanitizes them at the file sink, and
writes one complete JSON object per line.  A disabled instance is a true
no-op and does not touch the filesystem.

Typical use::

    log = DiagnosticLogger("logs/hwcdq-debug.jsonl", enabled=True)
    with log.register_secret(password):
        log.emit("ble", "authentication_submitted", opcode=0x02)
    log.close()

``register_secret`` retains only in-memory redaction variants for the lifetime
of the context manager.  It never emits an event or records the secret length.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence, Set as AbstractSet
from contextlib import contextmanager
from datetime import date, datetime, timezone
from enum import Enum
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from typing import Any, BinaryIO, Final
import unicodedata


SCHEMA_VERSION: Final[int] = 1
REDACTED: Final[str] = "[REDACTED]"
TRUNCATED: Final[str] = "[TRUNCATED]"
UNSUPPORTED: Final[str] = "[UNSUPPORTED]"
DEFAULT_MAX_BYTES: Final[int] = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT: Final[int] = 3

_PASSWORD_OPCODE: Final[int] = 0x02
_MAX_NESTING: Final[int] = 32
_SENSITIVE_KEY_PARTS: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "passcode",
    "credential",
    "secret",
    "token",
    "privatekey",
    "private_key",
    "apikey",
    "api_key",
    "accesskey",
    "access_key",
    "bearer",
    "authorization",
    "cookie",
    "nonce",
    "pairing_code",
    "pairingcode",
    "pin_code",
    "pincode",
    "pwd",
)
_FORBIDDEN_EVENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "character_input",
        "key_down",
        "key_input",
        "key_press",
        "key_pressed",
        "key_release",
        "key_up",
        "keypress",
        "keystroke",
        "raw_key",
        "raw_key_event",
        "raw_keystroke",
        "text_input",
    }
)


def _normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalized_name(value)
    compact = normalized.replace("_", "")
    return any(
        marker in normalized or marker.replace("_", "") in compact
        for marker in _SENSITIVE_KEY_PARTS
    )


def _parse_opcode(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip().casefold()
        try:
            return int(stripped, 16 if stripped.startswith("0x") else 10)
        except ValueError:
            return None
    return None


def _mapping_password_opcode(value: Mapping[Any, Any]) -> bool:
    for key, item in value.items():
        if isinstance(key, str) and _normalized_name(key) == "opcode":
            return _parse_opcode(item) == _PASSWORD_OPCODE
    return False


def _secret_variants(secret: str | bytes) -> frozenset[str]:
    """Return common textual renderings without exposing their lengths."""

    if isinstance(secret, bytes):
        encoded_values = {bytes(secret)}
        try:
            literal_values = {secret.decode("utf-8")}
        except UnicodeDecodeError:
            literal_values = set()
    else:
        literal_values = {
            unicodedata.normalize(form, secret)
            for form in ("NFC", "NFD", "NFKC", "NFKD")
        }
        encoded_values = {literal.encode("utf-8") for literal in literal_values}

    variants: set[str] = {item for item in literal_values if item}
    for encoded in encoded_values:
        if not encoded:
            continue
        compact_hex = encoded.hex()
        spaced_hex = encoded.hex(" ")
        dashed_hex = encoded.hex("-")
        escaped = "".join(f"\\x{byte:02x}" for byte in encoded)
        bytes_repr = repr(encoded)
        variants.update(
            {
                compact_hex,
                compact_hex.upper(),
                spaced_hex,
                spaced_hex.upper(),
                dashed_hex,
                dashed_hex.upper(),
                escaped,
                escaped.upper(),
                bytes_repr,
                bytes_repr[2:-1],
                f'b"{bytes_repr[2:-1]}"',
            }
        )

    for literal in tuple(literal_values):
        if not literal:
            continue
        try:
            escaped_unicode = literal.encode("unicode_escape").decode("ascii")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        variants.update(
            {
                escaped_unicode,
                escaped_unicode.upper(),
                escaped_unicode.replace("\\", "\\\\"),
                escaped_unicode.upper().replace("\\", "\\\\"),
            }
        )
    return frozenset(item for item in variants if item)


class DiagnosticLogger:
    """Thread-safe, rotating JSON Lines diagnostic sink.

    All public methods are non-throwing.  A filesystem or serialization error
    disables further writes, sets :attr:`healthy` to ``False``, and stores a
    sanitized human-readable explanation in :attr:`error`.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        enabled: bool = False,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        self._lock = threading.RLock()
        self._enabled = bool(enabled)
        self._healthy = True
        self._error: str | None = None
        self._closed = False
        self._stream: BinaryIO | None = None
        self._path: Path | None = None
        self._sequence = 0
        self._secret_refcounts: dict[frozenset[str], int] = {}
        self._max_bytes = self._bounded_positive_int(
            max_bytes, DEFAULT_MAX_BYTES, DEFAULT_MAX_BYTES
        )
        self._backup_count = self._bounded_positive_int(
            backup_count, DEFAULT_BACKUP_COUNT, DEFAULT_BACKUP_COUNT, allow_zero=True
        )

        if not self._enabled:
            return
        try:
            if path is None:
                raise ValueError("diagnostic log path is required")
            raw_path = os.fspath(path)
            if not isinstance(raw_path, str) or not raw_path:
                raise ValueError("diagnostic log path must be a non-empty string")
            expanded = os.path.expanduser(raw_path)
            self._path = Path(os.path.abspath(expanded))
            self._prepare_parent(self._path.parent)
            self._verify_private_parent(self._path.parent)
            self._stream = self._open_append_stream(self._path)
        except BaseException as exc:  # logging must never affect charger control
            self._fail_locked("setup", exc)

    @staticmethod
    def _bounded_positive_int(
        value: Any,
        default: int,
        maximum: int,
        *,
        allow_zero: bool = False,
    ) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return default
        minimum = 0 if allow_zero else 1
        if parsed < minimum:
            return default
        return min(parsed, maximum)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def healthy(self) -> bool:
        with self._lock:
            return self._healthy

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def backup_count(self) -> int:
        return self._backup_count

    @property
    def active(self) -> bool:
        with self._lock:
            return (
                self._enabled
                and self._healthy
                and not self._closed
                and self._stream is not None
            )

    def emit(self, category: str, event: str, /, **details: Any) -> bool:
        """Sanitize and append one event; return whether it was written.

        Raw key/text event names are rejected by policy.  UI integrations
        should log semantic actions such as ``button_clicked`` or
        ``shortcut_triggered``, never entered characters.
        """

        try:
            category_text = category if isinstance(category, str) else str(category)
            event_text = event if isinstance(event, str) else str(event)
        except BaseException:
            category_text = "unsupported"
            event_text = "unsupported"

        if _normalized_name(category_text) in {"keylogger", "raw_keyboard"}:
            return False
        if _normalized_name(event_text) in _FORBIDDEN_EVENT_NAMES:
            return False

        with self._lock:
            if not self.active:
                return False
            try:
                variants = self._active_secret_variants_locked()
                self._sequence += 1
                record = {
                    "schema_version": SCHEMA_VERSION,
                    "timestamp_utc": datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                    "monotonic_ns": time.monotonic_ns(),
                    "sequence": self._sequence,
                    "process": {
                        "pid": os.getpid(),
                        "name": self._redact_text(
                            Path(sys.argv[0]).name or "python", variants
                        ),
                    },
                    "thread": {
                        "id": threading.get_ident(),
                        "name": self._redact_text(
                            threading.current_thread().name, variants
                        ),
                    },
                    "category": self._redact_text(category_text, variants),
                    "event": self._redact_text(event_text, variants),
                    # This is the final sink-side recursive defense.
                    "details": self._sanitize(details, variants, seen=set(), depth=0),
                }
                encoded = (
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                self._write_line_locked(encoded)
                return True
            except BaseException as exc:  # logging must never affect charger control
                self._fail_locked("write", exc)
                return False

    @contextmanager
    def register_secret(self, secret: str | bytes | bytearray | memoryview) -> Iterator[None]:
        """Temporarily register secret renderings for sink-side redaction."""

        variants: frozenset[str] = frozenset()
        try:
            raw: str | bytes
            if isinstance(secret, str):
                raw = secret
            else:
                raw = bytes(secret)
            if raw:
                variants = _secret_variants(raw)
        except BaseException:
            variants = frozenset()

        if variants:
            with self._lock:
                self._secret_refcounts[variants] = self._secret_refcounts.get(variants, 0) + 1
        try:
            yield
        finally:
            if variants:
                with self._lock:
                    remaining = self._secret_refcounts.get(variants, 0) - 1
                    if remaining > 0:
                        self._secret_refcounts[variants] = remaining
                    else:
                        self._secret_refcounts.pop(variants, None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            stream = self._stream
            self._stream = None
            if stream is None:
                self._secret_refcounts.clear()
                return
            try:
                stream.flush()
                stream.close()
            except BaseException as exc:
                self._healthy = False
                self._error = self._safe_error_message("close", exc)
            finally:
                self._secret_refcounts.clear()

    def __enter__(self) -> DiagnosticLogger:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _active_secret_variants_locked(self) -> tuple[str, ...]:
        variants: set[str] = set()
        for group in self._secret_refcounts:
            variants.update(group)
        # Longer forms first prevents a short literal from partially masking a
        # richer representation and leaving a recoverable suffix.
        return tuple(sorted(variants, key=len, reverse=True))

    @staticmethod
    def _redact_text(text: str, variants: Sequence[str]) -> str:
        redacted = text
        for variant in variants:
            if variant:
                redacted = re.sub(
                    re.escape(variant),
                    lambda _match: REDACTED,
                    redacted,
                    flags=re.IGNORECASE,
                )
        return redacted

    def _sanitize(
        self,
        value: Any,
        variants: Sequence[str],
        *,
        seen: set[int],
        depth: int,
    ) -> Any:
        if depth > _MAX_NESTING:
            return TRUNCATED
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, str):
            return self._redact_text(value, variants)
        if isinstance(value, (bytes, bytearray, memoryview)):
            rendered = bytes(value).hex(" ").upper()
            return self._redact_text(rendered, variants)
        if isinstance(value, Enum):
            return self._sanitize(value.value, variants, seen=seen, depth=depth + 1)
        if isinstance(value, Path):
            return self._redact_text(str(value), variants)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, BaseException):
            try:
                message = str(value)
            except BaseException:
                message = UNSUPPORTED
            return {
                "type": self._redact_text(type(value).__name__, variants),
                "message": self._redact_text(message, variants),
            }

        identity = id(value)
        if identity in seen:
            return TRUNCATED

        if isinstance(value, Mapping):
            if _mapping_password_opcode(value):
                return {"opcode": _PASSWORD_OPCODE, "redacted": REDACTED}
            seen.add(identity)
            try:
                sanitized: dict[str, Any] = {}
                for key, item in value.items():
                    try:
                        key_text = key if isinstance(key, str) else str(key)
                    except BaseException:
                        key_text = UNSUPPORTED
                    safe_key = self._redact_text(key_text, variants)
                    if _is_sensitive_key(key_text):
                        sanitized[safe_key] = REDACTED
                    else:
                        sanitized[safe_key] = self._sanitize(
                            item, variants, seen=seen, depth=depth + 1
                        )
                return sanitized
            finally:
                seen.discard(identity)

        if isinstance(value, (Sequence, AbstractSet)) and not isinstance(
            value, (str, bytes, bytearray, memoryview)
        ):
            seen.add(identity)
            try:
                return [
                    self._sanitize(item, variants, seen=seen, depth=depth + 1)
                    for item in value
                ]
            finally:
                seen.discard(identity)

        # Unknown objects are represented by type only.  Calling repr() here
        # could itself leak credentials retained by a platform object.
        return {
            "type": self._redact_text(type(value).__name__, variants),
            "value": UNSUPPORTED,
        }

    def _prepare_parent(self, parent: Path) -> None:
        current = Path(parent.anchor)
        parts = parent.parts[1:] if parent.anchor else parent.parts
        for part in parts:
            current /= part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                os.mkdir(current, mode=0o700)
                metadata = os.lstat(current)
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise OSError(f"unsafe diagnostic directory: {current}")
                os.chmod(current, 0o700, follow_symlinks=False)
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise OSError(f"symlink in diagnostic path: {current}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise NotADirectoryError(f"diagnostic parent is not a directory: {current}")

    @staticmethod
    def _verify_private_parent(parent: Path) -> None:
        """Require a private destination without chmod-ing unrelated folders."""

        if os.name != "posix":
            return
        metadata = os.lstat(parent)
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o700:
            raise PermissionError(
                f"diagnostic directory must have mode 0700: {parent} has {mode:04o}"
            )

    def _open_append_stream(self, path: Path) -> BinaryIO:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if stat.S_ISLNK(metadata.st_mode):
                raise OSError(f"diagnostic log target is a symlink: {path}")
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError(f"diagnostic log target is not a regular file: {path}")

        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise OSError(f"diagnostic log target is not a regular file: {path}")
            current = os.lstat(path)
            if stat.S_ISLNK(current.st_mode) or (
                current.st_dev,
                current.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                raise OSError(f"diagnostic log target changed while opening: {path}")
            os.fchmod(descriptor, 0o600)
            if opened.st_size:
                os.lseek(descriptor, -1, os.SEEK_END)
                if os.read(descriptor, 1) != b"\n":
                    os.write(descriptor, b"\n")
            os.lseek(descriptor, 0, os.SEEK_END)
            return os.fdopen(descriptor, "ab", buffering=0)
        except BaseException:
            os.close(descriptor)
            raise

    def _write_line_locked(self, encoded: bytes) -> None:
        if self._stream is None or self._path is None:
            raise OSError("diagnostic log is unavailable")
        size = os.fstat(self._stream.fileno()).st_size
        if size > 0 and size + len(encoded) > self._max_bytes:
            self._rotate_locked()
        if self._stream is None:
            raise OSError("diagnostic log is unavailable after rotation")
        written = self._stream.write(encoded)
        if written != len(encoded):
            raise OSError("incomplete diagnostic log write")
        self._stream.flush()

    def _rotate_locked(self) -> None:
        if self._stream is None or self._path is None:
            raise OSError("diagnostic log is unavailable")
        self._stream.flush()
        self._stream.close()
        self._stream = None

        candidates = [
            Path(f"{self._path}.{index}")
            for index in range(1, self._backup_count + 1)
        ]
        for candidate in candidates:
            try:
                metadata = os.lstat(candidate)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise OSError(f"unsafe diagnostic rotation target: {candidate}")

        if self._backup_count == 0:
            os.unlink(self._path)
        else:
            oldest = candidates[-1]
            try:
                os.unlink(oldest)
            except FileNotFoundError:
                pass
            for index in range(self._backup_count - 1, 0, -1):
                source = Path(f"{self._path}.{index}")
                destination = Path(f"{self._path}.{index + 1}")
                try:
                    os.replace(source, destination)
                    os.chmod(destination, 0o600, follow_symlinks=False)
                except FileNotFoundError:
                    continue
            os.replace(self._path, candidates[0])
            os.chmod(candidates[0], 0o600, follow_symlinks=False)

        self._stream = self._open_append_stream(self._path)

    def _safe_error_message(self, stage: str, exc: BaseException) -> str:
        try:
            message = str(exc)
        except BaseException:
            message = type(exc).__name__
        variants = self._active_secret_variants_locked()
        message = self._redact_text(message, variants)
        return f"diagnostic logging {stage} failed: {type(exc).__name__}: {message}"

    def _fail_locked(self, stage: str, exc: BaseException) -> None:
        self._healthy = False
        self._error = self._safe_error_message(stage, exc)
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.close()
            except BaseException:
                pass


__all__ = [
    "DEFAULT_BACKUP_COUNT",
    "DEFAULT_MAX_BYTES",
    "DiagnosticLogger",
    "REDACTED",
    "SCHEMA_VERSION",
]
