from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT))

from hwcdq_control.main import (  # noqa: E402
    build_parser,
    corebluetooth_self_check,
    default_debug_log_path,
    main,
)
from PySide6.QtWidgets import QApplication  # noqa: E402


class MainCliTests(unittest.TestCase):
    def test_self_check_is_explicit_and_off_by_default(self) -> None:
        self.assertFalse(build_parser().parse_args([]).self_check)
        self.assertTrue(build_parser().parse_args(["--self-check"]).self_check)

    def test_self_check_exits_before_qt_or_diagnostics_start(self) -> None:
        modules = (
            "bleak.backends.corebluetooth.scanner",
            "bleak.backends.corebluetooth.client",
            "bleak.backends.service",
            "bleak.backends.corebluetooth.CentralManagerDelegate",
            "bleak.backends.corebluetooth.PeripheralDelegate",
            "bleak.backends.corebluetooth.utils",
        )
        with (
            patch("hwcdq_control.main.corebluetooth_self_check", return_value=modules),
            patch("hwcdq_control.main.DiagnosticLogger") as logger,
            patch("hwcdq_control.main.QApplication") as application,
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["--self-check"]), 0)
        logger.assert_not_called()
        application.assert_not_called()
        output.assert_called_once()
        self.assertIn("self-check OK", output.call_args.args[0])

    def test_self_check_reports_import_failure_without_starting_qt(self) -> None:
        with (
            patch(
                "hwcdq_control.main.corebluetooth_self_check",
                side_effect=ImportError("missing frozen backend"),
            ),
            patch("hwcdq_control.main.QApplication") as application,
            patch("builtins.print") as output,
        ):
            self.assertEqual(main(["--self-check"]), 1)
        application.assert_not_called()
        self.assertIn("self-check failed", output.call_args.args[0])

    def test_corebluetooth_self_check_imports_modules_without_constructing_ble(self) -> None:
        imported: list[str] = []

        def remember(module_name: str) -> object:
            imported.append(module_name)
            return object()

        with (
            patch("hwcdq_control.main.sys.platform", "darwin"),
            patch("hwcdq_control.main.importlib.import_module", side_effect=remember),
        ):
            resolved = corebluetooth_self_check()

        self.assertEqual(tuple(imported), resolved)
        self.assertEqual(
            resolved,
            (
                "bleak.backends.corebluetooth.scanner",
                "bleak.backends.corebluetooth.client",
                "bleak.backends.service",
                "bleak.backends.corebluetooth.CentralManagerDelegate",
                "bleak.backends.corebluetooth.PeripheralDelegate",
                "bleak.backends.corebluetooth.utils",
            ),
        )

    def test_debug_flags_are_explicit_and_debug_is_off_by_default(self) -> None:
        defaults = build_parser().parse_args([])
        self.assertFalse(defaults.debug)
        self.assertIsNone(defaults.debug_log)

        configured = build_parser().parse_args(
            ["--debug", "--debug-log", "private/debug.jsonl"]
        )
        self.assertTrue(configured.debug)
        self.assertEqual(configured.debug_log, Path("private/debug.jsonl"))

    def test_default_debug_path_is_under_a_dedicated_private_directory(self) -> None:
        working_directory = Path("/private/tmp/hwcdq-cli-test")
        with (
            patch.object(Path, "cwd", return_value=working_directory),
            patch("hwcdq_control.main.sys.frozen", False, create=True),
        ):
            self.assertEqual(
                default_debug_log_path(),
                working_directory / "logs" / "hwcdq-debug.jsonl",
            )

    def test_frozen_debug_path_does_not_depend_on_launchservices_cwd(self) -> None:
        home_directory = Path("/Users/example")
        with (
            patch.object(Path, "home", return_value=home_directory),
            patch("hwcdq_control.main.sys.frozen", True, create=True),
        ):
            self.assertEqual(
                default_debug_log_path(),
                home_directory
                / "Library"
                / "Logs"
                / "HWCDQ Bench Control"
                / "hwcdq-debug.jsonl",
            )

    def test_debug_log_without_debug_is_rejected_before_qt_starts(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["--debug-log", "private/debug.jsonl"])
        self.assertEqual(raised.exception.code, 2)

    def test_nonpositive_scan_duration_is_rejected_before_qt_starts(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["--scan-seconds", "0"])
        self.assertEqual(raised.exception.code, 2)

    def test_disabled_debug_mode_creates_no_default_log(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as directory:
            path = Path(directory) / "logs" / "hwcdq-debug.jsonl"
            with (
                patch(
                    "hwcdq_control.main.default_debug_log_path",
                    return_value=path,
                ),
                patch.object(QApplication, "exec", return_value=0),
            ):
                self.assertEqual(main(["--simulate"]), 0)
            self.assertFalse(path.exists())
            self.assertFalse(path.parent.exists())

    def test_simulation_never_constructs_native_device_preferences(self) -> None:
        with (
            patch("hwcdq_control.main.QSettings") as settings,
            patch.object(QApplication, "exec", return_value=0),
        ):
            self.assertEqual(main(["--simulate"]), 0)
        settings.assert_not_called()

    def test_real_mode_injects_native_settings_into_window(self) -> None:
        marker = object()
        with (
            patch("hwcdq_control.main.QSettings", return_value=marker) as settings,
            patch("hwcdq_control.main.AppController") as controller_type,
            patch("hwcdq_control.main.MainWindow") as window_type,
            patch.object(QApplication, "exec", return_value=0),
        ):
            self.assertEqual(main([]), 0)

        settings.assert_called_once_with(
            "HWCDQ interoperability",
            "HWCDQ Bench Control",
        )
        self.assertIs(window_type.call_args.kwargs["settings"], marker)
        controller_type.return_value.shutdown.assert_called_once_with()

    def test_visible_application_name_changes_without_renaming_the_cli(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.prog, "hwcdq-control")
        with patch.object(QApplication, "exec", return_value=0):
            self.assertEqual(main(["--simulate"]), 0)
        self.assertEqual(
            QApplication.applicationDisplayName(),
            "Pidzoom Portable charger HW178P",
        )
        self.assertEqual(
            QApplication.applicationName(),
            "Pidzoom Portable charger HW178P",
        )

    def test_debug_cli_writes_startup_and_ordered_shutdown_events(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as directory:
            path = Path(directory) / "debug" / "hwcdq-debug.jsonl"
            with patch.object(QApplication, "exec", return_value=0):
                self.assertEqual(
                    main(
                        [
                            "--simulate",
                            "--debug",
                            "--debug-log",
                            str(path),
                        ]
                    ),
                    0,
                )
            self.assertTrue(path.is_file())
            events = [
                json.loads(line)["event"]
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[0], "application_started")
            self.assertIn("window_shown", events)
            self.assertLess(events.index("shutdown_started"), events.index("shutdown_finished"))


if __name__ == "__main__":
    unittest.main()
