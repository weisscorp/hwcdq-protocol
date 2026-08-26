package cc.hwcdq.protocol

/** Executable, named protocol opcodes. Unknown values remain numeric after decoding. */
public enum class Opcode(public val value: Int) {
    GET_FIRMWARE(0x01),
    CHECK_CREDENTIAL(0x02),
    GET_SERIAL(0x04),
    GET_CONFIG(0x05),
    GET_TELEMETRY(0x06),
    SET_VOLTAGE(0x07),
    SET_CURRENT(0x08),
    OUTPUT_CONTROL(0x0C),
    ;

    public companion object {
        public fun fromValue(value: Int): Opcode? = entries.firstOrNull { it.value == value }
    }
}

/** Fully typed 103-byte configuration payload. */
public class Configuration internal constructor(
    public val targetVoltage: Float,
    public val targetCurrent: Float,
    public val offlineVoltage: Float,
    public val offlineCurrent: Float,
    public val powerOnOutput: Int,
    public val voltageCalibration: Float,
    public val voltageFeedbackCalibration: Float,
    public val currentCalibration: Float,
    public val currentFeedbackCalibration: Float,
    public val maxVoltage: Float,
    public val maxSingleModuleCurrent: Float,
    public val autoStop: Int,
    public val shutdownCurrent: Float,
    public val rawU8At46: Int,
    public val temperatureProtection: Int,
    public val rawU8At48: Int,
    public val protectionCutoffTemperature: Int,
    public val fanBoostTemperature: Int,
    public val fanMaxTemperature: Int,
    rawAscii23: ByteArray,
    public val twoStageCharging: Int,
    public val secondaryVoltage: Float,
    public val secondaryCurrent: Float,
    public val offlineControl: Int,
    public val rawU8At85: Int,
    public val softStartCoefficient: Int,
    public val powerLimit: Int,
    public val maxPower: Int,
    displayLanguageRaw: ByteArray,
    public val rawU8At99: Int,
    public val rawU8At100: Int,
    public val rawU8At101: Int,
    public val rawU8At102: Int,
) {
    private val rawAscii23Value: ByteArray = rawAscii23.copyOf()
    private val displayLanguageRawValue: ByteArray = displayLanguageRaw.copyOf()

    public val rawAscii23: ByteArray
        get() = rawAscii23Value.copyOf()

    public val displayLanguageRaw: ByteArray
        get() = displayLanguageRawValue.copyOf()
}

/** Fully typed 46-byte telemetry payload plus deterministic derived values. */
public data class Telemetry(
    public val inputVoltage: Float,
    public val inputCurrent: Float,
    public val inputFrequency: Float,
    public val temperature1: Float,
    public val temperature2: Float,
    public val outputVoltage: Float,
    public val outputCurrent: Float,
    public val currentPoint: Float,
    public val efficiency: Float,
    public val currentOutput: Int,
    public val accumulatedCapacityAh: Float,
    public val accumulatedEnergyWh: Float,
    public val moduleCount: Int,
) {
    public val outputEnabled: Boolean?
        get() = when (currentOutput) {
            0 -> true
            1 -> false
            else -> null
        }

    public val inputPowerW: Double
        get() = inputVoltage.toDouble() * inputCurrent.toDouble()

    public val outputPowerW: Double
        get() = outputVoltage.toDouble() * outputCurrent.toDouble()
}

/** Semantic interpretation of a checksum- and length-valid frame. */
public sealed interface PacketMeaning {
    public val command: String

    public data class EmptyRequest(public val request: Opcode) : PacketMeaning {
        override val command: String = when (request) {
            Opcode.GET_FIRMWARE -> "get_firmware"
            Opcode.CHECK_CREDENTIAL -> "check_password"
            Opcode.GET_SERIAL -> "get_serial"
            Opcode.GET_CONFIG -> "get_config"
            Opcode.GET_TELEMETRY -> "get_telemetry"
            Opcode.SET_VOLTAGE -> "set_voltage"
            Opcode.SET_CURRENT -> "set_current"
            Opcode.OUTPUT_CONTROL -> "output_control"
        }
    }

