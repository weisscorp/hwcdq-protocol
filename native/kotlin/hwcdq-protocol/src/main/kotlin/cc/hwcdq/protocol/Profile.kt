package cc.hwcdq.protocol

import kotlin.math.min

/**
 * Positive, finite, ordered binary32 bounds.
 *
 * The Float type boundary turns wider numeric overflow/underflow into infinity/zero, which the
 * constructor rejects.
 */
public data class NumericRange(public val minimum: Float, public val maximum: Float) {
    init {
        if (!minimum.isFinite() || !maximum.isFinite() || minimum <= 0.0f || maximum < minimum) {
            throw HwcdqProtocolException(
                ConformanceCode.PROFILE_VALUE_INVALID,
                "numeric range must have positive finite ordered bounds",
            )
        }
    }

    public operator fun contains(value: Float): Boolean = value >= minimum && value <= maximum
}

public data class EffectiveLimits(
    public val voltage: NumericRange,
    public val current: NumericRange,
)

/** Device-configured maximum fields; null represents unavailable configuration. */
public data class ReportedDeviceLimits(
    public val maxVoltage: Float?,
    public val maxSingleModuleCurrent: Float?,
)

public interface ChargerProfile {
    public val profileId: String
    public val model: String
    public val gattProfile: GattProfile
    public val voltageRange: NumericRange
    public val currentRange: NumericRange

    public fun effectiveLimits(reported: ReportedDeviceLimits): EffectiveLimits

    public fun effectiveLimits(configuration: Configuration): EffectiveLimits = effectiveLimits(
        ReportedDeviceLimits(configuration.maxVoltage, configuration.maxSingleModuleCurrent),
    )

    public fun validateVoltage(value: Double): Float

    public fun validateCurrent(value: Double): Float

    public fun validateVoltage(value: Double, reported: ReportedDeviceLimits): Float

    public fun validateCurrent(value: Double, reported: ReportedDeviceLimits): Float

    public fun validateVoltage(value: Double, configuration: Configuration): Float = validateVoltage(
        value,
        ReportedDeviceLimits(configuration.maxVoltage, configuration.maxSingleModuleCurrent),
    )

    public fun validateCurrent(value: Double, configuration: Configuration): Float = validateCurrent(
        value,
        ReportedDeviceLimits(configuration.maxVoltage, configuration.maxSingleModuleCurrent),
    )
}

/** Model-specific safety envelope. Device-reported maxima may narrow, never expand, it. */
public object Hw178pProfile : ChargerProfile {
    override val profileId: String = "pidzoom-hw178p"
    override val model: String = "HW178P"
    override val gattProfile: GattProfile = HwcdqGattProfile.profile
    override val voltageRange: NumericRange = NumericRange(50.0f, 178.0f)
    override val currentRange: NumericRange = NumericRange(0.01f, 14.0f)

    override fun effectiveLimits(reported: ReportedDeviceLimits): EffectiveLimits {
        val deviceVoltage = validDeviceMaximum(
            reported.maxVoltage,
            voltageRange.minimum,
            "max_voltage",
        )
        val deviceCurrent = validDeviceMaximum(
            reported.maxSingleModuleCurrent,
            currentRange.minimum,
            "max_single_module_current",
        )
        return EffectiveLimits(
            voltage = NumericRange(voltageRange.minimum, min(deviceVoltage, voltageRange.maximum)),
            current = NumericRange(currentRange.minimum, min(deviceCurrent, currentRange.maximum)),
        )
    }

    override fun validateVoltage(value: Double): Float {
        val converted = validProfileValue(value, "voltage")
        if (converted !in voltageRange) {
            throw HwcdqProtocolException(
                ConformanceCode.PROFILE_OUT_OF_RANGE,
                "voltage $value V is outside the HW178P profile range",
            )
        }
        return converted
    }

    override fun validateCurrent(value: Double): Float {
        val converted = validProfileValue(value, "current")
        if (converted !in currentRange) {
            throw HwcdqProtocolException(
                ConformanceCode.PROFILE_OUT_OF_RANGE,
                "current $value A is outside the HW178P profile range",
            )
        }
        return converted
    }

    override fun validateVoltage(value: Double, reported: ReportedDeviceLimits): Float {
        val converted = validProfileValue(value, "voltage")
        if (converted !in effectiveLimits(reported).voltage) {
            throw HwcdqProtocolException(
                ConformanceCode.PROFILE_OUT_OF_RANGE,
                "voltage $value V is outside the effective HW178P range",
            )
        }
        return converted
    }

    override fun validateCurrent(value: Double, reported: ReportedDeviceLimits): Float {
        val converted = validProfileValue(value, "current")
        if (converted !in effectiveLimits(reported).current) {
            throw HwcdqProtocolException(
                ConformanceCode.PROFILE_OUT_OF_RANGE,
                "current $value A is outside the effective HW178P range",
            )
        }
        return converted
    }

    private fun validDeviceMaximum(value: Float?, minimum: Float, field: String): Float {
        if (value == null || !value.isFinite() || value < minimum) {
            throw HwcdqProtocolException(
                ConformanceCode.PROFILE_DEVICE_LIMITS_INVALID,
                "$field does not define a usable HW178P range",
            )
        }
        return value
    }

    private fun validProfileValue(value: Double, field: String): Float {
        val converted = value.toFloat()
        if (!value.isFinite() || value <= 0.0 || !converted.isFinite() || converted <= 0.0f) {
            throw HwcdqProtocolException(
                ConformanceCode.PROFILE_VALUE_INVALID,
                "$field must be a positive finite IEEE-754 binary32 value",
            )
        }
        return converted
    }
}
