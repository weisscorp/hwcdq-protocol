package cc.hwcdq.protocol

import java.util.UUID
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class ProfileContractTest {
    private val contract = ContractFiles.vector("profile.json")

    @Test
    fun `all shared HW178P profile membership vectors pass`() {
        assertEquals(contract.getString("profile_id"), Hw178pProfile.profileId)
        for (case in contract.getJSONArray("contains_cases").objects()) {
            val value = case.getJSONObject("value").vectorDouble()
            val action = {
                when (case.getString("quantity")) {
                    "voltage" -> Hw178pProfile.validateVoltage(value)
                    "current" -> Hw178pProfile.validateCurrent(value)
                    else -> error("unknown profile quantity")
                }
            }
            if (case.getBoolean("accepted")) {
                val accepted = action()
                assertEquals(
                    case.getJSONObject("value").getString("f32le_hex"),
                    accepted.littleEndianHex(),
                    case.getString("id"),
                )
            } else {
                val failure = assertFailsWith<HwcdqProtocolException>(case.getString("id"), action)
                assertEquals(case.getString("expected_code"), failure.conformanceCode.wireName, case.getString("id"))
            }
        }
    }

    @Test
    fun `all shared device maximum vectors narrow but never expand profile`() {
        for (case in contract.getJSONArray("effective_limits_cases").objects()) {
            val config = case.getJSONObject("config")
            val reported = ReportedDeviceLimits(
                maxVoltage = config.optionalFloat("max_voltage"),
                maxSingleModuleCurrent = config.optionalFloat("max_single_module_current"),
            )
            if (case.isNull("expected")) {
                val failure = assertFailsWith<HwcdqProtocolException>(case.getString("id")) {
                    Hw178pProfile.effectiveLimits(reported)
                }
                assertEquals(case.getString("expected_code"), failure.conformanceCode.wireName, case.getString("id"))
                continue
            }
            val actual = Hw178pProfile.effectiveLimits(reported)
            val expected = case.getJSONObject("expected")
            assertEquals(expected.getString("voltage_minimum").toFloat(), actual.voltage.minimum, case.getString("id"))
            assertEquals(expected.getString("voltage_maximum").toFloat(), actual.voltage.maximum, case.getString("id"))
            assertEquals(expected.getString("current_minimum").toFloat(), actual.current.minimum, case.getString("id"))
            assertEquals(expected.getString("current_maximum").toFloat(), actual.current.maximum, case.getString("id"))
            assertEquals(actual.voltage.maximum, Hw178pProfile.validateVoltage(actual.voltage.maximum.toDouble(), reported))
            assertEquals(actual.current.maximum, Hw178pProfile.validateCurrent(actual.current.maximum.toDouble(), reported))
            if (actual.voltage.maximum < Hw178pProfile.voltageRange.maximum) {
                assertEquals(
                    ConformanceCode.PROFILE_OUT_OF_RANGE,
                    assertFailsWith<HwcdqProtocolException> {
                        Hw178pProfile.validateVoltage(Hw178pProfile.voltageRange.maximum.toDouble(), reported)
                    }.conformanceCode,
                )
            }
            if (actual.current.maximum < Hw178pProfile.currentRange.maximum) {
                assertEquals(
                    ConformanceCode.PROFILE_OUT_OF_RANGE,
                    assertFailsWith<HwcdqProtocolException> {
                        Hw178pProfile.validateCurrent(Hw178pProfile.currentRange.maximum.toDouble(), reported)
                    }.conformanceCode,
                )
            }
        }
    }

    @Test
    fun `GATT UUID and write policy match shared contract without BLE APIs`() {
        val contract = ContractFiles.contract("gatt.json")
        val profile = HwcdqGattProfile.profile
        assertEquals(UUID.fromString(contract.getJSONObject("service").getString("uuid")), profile.serviceUuid)
        assertEquals(UUID.fromString(contract.getJSONObject("rx").getString("uuid")), profile.receiveUuid)
        assertEquals(UUID.fromString(contract.getJSONObject("tx").getString("uuid")), profile.transmitUuid)
        assertEquals(
            contract.getJSONObject("tx").getInt("live_maximum_write_without_response"),
            profile.observedMaximumWriteWithoutResponse,
        )
        assertEquals(GattWriteMode.WITHOUT_RESPONSE, profile.preferredWriteMode)
        assertEquals(GattWriteMode.WITH_RESPONSE, profile.fallbackWriteMode)
        assertEquals(profile, Hw178pProfile.gattProfile)
    }

    @Test
    fun `public numeric ranges reject invalid bounds`() {
        for ((minimum, maximum) in listOf(
            Float.NaN to 14.0f,
            0.0f to 14.0f,
            15.0f to 14.0f,
            1.0f to Float.POSITIVE_INFINITY,
        )) {
            val failure = assertFailsWith<HwcdqProtocolException> {
                NumericRange(minimum, maximum)
            }
            assertEquals(ConformanceCode.PROFILE_VALUE_INVALID, failure.conformanceCode)
        }
    }

    private fun org.json.JSONObject.optionalFloat(field: String): Float? =
        if (has(field)) getJSONObject(field).vectorDouble().toFloat() else null
}
