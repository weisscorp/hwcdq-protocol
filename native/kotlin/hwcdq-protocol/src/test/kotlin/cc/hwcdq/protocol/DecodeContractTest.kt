package cc.hwcdq.protocol

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue
import org.json.JSONObject

class DecodeContractTest {
    @Test
    fun `all shared decode vectors retain layout and semantic meaning`() {
        val cases = ContractFiles.vector("decode.json").getJSONArray("decode_cases").objects()
        for (case in cases) {
            val packet = HwcdqCodec.decode(case.getString("packet_hex").hexBytes())
            val expected = case.getJSONObject("expected")
            assertEquals(expected.getInt("opcode"), packet.opcode, case.getString("id"))
            assertEquals(expected.getString("command"), packet.command, case.getString("id"))
            assertEquals(expected.getInt("payload_length"), packet.payload.size, case.getString("id"))
            assertEquals(case.getString("packet_hex"), packet.rawFrame.lowerHex(), case.getString("id"))
            assertEquals(packet.rawFrame.size - 1, packet.declaredRemainingLength)
            assertTrue(HwcdqCodec.verifyChecksum(packet.opcode, packet.payload, packet.checksum))

            for (field in expected.getJSONArray("fields").objects()) {
                assertField(case.getString("id"), packet, field)
            }
        }
    }

    @Test
    fun `decoded byte arrays are defensive copies`() {
        val case = ContractFiles.vector("decode.json")
            .getJSONArray("decode_cases")
            .objects()
            .first { it.getString("id") == "unknown_opcode_preserves_payload" }
        val packet = HwcdqCodec.decode(case.getString("packet_hex").hexBytes())
        val originalFrame = packet.rawFrame.lowerHex()
        val originalPayload = packet.payload.lowerHex()
        packet.rawFrame.fill(0)
        packet.payload.fill(0)
        assertEquals(originalFrame, packet.rawFrame.lowerHex())
        assertEquals(originalPayload, packet.payload.lowerHex())
        val unknown = packet.meaning as UnknownPacket
        unknown.payload.fill(0)
        assertEquals(originalPayload, unknown.payload.lowerHex())

        val configCase = ContractFiles.vector("decode.json")
            .getJSONArray("decode_cases")
            .objects()
            .first { it.getString("id") == "config_synthetic_complete_layout" }
        val config = (HwcdqCodec.decode(configCase.getString("packet_hex").hexBytes()).meaning as
            PacketMeaning.ConfigurationResponse).config
        val rawAscii = config.rawAscii23.lowerHex()
        val language = config.displayLanguageRaw.lowerHex()
        config.rawAscii23.fill(0)
        config.displayLanguageRaw.fill(0)
        assertEquals(rawAscii, config.rawAscii23.lowerHex())
        assertEquals(language, config.displayLanguageRaw.lowerHex())
    }

    @Test
    fun `known malformed payloads retain stable operation labels`() {
        val cases = mapOf(
            Opcode.CHECK_CREDENTIAL to "check_password_unknown_payload",
            Opcode.GET_CONFIG to "config_response",
            Opcode.GET_TELEMETRY to "telemetry_opcode_unknown_payload",
            Opcode.SET_VOLTAGE to "set_voltage",
            Opcode.SET_CURRENT to "set_current",
            Opcode.OUTPUT_CONTROL to "output_control",
        )
        for ((opcode, expectedCommand) in cases) {
            val payload = byteArrayOf(0x02, 0x03)
            val frame = byteArrayOf(
                (payload.size + 2).toByte(),
                opcode.value.toByte(),
                *payload,
                HwcdqCodec.checksum(opcode.value, payload).toByte(),
            )
            val packet = HwcdqCodec.decode(frame)
            assertEquals(expectedCommand, packet.command, opcode.name)
            assertTrue(packet.meaning is PacketMeaning.KnownOpcodeUnknownPayload)
        }
    }

    private fun assertField(caseId: String, packet: DecodedPacket, field: JSONObject) {
        val actual = fieldValue(packet, field.getString("path"))
        when (field.getString("type")) {
            "boolean" -> assertEquals(field.getBoolean("value"), actual, "$caseId ${field.getString("path")}")
            "integer" -> assertEquals(field.getNumber("value").toInt(), actual, "$caseId ${field.getString("path")}")
            "null" -> assertNull(actual, "$caseId ${field.getString("path")}")
            "bytes" -> assertEquals(field.getString("hex"), (actual as ByteArray).lowerHex(), "$caseId ${field.getString("path")}")
            "f32" -> {
                val value = actual as Float
                assertEquals(field.getString("f32le_hex"), value.littleEndianHex(), "$caseId ${field.getString("path")}")
            }
            "number" -> assertEquals(
                field.getString("decimal").toDouble(),
                actual as Double,
                1e-12,
                "$caseId ${field.getString("path")}",
            )
            else -> error("unhandled field type ${field.getString("type")}")
        }
    }

