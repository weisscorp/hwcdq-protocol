"""Deprecated import shim for :mod:`hwcdq.bleak`."""

from hwcdq.bleak import BleakScanner as BleakScannerAdapter, BleakTransport


__all__ = ["BleakScannerAdapter", "BleakTransport"]
