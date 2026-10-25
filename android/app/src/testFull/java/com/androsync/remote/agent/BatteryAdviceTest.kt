package com.androsync.remote.agent

import org.junit.Assert.assertTrue
import org.junit.Test

class BatteryAdviceTest {
    @Test
    fun `each big maker gets its own steps, matched without regard to case`() {
        assertTrue(vendorBatteryAdvice("samsung").contains("Never sleeping apps"))
        assertTrue(vendorBatteryAdvice("Samsung").contains("Never sleeping apps"))
        assertTrue(vendorBatteryAdvice("realme").contains("Auto-launch"))
        assertTrue(vendorBatteryAdvice("OPPO").contains("Allow background activity"))
        assertTrue(vendorBatteryAdvice("Xiaomi").contains("Autostart"))
    }

    @Test
    fun `an unknown maker gets general advice`() {
        assertTrue(vendorBatteryAdvice("Fairphone").contains("battery settings"))
    }
}
