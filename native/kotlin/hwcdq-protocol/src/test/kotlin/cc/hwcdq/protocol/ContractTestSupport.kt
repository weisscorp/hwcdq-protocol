package cc.hwcdq.protocol

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.Path
import org.json.JSONArray
import org.json.JSONObject

internal object ContractFiles {
    private val repositoryRoot: Path = Path.of(System.getProperty("user.dir"))
        .toAbsolutePath()
        .normalize()
        .resolve("../../..")
        .normalize()

    val vectorsDirectory: Path = repositoryRoot.resolve("contract/v1/vectors")

    fun vector(name: String): JSONObject = json(vectorsDirectory.resolve(name))

    fun contract(name: String): JSONObject = json(repositoryRoot.resolve("contract/v1/$name"))

    private fun json(path: Path): JSONObject {
        check(Files.isRegularFile(path)) { "shared contract file is missing: $path" }
        return JSONObject(Files.readString(path))
    }
}

internal fun JSONObject.isRequiredByKotlin(): Boolean {
    if (!optBoolean("shared_native_requirement", true)) {
        return false
    }
    val implementations = optJSONArray("implementations") ?: return true
    return implementations.stringValues().any { it == "kotlin" }
}

internal fun JSONArray.objects(): List<JSONObject> =
    (0 until length()).map(::getJSONObject)

internal fun JSONArray.stringValues(): List<String> =
    (0 until length()).map(::getString)

internal fun String.hexBytes(): ByteArray {
    require(length % 2 == 0) { "hex input must contain whole bytes" }
    return ByteArray(length / 2) { index ->
        substring(index * 2, index * 2 + 2).toInt(16).toByte()
    }
}

internal fun ByteArray.lowerHex(): String = joinToString(separator = "") {
    (it.toInt() and 0xFF).toString(16).padStart(2, '0')
}

internal fun Float.littleEndianHex(): String =
    ByteBuffer.allocate(Float.SIZE_BYTES)
        .order(ByteOrder.LITTLE_ENDIAN)
        .putFloat(this)
        .array()
        .lowerHex()

internal fun JSONObject.vectorDouble(): Double = when (optString("kind", "decimal")) {
    "decimal" -> getString("decimal").toDouble()
    "nan" -> Double.NaN
    "positive_infinity" -> Double.POSITIVE_INFINITY
    "negative_infinity" -> Double.NEGATIVE_INFINITY
    else -> error("unsupported numeric vector kind: ${getString("kind")}")
}