    public class FirmwareResponse(bytes: ByteArray) : PacketMeaning {
        private val value: ByteArray = bytes.copyOf()
        public val bytes: ByteArray
            get() = value.copyOf()
        override val command: String = "firmware_response"
    }

    public class SerialResponse(bytes: ByteArray) : PacketMeaning {
        private val value: ByteArray = bytes.copyOf()
        public val bytes: ByteArray
            get() = value.copyOf()
        override val command: String = "serial_response"
    }

    public data class CredentialRequest(public val credentialFormatValid: Boolean) : PacketMeaning {
        override val command: String = "check_password"
    }

    public data class Acknowledgement(
        public val operation: Opcode,
        public val acknowledged: Boolean,
    ) : PacketMeaning {
        override val command: String = when (operation) {
            Opcode.CHECK_CREDENTIAL -> "check_password_ack"
            Opcode.SET_VOLTAGE -> "set_voltage"
            Opcode.SET_CURRENT -> "set_current"
            Opcode.OUTPUT_CONTROL -> "output_control"
            else -> "${operation.name.lowercase()}_ack"
        }
    }

    public data class ConfigurationResponse(public val config: Configuration) : PacketMeaning {
        override val command: String = "config_response"
    }

    public data class TelemetryResponse(public val telemetry: Telemetry) : PacketMeaning {
        override val command: String = "telemetry_response"
    }

    public data class VoltageSetpoint(public val volts: Float) : PacketMeaning {
        override val command: String = "set_voltage"
    }

    public data class CurrentSetpoint(public val amps: Float) : PacketMeaning {
        override val command: String = "set_current"
    }

    public data class OutputControl(public val state: Int) : PacketMeaning {
        public val stateValid: Boolean = state == 0 || state == 1
        public val enabled: Boolean? = when (state) {
            0 -> true
            1 -> false
            else -> null
        }
        override val command: String = "output_control"
    }

    public class KnownOpcodeUnknownPayload(
        public val knownOpcode: Opcode,
        payload: ByteArray,
    ) : PacketMeaning {
        private val value: ByteArray = payload.copyOf()
        public val payload: ByteArray
            get() = value.copyOf()
        override val command: String = when (knownOpcode) {
            Opcode.CHECK_CREDENTIAL -> "check_password_unknown_payload"
            Opcode.GET_CONFIG -> "config_response"
            Opcode.GET_TELEMETRY -> "telemetry_opcode_unknown_payload"
            Opcode.SET_VOLTAGE -> "set_voltage"
            Opcode.SET_CURRENT -> "set_current"
            Opcode.OUTPUT_CONTROL -> "output_control"
            else -> "${knownOpcode.name.lowercase()}_unknown_payload"
        }
    }
}

/** Lossless representation for an unknown opcode; deliberately has no encode operation. */
public class UnknownPacket(public val opcode: Int, payload: ByteArray) : PacketMeaning {
    private val value: ByteArray = payload.copyOf()
    public val payload: ByteArray
        get() = value.copyOf()
    override val command: String = "unknown"
}

/** A validated frame and its direction-neutral semantic interpretation. */
public class DecodedPacket internal constructor(
    rawFrame: ByteArray,
    public val opcode: Int,
    payload: ByteArray,
    public val meaning: PacketMeaning,
) {
    private val rawFrameValue: ByteArray = rawFrame.copyOf()
    private val payloadValue: ByteArray = payload.copyOf()

    public val rawFrame: ByteArray
        get() = rawFrameValue.copyOf()

    public val declaredRemainingLength: Int = rawFrameValue[0].toInt() and 0xFF

    public val payload: ByteArray
        get() = payloadValue.copyOf()

    public val checksum: Int = rawFrameValue.last().toInt() and 0xFF

    public val command: String
        get() = meaning.command
}
