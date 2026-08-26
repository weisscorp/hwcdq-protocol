package cc.hwcdq.protocol

/** One decoded packet together with the exact frame consumed from the stream. */
public class FramedPacket internal constructor(frame: ByteArray, public val packet: DecodedPacket) {
    private val value: ByteArray = frame.copyOf()
    public val frame: ByteArray
        get() = value.copyOf()
}

/**
 * Incremental frame boundary decoder for arbitrarily split or coalesced byte chunks.
 *
 * A bad length or checksum clears all buffered bytes because the protocol has no sync word.
 */
public class IncrementalFrameDecoder(
    public val maximumFrameSize: Int = HwcdqCodec.MAXIMUM_FRAME_BYTES,
) {
    private var buffer: ByteArray = byteArrayOf()

    public val bufferedByteCount: Int
        get() = buffer.size

    init {
        if (maximumFrameSize !in HwcdqCodec.MINIMUM_FRAME_BYTES..HwcdqCodec.MAXIMUM_FRAME_BYTES) {
            throw HwcdqProtocolException(
                ConformanceCode.STREAM_LENGTH_INVALID,
                "maximum frame size must be between ${HwcdqCodec.MINIMUM_FRAME_BYTES} and ${HwcdqCodec.MAXIMUM_FRAME_BYTES}",
            )
        }
    }

    public fun append(chunk: ByteArray): List<FramedPacket> {
        if (chunk.isEmpty()) {
            return emptyList()
        }
        buffer += chunk
        val decoded = mutableListOf<FramedPacket>()
        while (buffer.isNotEmpty()) {
            val remainingLength = buffer[0].toInt() and 0xFF
            val frameSize = remainingLength + 1
            if (remainingLength < 2 || frameSize > maximumFrameSize) {
                reset()
                throw HwcdqProtocolException(
                    ConformanceCode.STREAM_LENGTH_INVALID,
                    "candidate frame length $frameSize is outside the configured stream boundary",
                )
            }
            if (buffer.size < frameSize) {
                break
            }
            val frame = buffer.copyOfRange(0, frameSize)
            buffer = buffer.copyOfRange(frameSize, buffer.size)
            val packet = try {
                HwcdqCodec.decode(frame)
            } catch (cause: HwcdqProtocolException) {
                reset()
                throw HwcdqProtocolException(
                    ConformanceCode.STREAM_FRAME_INVALID,
                    "candidate frame failed packet validation",
                    cause,
                )
            }
            decoded += FramedPacket(frame, packet)
        }
        return decoded
    }

    public fun reset() {
        buffer = byteArrayOf()
    }
}
