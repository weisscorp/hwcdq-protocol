from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from hwcdq_control.diagnostics import (  # noqa: E402
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    DiagnosticLogger,
    REDACTED,
    SCHEMA_VERSION,
)


def read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def rotation_paths(path: Path) -> list[Path]:
    backups = [Path(f"{path}.{index}") for index in range(3, 0, -1)]
    return [candidate for candidate in backups + [path] if candidate.exists()]


class DiagnosticLoggerTests(unittest.TestCase):
    def test_disabled_logger_is_noop_and_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "not-created" / "debug.jsonl"
            logger = DiagnosticLogger(path)
            self.assertFalse(logger.enabled)
            self.assertTrue(logger.healthy)
            self.assertFalse(logger.active)
            self.assertFalse(logger.emit("ui", "button_clicked", button="scan"))
            logger.close()
            self.assertFalse(path.exists())
            self.assertFalse(path.parent.exists())

    def test_schema_sequence_and_process_thread_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "logs" / "debug.jsonl"
            with DiagnosticLogger(path, enabled=True) as logger:
                self.assertTrue(logger.emit("ui", "button_clicked", button="scan"))
                self.assertTrue(logger.emit("ble", "scan_started", timeout=5.0))

            records = read_records(path)
            self.assertEqual([item["sequence"] for item in records], [1, 2])
            for record in records:
                self.assertEqual(record["schema_version"], SCHEMA_VERSION)
                self.assertRegex(record["timestamp_utc"], r"Z$")
                self.assertIsInstance(record["monotonic_ns"], int)
                self.assertEqual(record["process"]["pid"], os.getpid())
                self.assertIsInstance(record["process"]["name"], str)
                self.assertIsInstance(record["thread"]["id"], int)
                self.assertIsInstance(record["thread"]["name"], str)
            self.assertLessEqual(records[0]["monotonic_ns"], records[1]["monotonic_ns"])

    def test_concurrent_writers_produce_complete_ordered_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True)

            def worker(worker_id: int) -> None:
                for index in range(80):
                    self.assertTrue(
                        logger.emit(
                            "worker",
                            "step",
                            worker_id=worker_id,
                            index=index,
                        )
                    )

            threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            logger.close()

            records = read_records(path)
            self.assertEqual(len(records), 480)
            self.assertEqual([item["sequence"] for item in records], list(range(1, 481)))

    def test_rotation_preserves_line_integrity_order_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "logs" / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True, max_bytes=650, backup_count=3)
            for index in range(12):
                self.assertTrue(logger.emit("ble", "packet", index=index, data="x" * 80))
            logger.close()

            paths = rotation_paths(path)
            self.assertGreaterEqual(len(paths), 2)
            records = [record for candidate in paths for record in read_records(candidate)]
            sequences = [record["sequence"] for record in records]
            self.assertEqual(sequences, sorted(sequences))
            self.assertEqual(len(sequences), len(set(sequences)))
            self.assertEqual(sequences[-1], 12)
            for candidate in paths:
                self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_rotation_configuration_is_capped_at_safe_defaults(self) -> None:
        logger = DiagnosticLogger(
            enabled=False,
            max_bytes=DEFAULT_MAX_BYTES * 10,
            backup_count=99,
        )
        self.assertEqual(logger.max_bytes, DEFAULT_MAX_BYTES)
        self.assertEqual(logger.backup_count, DEFAULT_BACKUP_COUNT)

    def test_existing_regular_file_is_appended_not_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            original = {"existing": True}
            path.write_text(json.dumps(original), encoding="utf-8")
            logger = DiagnosticLogger(path, enabled=True)
            self.assertTrue(logger.emit("app", "started"))
            logger.close()
            records = read_records(path)
            self.assertEqual(records[0], original)
            self.assertEqual(records[1]["event"], "started")

    @unittest.skipUnless(os.name == "posix", "POSIX mode checks unavailable")
    def test_existing_parent_must_already_be_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            parent = base / "existing"
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            path = parent / "debug.jsonl"

            rejected = DiagnosticLogger(path, enabled=True)
            self.assertFalse(rejected.healthy)
            self.assertIn("mode 0700", rejected.error or "")
            self.assertFalse(path.exists())
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)

            parent.chmod(0o700)
            accepted = DiagnosticLogger(path, enabled=True)
            self.assertTrue(accepted.active)
            self.assertTrue(accepted.emit("app", "started"))
            accepted.close()
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_target_and_parent_fail_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            real_directory = base / "real"
            real_directory.mkdir()
            parent_link = base / "linked"
            parent_link.symlink_to(real_directory, target_is_directory=True)

            parent_logger = DiagnosticLogger(parent_link / "debug.jsonl", enabled=True)
            self.assertFalse(parent_logger.healthy)
            self.assertFalse(parent_logger.emit("app", "started"))
            self.assertIn("symlink", parent_logger.error or "")

            real_file = base / "real.jsonl"
            real_file.touch()
            file_link = base / "debug.jsonl"
            file_link.symlink_to(real_file)
            target_logger = DiagnosticLogger(file_link, enabled=True)
            self.assertFalse(target_logger.healthy)
            self.assertFalse(target_logger.emit("app", "started"))
            self.assertIn("symlink", target_logger.error or "")

    def test_setup_and_write_failures_are_non_throwing_and_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent_file = Path(directory).resolve() / "parent"
            parent_file.touch()
            setup_logger = DiagnosticLogger(parent_file / "debug.jsonl", enabled=True)
            self.assertFalse(setup_logger.healthy)
            self.assertIsNotNone(setup_logger.error)

            path = Path(directory).resolve() / "working.jsonl"
            write_logger = DiagnosticLogger(path, enabled=True)
            with mock.patch.object(
                write_logger,
                "_write_line_locked",
                side_effect=OSError("synthetic disk failure"),
            ):
                self.assertFalse(write_logger.emit("ble", "write"))
            self.assertFalse(write_logger.healthy)
            self.assertIn("synthetic disk failure", write_logger.error or "")
            self.assertFalse(write_logger.emit("ble", "write_again"))
            write_logger.close()

    def test_close_flushes_and_prevents_later_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True)
            self.assertTrue(logger.emit("app", "shutdown_requested"))
            logger.close()
            self.assertEqual(read_records(path)[0]["event"], "shutdown_requested")
            size = path.stat().st_size
            self.assertFalse(logger.emit("app", "after_close"))
            self.assertEqual(path.stat().st_size, size)

    def test_recursive_redaction_handles_sensitive_keys_and_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True)
            cyclic: dict[str, object] = {}
            cyclic["self"] = cyclic
            self.assertTrue(
                logger.emit(
                    "auth",
                    "result",
                    nested={
                        "password": "one",
                        "api-token": "two",
                        "credential_blob": {"value": "three"},
                        "ordinary": "visible",
                    },
                    cyclic=cyclic,
                    nonfinite=float("nan"),
                )
            )
            logger.close()
            details = read_records(path)[0]["details"]
            self.assertEqual(details["nested"]["password"], REDACTED)
            self.assertEqual(details["nested"]["api-token"], REDACTED)
            self.assertEqual(details["nested"]["credential_blob"], REDACTED)
            self.assertEqual(details["nested"]["ordinary"], "visible")
            self.assertEqual(details["cyclic"]["self"], "[TRUNCATED]")
            self.assertEqual(details["nonfinite"], "nan")

    def test_registered_ascii_and_unicode_secret_renderings_never_reach_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True, max_bytes=500)
            secrets = ("hunter2", "пароль")
            for secret in secrets:
                encoded = secret.encode("utf-8")
                renderings = [
                    secret,
                    encoded.hex(),
                    "HuNtEr2" if secret == "hunter2" else encoded.hex().swapcase(),
                    encoded.hex(" ").upper(),
                    repr(encoded),
                    "".join(f"\\x{byte:02x}" for byte in encoded),
                    secret.encode("unicode_escape").decode("ascii"),
                ]
                with logger.register_secret(secret):
                    for rendering in renderings:
                        self.assertTrue(logger.emit("transport", "exception", message=rendering))
            logger.close()

            persisted = b"".join(candidate.read_bytes() for candidate in rotation_paths(path))
            for secret in secrets:
                encoded = secret.encode("utf-8")
                forbidden = (
                    secret.encode("utf-8"),
                    encoded.hex().encode("ascii"),
                    repr(encoded).encode("ascii"),
                    "".join(f"\\x{byte:02x}" for byte in encoded).encode("ascii"),
                    secret.encode("unicode_escape"),
                )
                for rendering in forbidden:
                    self.assertNotIn(rendering, persisted)
            self.assertIn(REDACTED.encode("utf-8"), persisted)
            for candidate in rotation_paths(path):
                read_records(candidate)  # every retained line is valid JSON

    def test_registered_secret_is_removed_from_process_and_thread_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True)
            original_argv = sys.argv[0]
            current_thread = threading.current_thread()
            original_thread_name = current_thread.name
            try:
                sys.argv[0] = "/tmp/metadata-secret"
                current_thread.name = "metadata-secret"
                with logger.register_secret("metadata-secret"):
                    self.assertTrue(logger.emit("app", "metadata"))
            finally:
                sys.argv[0] = original_argv
                current_thread.name = original_thread_name
                logger.close()

            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn("metadata-secret", persisted)
            record = read_records(path)[0]
            self.assertEqual(record["process"]["name"], REDACTED)
            self.assertEqual(record["thread"]["name"], REDACTED)

    def test_password_opcode_discards_all_secret_derived_packet_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True, max_bytes=500)
            secret = "0000"
            with logger.register_secret(secret):
                for index in range(8):
                    self.assertTrue(
                        logger.emit(
                            "ble",
                            "tx",
                            opcode="0x02",
                            payload=secret,
                            raw=b"\x55\x02\x30\x30\x30\x30",
                            frame="55 02 30 30 30 30",
                            chunk="30303030",
                            length=4,
                            checksum=0x1234,
                            transformed={"password": secret},
                            iteration=index,
                        )
                    )
            logger.close()

            sanitized_details: list[bytes] = []
            for candidate in rotation_paths(path):
                for record in read_records(candidate):
                    self.assertEqual(
                        record["details"],
                        {"opcode": 2, "redacted": REDACTED},
                    )
                    sanitized_details.append(
                        json.dumps(record["details"], sort_keys=True).encode("utf-8")
                    )
            persisted = b"\n".join(sanitized_details)
            for forbidden in (b"0000", b"30303030", b"30 30 30 30", b"checksum", b"length"):
                self.assertNotIn(forbidden, persisted)

    def test_nested_password_packet_is_redacted_without_dropping_outer_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True)
            self.assertTrue(
                logger.emit(
                    "queue",
                    "submitted",
                    transaction_id="safe-id",
                    packet={"opcode": 2, "payload": "should-not-survive", "length": 18},
                )
            )
            logger.close()
            details = read_records(path)[0]["details"]
            self.assertEqual(details["transaction_id"], "safe-id")
            self.assertEqual(details["packet"], {"opcode": 2, "redacted": REDACTED})

    def test_raw_keystroke_event_types_are_rejected_but_shortcuts_are_semantic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "debug.jsonl"
            logger = DiagnosticLogger(path, enabled=True)
            self.assertFalse(logger.emit("ui", "key_press", key="A"))
            self.assertFalse(logger.emit("keylogger", "captured", value="A"))
            self.assertTrue(logger.emit("ui", "shortcut_triggered", action="stop"))
            logger.close()
            records = read_records(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["event"], "shortcut_triggered")


if __name__ == "__main__":
    unittest.main()
