package cc.hwcdq.protocol

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Stateless named encoders and direction-neutral packet decoder. */
public object HwcdqCodec {
    public const val MINIMUM_FRAME_BYTES: Int = 3
    public const val MAXIMUM_FRAME_BYTES: Int = 256
    public const val MAXIMUM_PAYLOAD_BYTES: Int = 253
    public const val CONFIG_PAYLOAD_BYTES: Int = 103
    public const val TELEMETRY_PAYLOAD_BYTES: Int = 46

    public fun encodeGetFirmware(): ByteArray = encodeNamed(Opcode.GET_FIRMWARE)

    public fun encodeCheckCredential(credential: Credential): ByteArray =
        encodeNamed(Opcode.CHECK_CREDENTIAL, credential.toWireBytes())

    public fun encodeGetSerial(): ByteArray = encodeNamed(Opcode.GET_SERIAL)

    public fun encodeGetConfig(): ByteArray = encodeNamed(Opcode.GET_CONFIG)

    public fun encodeGetTelemetry(): ByteArray = encodeNamed(Opcode.GET_TELEMETRY)

    /** Validate only positive finite binary32 representation, not a charger profile. */
    public fun encodeSetVoltage(volts: Double): ByteArray =
        encodeNamed(Opcode.SET_VOLTAGE, encodePositiveFloat32(volts, "voltage"))

    /** Validate only positive finite binary32 representation, not a charger profile. */
    public fun encodeSetCurrent(amps: Double): ByteArray =
        encodeNamed(Opcode.SET_CURRENT, encodePositiveFloat32(amps, "current"))

    public fun encodeStart(): ByteArray =
        encodeNamed(Opcode.OUTPUT_CONTROL, encodeInt32Le(0))

    public fun encodeStop(): ByteArray =
        encodeNamed(Opcode.OUTPUT_CONTROL, encodeInt32Le(1))

    /** Sum opcode and payload bytes modulo 256. */
    public fun checksum(opcode: Int, payload: ByteArray = byteArrayOf()): Int {
        requireByte(opcode, "opcode")
        var sum = opcode
        for (byte in payload) {
            sum = (sum + byte.unsigned) and 0xFF
        }
        return sum
    }

    /** Check a supplied checksum byte without constructing or decoding a frame. */
    public fun verifyChecksum(opcode: Int, payload: ByteArray, suppliedChecksum: Int): Boolean {
        requireByte(suppliedChecksum, "checksum")
        return checksum(opcode, payload) == suppliedChecksum
    }

    /** Return false for any structurally invalid or checksum-invalid complete frame. */
    public fun verifyChecksum(frame: ByteArray): Boolean {
        if (frame.size < MINIMUM_FRAME_BYTES) {
            return false
        }
        val remainingLength = frame[0].unsigned
        if (remainingLength < 2 || frame.size != remainingLength + 1) {
            return false
        }
        val payload = frame.copyOfRange(2, frame.lastIndex)
        return checksum(frame[1].unsigned, payload) == frame.last().unsigned
    }

    /** Validate one complete frame and decode known payload layouts without assuming direction. */
    public fun decode(frame: ByteArray): DecodedPacket {
        if (frame.size < MINIMUM_FRAME_BYTES) {
            throw HwcdqProtocolException(
                ConformanceCode.PACKET_TRUNCATED,
                "a complete frame requires at least $MINIMUM_FRAME_BYTES bytes",
            )
        }
        val remainingLength = frame[0].unsigned
        if (remainingLength < 2) {
            throw HwcdqProtocolException(
                ConformanceCode.PACKET_LENGTH_MINIMUM,
                "remaining length must include opcode and checksum",
            )
        }
        val expectedSize = remainingLength + 1
        if (frame.size != expectedSize) {
            throw HwcdqProtocolException(
                ConformanceCode.PACKET_LENGTH_MISMATCH,
                "declared frame size $expectedSize does not match ${frame.size} bytes",
            )
        }
        val opcodeValue = frame[1].unsigned
        val payload = frame.copyOfRange(2, frame.lastIndex)
        val actualChecksum = frame.last().unsigned
        val expectedChecksum = checksum(opcodeValue, payload)
        if (actualChecksum != expectedChecksum) {
            throw HwcdqProtocolException(
                ConformanceCode.PACKET_CHECKSUM_MISMATCH,
                "checksum 0x${actualChecksum.hexByte()} does not match 0x${expectedChecksum.hexByte()}",
            )
        }
        return DecodedPacket(frame, opcodeValue, payload, decodeMeaning(opcodeValue, payload))
    }

