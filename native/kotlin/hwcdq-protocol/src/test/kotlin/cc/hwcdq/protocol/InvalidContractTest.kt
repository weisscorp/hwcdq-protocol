package cc.hwcdq.protocol

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse

class InvalidContractTest {
    @Test
    fun `all shared native invalid vectors expose their dotted error code`() {
        val cases = ContractFiles.vector("invalid.json")
            .getJSONArray("invalid_cases")
            .objects()
            .filter { it.isRequiredByKotlin() }

        for (case in cases) {
            val failure = assertFailsWith<HwcdqProtocolException>(case.getString("id")) {
                invoke(case)
            }
            assertEquals(
                case.getString("expected_code"),
                failure.conformanceCode.wireName,
                case.getString("id"),
            )
        }
    }

    private fun invoke(case: org.json.JSONObject) {
        val arguments = case.getJSONObject("arguments")
        when (case.getString("operation")) {
            "decode_packet" -> {
                val packet = arguments.getString("packet_hex").hexBytes()
                assertFalse(HwcdqCodec.verifyChecksum(packet))
                HwcdqCodec.decode(packet)
            }
            "encode_set_voltage" ->
                HwcdqCodec.encodeSetVoltage(arguments.getJSONObject("value").vectorDouble())
            "encode_set_current" ->
                HwcdqCodec.encodeSetCurrent(arguments.getJSONObject("value").vectorDouble())
            "encode_check_password_credential", "encode_authentication_credential" ->
                HwcdqCodec.encodeCheckCredential(
                    Credential.fromDigest(arguments.getString("credential")),
                )
            else -> error("unhandled shared invalid operation: ${case.getString("operation")}")
        }
    }
}
