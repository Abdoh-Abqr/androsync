package com.androsync.remote.ui.pairs

import com.androsync.remote.data.api.DeviceStateDto
import com.androsync.remote.data.api.PairDto
import org.junit.Assert.assertEquals
import org.junit.Test

class PhoneChoiceTest {
    private fun dev(serial: String, state: String = "device", id: String? = null, name: String? = null) =
        DeviceStateDto(serial = serial, state = state, id = id, name = name)

    private fun pair(name: String, device: String = "any") =
        PairDto(name = name, local = "/x", remote = "/sdcard/x", direction = "two-way", conflict = "newest-wins", device = device)

    @Test
    fun `ready phones are offered, by name when they have one, by id otherwise`() {
        val devices = listOf(dev("USB1", id = "HW_A", name = "S24 FE"), dev("USB2", id = "HW_B"), dev("USB3", state = "offline", id = "HW_C"))
        assertEquals(listOf(PhoneChoice("HW_A", "S24 FE"), PhoneChoice("HW_B", "HW_B")), phoneChoices(devices))
    }

    @Test
    fun `the same phone over USB and Wi-Fi is offered once`() {
        val devices = listOf(dev("USB1", id = "HW_A"), dev("100.64.0.9:5555", id = "HW_A"))
        assertEquals(1, phoneChoices(devices).size)
    }

    @Test
    fun `a phone a pair is already bound to stays in the list even when it is not attached`() {
        val choices = phoneChoices(listOf(dev("USB1", id = "HW_A")), alsoKeep = listOf("HW_GONE", "any", "HW_A"))
        assertEquals(listOf(PhoneChoice("HW_A", "HW_A"), PhoneChoice("HW_GONE", "HW_GONE (not connected)")), choices)
    }

    @Test
    fun `the label of a bound pair uses the phone's name and is null for any`() {
        val devices = listOf(dev("USB1", id = "HW_A", name = "S24 FE"))
        assertEquals("S24 FE", phoneLabel("HW_A", devices))
        assertEquals("HW_Z", phoneLabel("HW_Z", devices))
        assertEquals(null, phoneLabel("any", devices))
    }

    @Test
    fun `the filter shows that phone's pairs and the any pairs`() {
        val pairs = listOf(pair("a", "HW_A"), pair("b", "HW_B"), pair("c"))
        assertEquals(listOf("a", "c"), filterPairsForPhone(pairs, "HW_A").map { it.name })
        assertEquals(listOf("a", "b", "c"), filterPairsForPhone(pairs, null).map { it.name })
    }
}