    private fun encodeNamed(opcode: Opcode, payload: ByteArray = byteArrayOf()): ByteArray {
        if (payload.size > MAXIMUM_PAYLOAD_BYTES) {
            throw HwcdqProtocolException(
                ConformanceCode.PACKET_LENGTH_MISMATCH,
                "payload exceeds $MAXIMUM_PAYLOAD_BYTES bytes",
            )
        }
        val frame = ByteArray(payload.size + 3)
        frame[0] = (payload.size + 2).toByte()
        frame[1] = opcode.value.toByte()
        payload.copyInto(frame, destinationOffset = 2)
        frame[frame.lastIndex] = checksum(opcode.value, payload).toByte()
        return frame
    }

    private fun decodeMeaning(opcodeValue: Int, payload: ByteArray): PacketMeaning {
        val opcode = Opcode.fromValue(opcodeValue) ?: return UnknownPacket(opcodeValue, payload)
        if (payload.isEmpty() && opcode in EMPTY_REQUEST_OPCODES) {
            return PacketMeaning.EmptyRequest(opcode)
        }
        if (opcode in ACKNOWLEDGEMENT_OPCODES && payload.size == 1 && payload[0].unsigned <= 1) {
            return PacketMeaning.Acknowledgement(opcode, payload[0].unsigned == 1)
        }
        return when (opcode) {
            Opcode.GET_FIRMWARE -> PacketMeaning.FirmwareResponse(payload)
            Opcode.CHECK_CREDENTIAL -> decodeCredential(payload)
                ?: PacketMeaning.KnownOpcodeUnknownPayload(opcode, payload)
            Opcode.GET_SERIAL -> PacketMeaning.SerialResponse(payload)
            Opcode.GET_CONFIG -> if (payload.size == CONFIG_PAYLOAD_BYTES) {
                PacketMeaning.ConfigurationResponse(decodeConfiguration(payload))
            } else {
                PacketMeaning.KnownOpcodeUnknownPayload(opcode, payload)
            }
            Opcode.GET_TELEMETRY -> if (payload.size == TELEMETRY_PAYLOAD_BYTES) {
                PacketMeaning.TelemetryResponse(decodeTelemetry(payload))
            } else {
                PacketMeaning.KnownOpcodeUnknownPayload(opcode, payload)
            }
            Opcode.SET_VOLTAGE -> if (payload.size == Float.SIZE_BYTES) {
                PacketMeaning.VoltageSetpoint(payload.float32Le(0))
            } else {
                PacketMeaning.KnownOpcodeUnknownPayload(opcode, payload)
            }
            Opcode.SET_CURRENT -> if (payload.size == Float.SIZE_BYTES) {
                PacketMeaning.CurrentSetpoint(payload.float32Le(0))
            } else {
                PacketMeaning.KnownOpcodeUnknownPayload(opcode, payload)
            }
            Opcode.OUTPUT_CONTROL -> if (payload.size == Int.SIZE_BYTES) {
                PacketMeaning.OutputControl(payload.int32Le(0))
            } else {
                PacketMeaning.KnownOpcodeUnknownPayload(opcode, payload)
            }
        }
    }

    private fun decodeCredential(payload: ByteArray): PacketMeaning.CredentialRequest? {
        if (payload.lastOrNull() != 0.toByte()) {
            return null
        }
        val digest = payload.dropLast(1)
        val formatValid = digest.size == 32 && digest.all { byte ->
            val character = byte.toInt().toChar()
            character in '0'..'9' || character in 'a'..'f' || character in 'A'..'F'
        }
        return PacketMeaning.CredentialRequest(formatValid)
    }

