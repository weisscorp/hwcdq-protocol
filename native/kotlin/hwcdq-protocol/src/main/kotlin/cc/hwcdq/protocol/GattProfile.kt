package cc.hwcdq.protocol

import java.util.UUID

public enum class GattWriteMode {
    WITHOUT_RESPONSE,
    WITH_RESPONSE,
}

/** UUID and write-policy constants only; this library performs no BLE operations. */
public data class GattProfile(
    public val serviceUuid: UUID,
    public val receiveUuid: UUID,
    public val transmitUuid: UUID,
    public val preferredWriteMode: GattWriteMode,
    public val fallbackWriteMode: GattWriteMode,
    public val observedMaximumWriteWithoutResponse: Int,
)

public object HwcdqGattProfile {
    public val profile: GattProfile = GattProfile(
        serviceUuid = UUID.fromString("0000ffe1-0000-1000-8000-00805f9b34fb"),
        receiveUuid = UUID.fromString("0000ffe2-0000-1000-8000-00805f9b34fb"),
        transmitUuid = UUID.fromString("0000ffe3-0000-1000-8000-00805f9b34fb"),
        preferredWriteMode = GattWriteMode.WITHOUT_RESPONSE,
        fallbackWriteMode = GattWriteMode.WITH_RESPONSE,
        observedMaximumWriteWithoutResponse = 253,
    )
}
