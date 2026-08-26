"""Deprecated import shim for :mod:`hwcdq.gatt`."""

from hwcdq.gatt import (
    chunks_for_write,
    resolve_wnr_chunk_size,
    select_hwcdq_topology,
    short_uuid,
)


__all__ = [
    "chunks_for_write",
    "resolve_wnr_chunk_size",
    "select_hwcdq_topology",
    "short_uuid",
]