    private fun decodeConfiguration(payload: ByteArray): Configuration = Configuration(
        targetVoltage = payload.float32Le(0),
        targetCurrent = payload.float32Le(4),
        offlineVoltage = payload.float32Le(8),
        offlineCurrent = payload.float32Le(12),
        powerOnOutput = payload[16].unsigned,
        voltageCalibration = payload.float32Le(17),
        voltageFeedbackCalibration = payload.float32Le(21),
        currentCalibration = payload.float32Le(25),
        currentFeedbackCalibration = payload.float32Le(29),
        maxVoltage = payload.float32Le(33),
        maxSingleModuleCurrent = payload.float32Le(37),
        autoStop = payload[41].unsigned,
        shutdownCurrent = payload.float32Le(42),
        rawU8At46 = payload[46].unsigned,
        temperatureProtection = payload[47].unsigned,
        rawU8At48 = payload[48].unsigned,
        protectionCutoffTemperature = payload[49].unsigned,
        fanBoostTemperature = payload[50].unsigned,
        fanMaxTemperature = payload[51].unsigned,
        rawAscii23 = payload.copyOfRange(52, 75),
        twoStageCharging = payload[75].unsigned,
        secondaryVoltage = payload.float32Le(76),
        secondaryCurrent = payload.float32Le(80),
        offlineControl = payload[84].unsigned,
        rawU8At85 = payload[85].unsigned,
        softStartCoefficient = payload[86].unsigned,
        powerLimit = payload.uint16Le(87),
        maxPower = payload.uint16Le(89),
        displayLanguageRaw = payload.copyOfRange(91, 99),
        rawU8At99 = payload[99].unsigned,
        rawU8At100 = payload[100].unsigned,
        rawU8At101 = payload[101].unsigned,
        rawU8At102 = payload[102].unsigned,
    )

    private fun decodeTelemetry(payload: ByteArray): Telemetry = Telemetry(
        inputVoltage = payload.float32Le(0),
        inputCurrent = payload.float32Le(4),
        inputFrequency = payload.float32Le(8),
        temperature1 = payload.float32Le(12),
        temperature2 = payload.float32Le(16),
        outputVoltage = payload.float32Le(20),
        outputCurrent = payload.float32Le(24),
        currentPoint = payload.float32Le(28),
        efficiency = payload.float32Le(32),
        currentOutput = payload[36].unsigned,
        accumulatedCapacityAh = payload.float32Le(37),
        accumulatedEnergyWh = payload.float32Le(41),
        moduleCount = payload[45].unsigned,
    )

    private fun encodePositiveFloat32(value: Double, field: String): ByteArray {
        if (!value.isFinite()) {
            throw HwcdqProtocolException(
                ConformanceCode.SCALAR_NON_FINITE,
                "$field must be finite",
            )
        }
        if (value <= 0.0) {
            throw HwcdqProtocolException(
                ConformanceCode.SCALAR_NON_POSITIVE,
                "$field must be positive",
            )
        }
        val converted = value.toFloat()
        if (!converted.isFinite() || converted <= 0.0f) {
            throw HwcdqProtocolException(
                ConformanceCode.SCALAR_NOT_FLOAT32,
                "$field is not representable as a positive IEEE-754 binary32 value",
            )
        }
        return ByteBuffer.allocate(Float.SIZE_BYTES)
            .order(ByteOrder.LITTLE_ENDIAN)
            .putFloat(converted)
            .array()
    }

    private fun encodeInt32Le(value: Int): ByteArray =
        ByteBuffer.allocate(Int.SIZE_BYTES)
            .order(ByteOrder.LITTLE_ENDIAN)
            .putInt(value)
            .array()

    private fun requireByte(value: Int, field: String) {
        if (value !in 0..0xFF) {
            throw HwcdqProtocolException(
                ConformanceCode.SCALAR_TYPE,
                "$field must be an unsigned byte",
            )
        }
    }

    private val ACKNOWLEDGEMENT_OPCODES: Set<Opcode> = setOf(
        Opcode.CHECK_CREDENTIAL,
        Opcode.SET_VOLTAGE,
        Opcode.SET_CURRENT,
        Opcode.OUTPUT_CONTROL,
    )

    private val EMPTY_REQUEST_OPCODES: Set<Opcode> = setOf(
        Opcode.GET_FIRMWARE,
        Opcode.GET_SERIAL,
        Opcode.GET_CONFIG,
        Opcode.GET_TELEMETRY,
    )
}

private val Byte.unsigned: Int
    get() = toInt() and 0xFF

private fun ByteArray.float32Le(offset: Int): Float =
    ByteBuffer.wrap(this).order(ByteOrder.LITTLE_ENDIAN).getFloat(offset)

private fun ByteArray.int32Le(offset: Int): Int =
    ByteBuffer.wrap(this).order(ByteOrder.LITTLE_ENDIAN).getInt(offset)

private fun ByteArray.uint16Le(offset: Int): Int =
    ByteBuffer.wrap(this).order(ByteOrder.LITTLE_ENDIAN).getShort(offset).toInt() and 0xFFFF

private fun Int.hexByte(): String = toString(16).padStart(2, '0')
