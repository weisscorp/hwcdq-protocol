package cc.hwcdq.protocol

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class FramingContractTest {
    @Test
    fun `all shared incremental framing vectors pass`() {
        val cases = ContractFiles.vector("framing.json").getJSONArray("cases").objects()
        for (case in cases) {
            val decoder = IncrementalFrameDecoder(
                maximumFrameSize = case.optInt("maximum_frame_size", HwcdqCodec.MAXIMUM_FRAME_BYTES),
            )
            val frames = mutableListOf<FramedPacket>()
            val exercise = {
                for (chunk in case.getJSONArray("chunks_hex").stringValues()) {
                    frames += decoder.append(chunk.hexBytes())
                }
            }

            if (case.has("expected_code")) {
                val failure = assertFailsWith<HwcdqProtocolException>(case.getString("id"), exercise)
                assertEquals(case.getString("expected_code"), failure.conformanceCode.wireName, case.getString("id"))
            } else {
                exercise()
            }

            assertEquals(
                case.getJSONArray("expected_frames_hex").stringValues(),
                frames.map { it.frame.lowerHex() },
                case.getString("id"),
            )
            assertEquals(case.getInt("expected_buffered_bytes"), decoder.bufferedByteCount, case.getString("id"))

            if (case.has("post_error_chunks_hex")) {
                val recovered = case.getJSONArray("post_error_chunks_hex")
                    .stringValues()
                    .flatMap { decoder.append(it.hexBytes()) }
                assertEquals(
                    case.getJSONArray("post_error_frames_hex").stringValues(),
                    recovered.map { it.frame.lowerHex() },
                    "${case.getString("id")} recovery",
                )
            }
        }
    }
}
