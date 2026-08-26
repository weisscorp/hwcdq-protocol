"""Notification-stream reassembly for length-prefixed HWCDQ packets."""

from __future__ import annotations

from .errors import FrameStreamError
from . import protocol as codec


class FrameAssembler:
    """Accumulate arbitrary BLE notification chunks into complete packets.

    There is no sync word in this protocol.  Continuing after a corrupt length
    or checksum would risk silently treating payload bytes as a command, so a
    malformed stream clears the buffer and fails closed.
    """

    def __init__(self, *, maximum_frame_size: int = 256) -> None:
        if not 3 <= maximum_frame_size <= 256:
            raise ValueError("maximum frame size must be in 3..256")
        self.maximum_frame_size = maximum_frame_size
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, chunk: bytes | bytearray | memoryview) -> tuple[bytes, ...]:
        try:
            incoming = bytes(chunk)
        except (TypeError, ValueError) as exc:
            raise FrameStreamError("notification chunk must be bytes-like") from exc
        if not incoming:
            return ()

        self._buffer.extend(incoming)
        frames: list[bytes] = []
        try:
            while self._buffer:
                declared = self._buffer[0]
                total = declared + 1
                if declared < 2 or total > self.maximum_frame_size:
                    raise FrameStreamError(
                        f"invalid frame length byte 0x{declared:02X}"
                    )
                if len(self._buffer) < total:
                    break
                candidate = bytes(self._buffer[:total])
                del self._buffer[:total]
                try:
                    codec.decode_packet(candidate)
                except codec.ProtocolError as exc:
                    raise FrameStreamError(str(exc)) from exc
                frames.append(candidate)
        except FrameStreamError:
            self.reset()
            raise
        return tuple(frames)


__all__ = ["FrameAssembler"]