    private fun fieldValue(packet: DecodedPacket, path: String): Any? {
        val meaning = packet.meaning
        return when (path) {
            "acknowledged" -> (meaning as PacketMeaning.Acknowledgement).acknowledged
            "state" -> (meaning as PacketMeaning.OutputControl).state
            "state_valid" -> (meaning as PacketMeaning.OutputControl).stateValid
            "enabled" -> (meaning as PacketMeaning.OutputControl).enabled
            "payload" -> packet.payload
            "credential_format_valid" ->
                (meaning as PacketMeaning.CredentialRequest).credentialFormatValid
            else -> if (path.startsWith("config.")) {
                configurationField((meaning as PacketMeaning.ConfigurationResponse).config, path.removePrefix("config."))
            } else if (path.startsWith("telemetry.")) {
                telemetryField((meaning as PacketMeaning.TelemetryResponse).telemetry, path.removePrefix("telemetry."))
            } else {
                error("unhandled field path $path")
            }
        }
    }

    @Suppress("CyclomaticComplexMethod")
    private fun configurationField(config: Configuration, field: String): Any = when (field) {
        "target_voltage" -> config.targetVoltage
        "target_current" -> config.targetCurrent
        "offline_voltage" -> config.offlineVoltage
        "offline_current" -> config.offlineCurrent
        "power_on_output" -> config.powerOnOutput
        "voltage_calibration" -> config.voltageCalibration
        "voltage_feedback_calibration" -> config.voltageFeedbackCalibration
        "current_calibration" -> config.currentCalibration
        "current_feedback_calibration" -> config.currentFeedbackCalibration
        "max_voltage" -> config.maxVoltage
        "max_single_module_current" -> config.maxSingleModuleCurrent
        "auto_stop" -> config.autoStop
        "shutdown_current" -> config.shutdownCurrent
        "raw_u8_46" -> config.rawU8At46
        "temperature_protection" -> config.temperatureProtection
        "raw_u8_48" -> config.rawU8At48
        "protection_cutoff_temperature" -> config.protectionCutoffTemperature
        "fan_boost_temperature" -> config.fanBoostTemperature
        "fan_max_temperature" -> config.fanMaxTemperature
        "raw_ascii_23" -> config.rawAscii23
        "two_stage_charging" -> config.twoStageCharging
        "secondary_voltage" -> config.secondaryVoltage
        "secondary_current" -> config.secondaryCurrent
        "offline_control" -> config.offlineControl
        "raw_u8_85" -> config.rawU8At85
        "soft_start_coefficient" -> config.softStartCoefficient
        "power_limit" -> config.powerLimit
        "max_power" -> config.maxPower
        "display_language_raw" -> config.displayLanguageRaw
        "raw_u8_99" -> config.rawU8At99
        "raw_u8_100" -> config.rawU8At100
        "raw_u8_101" -> config.rawU8At101
        "raw_u8_102" -> config.rawU8At102
        else -> error("unhandled config field $field")
    }

    private fun telemetryField(telemetry: Telemetry, field: String): Any? = when (field) {
        "input_voltage" -> telemetry.inputVoltage
        "input_current" -> telemetry.inputCurrent
        "input_frequency" -> telemetry.inputFrequency
        "temperature_1" -> telemetry.temperature1
        "temperature_2" -> telemetry.temperature2
        "output_voltage" -> telemetry.outputVoltage
        "output_current" -> telemetry.outputCurrent
        "current_point" -> telemetry.currentPoint
        "efficiency" -> telemetry.efficiency
        "current_output" -> telemetry.currentOutput
        "output_enabled" -> telemetry.outputEnabled
        "accumulated_capacity_ah" -> telemetry.accumulatedCapacityAh
        "accumulated_energy_wh" -> telemetry.accumulatedEnergyWh
        "module_count" -> telemetry.moduleCount
        "input_power_w" -> telemetry.inputPowerW
        "output_power_w" -> telemetry.outputPowerW
        else -> error("unhandled telemetry field $field")
    }
}
