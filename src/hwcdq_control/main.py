"""Command-line entry point for the native HWCDQ Qt application."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication
from hwcdq import DiagnosticLogger
from hwcdq.profile import (
    APP_DISPLAY_NAME,
    LEGACY_SETTINGS_APPLICATION_NAME,
    LEGACY_SETTINGS_ORGANIZATION_NAME,
)

from . import __version__
from .qt_controller import AppController
from .ui import MainWindow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hwcdq-control",
        description="Native BLE bench controller for an HWCDQ smart charger",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="use the deterministic in-process charger; Bluetooth is not touched",
    )
    parser.add_argument(
        "--enable-output-controls",
        action="store_true",
        help="unlock evidence-backed set-voltage, set-current, and Start controls",
    )
    parser.add_argument(
        "--scan-seconds",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="BLE scan duration (default: 5)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="write secure local JSONL diagnostics (no network upload)",
    )
    parser.add_argument(
        "--debug-log",
        type=Path,
        metavar="PATH",
        help="diagnostic JSONL path; requires --debug",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help=(
            "resolve the macOS Bleak/CoreBluetooth runtime without scanning "
            "or requesting Bluetooth access"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def default_debug_log_path() -> Path:
    """Return the private directory that the logger creates on first use."""

    if getattr(sys, "frozen", False):
        return (
            Path.home()
            / "Library"
            / "Logs"
            / "HWCDQ Bench Control"
            / "hwcdq-debug.jsonl"
        )
    return Path.cwd() / "logs" / "hwcdq-debug.jsonl"


def corebluetooth_self_check() -> tuple[str, ...]:
    """Import the concrete macOS Bleak backend without constructing it.

    Importing these modules resolves PyObjC/CoreBluetooth bindings, but it does
    not create a scanner, central manager, or BLE client and therefore cannot
    trigger a Bluetooth permission request or communicate with a peripheral.
    """

    if sys.platform != "darwin":
        raise RuntimeError("CoreBluetooth self-check is supported only on macOS")

    modules = (
        "bleak.backends.corebluetooth.scanner",
        "bleak.backends.corebluetooth.client",
        "bleak.backends.service",
        "bleak.backends.corebluetooth.CentralManagerDelegate",
        "bleak.backends.corebluetooth.PeripheralDelegate",
        "bleak.backends.corebluetooth.utils",
    )
    for module_name in modules:
        importlib.import_module(module_name)
    return modules


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_check:
        try:
            modules = corebluetooth_self_check()
        except Exception as exc:
            print(f"HWCDQ CoreBluetooth self-check failed: {exc}", file=sys.stderr)
            return 1
        print(
            "HWCDQ CoreBluetooth self-check OK: "
            + ", ".join(modules)
        )
        return 0
    if args.scan_seconds <= 0:
        parser.error("--scan-seconds must be positive")
    if args.debug_log is not None and not args.debug:
        parser.error("--debug-log requires --debug")

    debug_path = args.debug_log or default_debug_log_path()
    diagnostics = DiagnosticLogger(debug_path, enabled=args.debug)
    if args.debug:
        rendered_path = diagnostics.path or debug_path.absolute()
        print(f"HWCDQ debug log: {rendered_path}", file=sys.stderr)
        if not diagnostics.active:
            print(
                "HWCDQ debug logging unavailable: "
                f"{diagnostics.error or 'unknown local logging error'}",
                file=sys.stderr,
            )
    diagnostics.emit(
        "app.lifecycle",
        "application_started",
        version=__version__,
        simulate=args.simulate,
        output_controls_enabled=args.enable_output_controls,
        scan_duration_seconds=args.scan_seconds,
    )

    try:
        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setApplicationName(APP_DISPLAY_NAME)
        app.setApplicationDisplayName(APP_DISPLAY_NAME)
        app.setOrganizationName(LEGACY_SETTINGS_ORGANIZATION_NAME)

        controller = AppController(
            simulate=args.simulate,
            output_controls_enabled=args.enable_output_controls,
            scan_duration=args.scan_seconds,
            diagnostics=diagnostics,
        )
        # Keep the established preference namespace even though the visible
        # product name changed. This preserves the remembered peripheral.
        settings = (
            None
            if args.simulate
            else QSettings(
                LEGACY_SETTINGS_ORGANIZATION_NAME,
                LEGACY_SETTINGS_APPLICATION_NAME,
            )
        )
        if args.simulate:
            initial_mode = "simulation"
        elif args.enable_output_controls:
            initial_mode = "control"
        else:
            initial_mode = "monitoring"
        window = MainWindow(
            controller,
            initial_mode=initial_mode,
            diagnostics=diagnostics,
            settings=settings,
        )

        shutdown_complete = False

        def shutdown() -> None:
            nonlocal shutdown_complete
            if shutdown_complete:
                return
            shutdown_complete = True
            diagnostics.emit("app.lifecycle", "shutdown_started")
            try:
                controller.shutdown()
            finally:
                diagnostics.emit("app.lifecycle", "shutdown_finished")
                diagnostics.close()

        app.aboutToQuit.connect(shutdown)
        window.show()
        diagnostics.emit(
            "app.lifecycle",
            "window_shown",
            initial_mode=initial_mode,
        )
        QTimer.singleShot(0, controller.announce_mode)
        try:
            return int(app.exec())
        finally:
            shutdown()
    except BaseException:
        diagnostics.emit("app.lifecycle", "startup_failed")
        diagnostics.close()
        raise


__all__ = [
    "build_parser",
    "corebluetooth_self_check",
    "default_debug_log_path",
    "main",
]
