package cc.hwcdq.protocol

/** Stable language-neutral error identifiers from contract/v1/wire.json. */
public enum class ConformanceCode(public val wireName: String) {
    CREDENTIAL_LENGTH("credential.length"),
    CREDENTIAL_NON_HEX("credential.non_hex"),
    CREDENTIAL_TYPE("credential.type"),
    PACKET_CHECKSUM_MISMATCH("packet.checksum.mismatch"),
    PACKET_LENGTH_MINIMUM("packet.length.minimum"),
    PACKET_LENGTH_MISMATCH("packet.length.mismatch"),
    PACKET_TRUNCATED("packet.truncated"),
    PROFILE_DEVICE_LIMITS_INVALID("profile.device_limits.invalid"),
    PROFILE_OUT_OF_RANGE("profile.out_of_range"),
    PROFILE_VALUE_INVALID("profile.value.invalid"),
    SCALAR_NON_FINITE("scalar.non_finite"),
    SCALAR_NON_POSITIVE("scalar.non_positive"),
    SCALAR_NOT_FLOAT32("scalar.not_float32"),
    SCALAR_TYPE("scalar.type"),
    STREAM_FRAME_INVALID("stream.frame.invalid"),
    STREAM_LENGTH_INVALID("stream.length.invalid"),
}

/** The single public failure type for codec, framing, credential, and profile validation. */
public class HwcdqProtocolException(
    public val conformanceCode: ConformanceCode,
    message: String,
    cause: Throwable? = null,
) : IllegalArgumentException("${conformanceCode.wireName}: $message", cause)
