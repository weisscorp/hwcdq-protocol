package cc.hwcdq.protocol

import java.io.DataInputStream
import kotlin.test.Test
import kotlin.test.assertEquals

class JvmCompatibilityTest {
    @Test
    fun `runtime classes target Java 17 bytecode`() {
        val resource = "/cc/hwcdq/protocol/HwcdqCodec.class"
        val stream = checkNotNull(HwcdqCodec::class.java.getResourceAsStream(resource))
        DataInputStream(stream).use { input ->
            assertEquals(0xCAFEBABE.toInt(), input.readInt(), "class magic")
            input.readUnsignedShort() // minor version
            assertEquals(61, input.readUnsignedShort(), "class major version")
        }
    }
}
