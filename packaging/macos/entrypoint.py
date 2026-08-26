"""Dedicated PyInstaller entry point for the signed macOS application."""

from hwcdq_control.main import main


if __name__ == "__main__":
    raise SystemExit(main())
