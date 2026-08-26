package cc.hwcdq.protocol

import java.nio.charset.StandardCharsets

/** A validated 32-character hexadecimal application credential digest. */
public class Credential private constructor(private val digest: String) {
    /** The exact 33-byte protocol representation: ASCII digest followed by NUL. */
    @JvmSynthetic
    internal fun toWireBytes(): ByteArray =
        digest.toByteArray(StandardCharsets.US_ASCII) + byteArrayOf(0)

    override fun equals(other: Any?): Boolean = other is Credential && digest == other.digest

    override fun hashCode(): Int = digest.hashCode()

    override fun toString(): String = "Credential(<redacted>)"

    public companion object {
        private const val APK_FALLBACK_DIGEST: String = "D41D8CD98F00B204E9800998ECF8427E"

        /** The APK's case-sensitive fallback credential. */
        public val apkFallback: Credential = Credential(APK_FALLBACK_DIGEST)

        /**
         * Validate and canonicalize an already-derived MD5 digest.
         *
         * This API intentionally accepts no plaintext and performs no hashing. Non-fallback
         * credentials are canonical lowercase; the APK fallback retains its exact uppercase form.
         */
        public fun fromDigest(digest: String): Credential {
            if (digest.toByteArray(StandardCharsets.UTF_8).size != 32) {
                throw HwcdqProtocolException(
                    ConformanceCode.CREDENTIAL_LENGTH,
                    "credential digest must contain exactly 32 characters",
                )
            }
            if (!digest.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' }) {
                throw HwcdqProtocolException(
                    ConformanceCode.CREDENTIAL_NON_HEX,
                    "credential digest must contain only ASCII hexadecimal characters",
                )
            }
            val canonical =
                if (digest.equals(APK_FALLBACK_DIGEST, ignoreCase = true)) {
                    APK_FALLBACK_DIGEST
                } else {
                    digest.lowercase()
                }
            return Credential(canonical)
        }
    }
}
