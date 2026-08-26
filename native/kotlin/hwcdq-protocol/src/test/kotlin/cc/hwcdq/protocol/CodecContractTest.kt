package cc.hwcdq.protocol

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class CodecContractTest {
    private val contract = ContractFiles.vector("codec.json")

    @Test
    fun `all shared native encoder vectors match exactly`() {
        for (case in contract.getJSONArray("encode_cases").objects().filter { it.isRequiredByKotlin() }) {
            val arguments = case.getJSONObject("arguments")
            val actual = when (case.getString("operation")) {
                "encode_get_firmware" -> HwcdqCodec.encodeGetFirmware()
                "encode_get_serial" -> HwcdqCodec.encodeGetSerial()
                "encode_get_config" -> HwcdqCodec.encodeGetConfig()
                "encode_get_telemetry" -> HwcdqCodec.encodeGetTelemetry()
                "encode_authentication_apk_fallback" ->
                    HwcdqCodec.encodeCheckCredential(Credential.apkFallback)
                "encode_authentication_credential" ->
                    HwcdqCodec.encodeCheckCredential(
                        Credential.fromDigest(arguments.getString("credential")),
                    )
                "encode_set_voltage" ->
                    HwcdqCodec.encodeSetVoltage(arguments.getJSONObject("value").vectorDouble())
                "encode_set_current" ->
                    HwcdqCodec.encodeSetCurrent(arguments.getJSONObject("value").vectorDouble())
                "encode_start" -> HwcdqCodec.encodeStart()
                "encode_stop" -> HwcdqCodec.encodeStop()
                else -> error("unhandled shared encoder: ${case.getString("operation")}")
            }
            val expected = case.getJSONObject("expected")
            assertContentEquals(
                expected.getString("frame_hex").hexBytes(),
                actual,
                case.getString("id"),
            )

            val decoded = HwcdqCodec.decode(actual)
            assertEquals(expected.getInt("opcode"), decoded.opcode, case.getString("id"))
            assertEquals(expected.getString("payload_hex"), decoded.payload.lowerHex(), case.getString("id"))
            assertEquals(expected.getString("checksum_hex"), decoded.checksum.toString(16).padStart(2, '0'))
            assertTrue(HwcdqCodec.verifyChecksum(decoded.opcode, decoded.payload, decoded.checksum))
            assertFalse(HwcdqCodec.verifyChecksum(decoded.opcode, decoded.payload, decoded.checksum xor 0xFF))
            assertTrue(HwcdqCodec.verifyChecksum(actual))
            val badFrame = actual.copyOf().also { it[it.lastIndex] = (it.last().toInt() xor 0x01).toByte() }
            assertFalse(HwcdqCodec.verifyChecksum(badFrame))
        }
    }

    @Test
    fun `credential canonicalization vectors are observable only through encoding`() {
        for (case in contract.getJSONArray("credential_cases").objects().filter { it.isRequiredByKotlin() }) {
            val credential = when (case.getString("operation")) {
                "apk_fallback_credential" -> Credential.apkFallback
                "canonicalize_direct_credential" ->
                    Credential.fromDigest(case.getJSONObject("arguments").getString("digest"))
                else -> error("unhandled shared credential case: ${case.getString("operation")}")
            }
            val encoded = HwcdqCodec.encodeCheckCredential(credential)
            val expected = case.getJSONObject("expected")
            if (expected.has("frame_hex")) {
                assertEquals(expected.getString("frame_hex"), encoded.lowerHex(), case.getString("id"))
            } else {
                assertEquals(
                    expected.getString("ascii_hex"),
                    encoded.copyOfRange(2, encoded.lastIndex - 1).lowerHex(),
                    case.getString("id"),
                )
            }
        }
    }

    @Test
    fun `credential public surface is redacted and direct-digest only`() {
        val digest = contract.getJSONArray("credential_cases")
            .objects()
            .first { it.getString("operation") == "canonicalize_direct_credential" }
            .getJSONObject("arguments")
            .getString("digest")
        val credential = Credential.fromDigest(digest)
        assertFalse(credential.toString().contains(digest, ignoreCase = true))

        val visibleNames = Credential::class.java.methods
            .filterNot { it.isSynthetic }
            .map { it.name.lowercase() }
        assertFalse(visibleNames.any { it == "getdigest" || it == "towirebytes" })
        assertFalse(visibleNames.any { "password" in it || "md5" in it })
    }
}
