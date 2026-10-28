package com.androsync.remote.ui.pairs

import com.androsync.remote.data.api.AgentInfoDto
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
    fun `phone apps are offered for agent pairs and adb phones for adb pairs`() {
        val devices = listOf(dev("USB1", id = "HW_A", name = "S24 FE"))
        val agents = listOf(
            AgentInfoDto(id = "t1", device = "agent-t1", name = "Tab S9", connected = true),
            AgentInfoDto(id = "r1", device = "agent-r1", model = "RMX3", connected = true),
            AgentInfoDto(id = "old", device = "agent-old", name = "Old", connected = false),
        )
        assertEquals(
            listOf(PhoneChoice("agent-t1", "Tab S9"), PhoneChoice("agent-r1", "RMX3")),
            phoneChoices(devices, agents = agents, transport = "agent"),
        )
        assertEquals(listOf(PhoneChoice("HW_A", "S24 FE")), phoneChoices(devices, agents = agents, transport = "adb"))
        assertEquals(3, phoneChoices(devices, agents = agents).size)
    }

    @Test
    fun `a bound phone app that is offline keeps its name`() {
        val agents = listOf(AgentInfoDto(id = "old", device = "agent-old", name = "Old", connected = false))
        assertEquals(
            listOf(PhoneChoice("agent-old", "Old")),
            phoneChoices(emptyList(), alsoKeep = listOf("agent-old"), agents = agents, transport = "agent"),
        )
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
