package com.androsync.remote.ui.setup

import com.androsync.remote.data.api.DeviceStateDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SetupChecklistTest {

    private fun dev(state: String) = DeviceStateDto(serial = "RZ123", state = state, transport = "usb")

    private fun steps(
        devOptions: Boolean = true,
        adb: Boolean = true,
        readiness: DeviceReadiness = DeviceReadiness.READY,
        isFull: Boolean = false,
        allFiles: Boolean = false,
    ) = computeChecklist(devOptions, adb, readiness, isFull, allFiles)

    private fun byId(list: List<ChecklistStep>, id: StepId) = list.first { it.id == id }

    // --- readiness from the ping ------------------------------------------------------

    @Test
    fun `readiness is unknown when the laptop cannot be reached`() {
        assertEquals(DeviceReadiness.UNKNOWN, deviceReadiness(false, listOf(dev("device"))))
    }

    @Test
    fun `a ready phone wins over a second phone that is not`() {
        assertEquals(DeviceReadiness.READY, deviceReadiness(true, listOf(dev("unauthorized"), dev("device"))))
    }

    @Test
    fun `unauthorized offline and none are told apart`() {
        assertEquals(DeviceReadiness.UNAUTHORIZED, deviceReadiness(true, listOf(dev("unauthorized"))))
        assertEquals(DeviceReadiness.OFFLINE, deviceReadiness(true, listOf(dev("offline"))))
        assertEquals(DeviceReadiness.NO_DEVICE, deviceReadiness(true, emptyList()))
    }

    // --- the steps ------------------------------------------------------------------------

    @Test
    fun `everything done gives ticked steps, a keep-it-on tip and nothing left to do`() {
        val list = steps()
        assertEquals(
            listOf(StepId.DEVELOPER_OPTIONS, StepId.USB_DEBUGGING, StepId.COMPUTER_ALLOWED, StepId.STAYS_ON),
            list.map { it.id },
        )
        assertEquals(StepStatus.DONE, byId(list, StepId.DEVELOPER_OPTIONS).status)
        assertEquals(StepStatus.DONE, byId(list, StepId.USB_DEBUGGING).status)
        assertEquals(StepStatus.DONE, byId(list, StepId.COMPUTER_ALLOWED).status)
        assertEquals(StepStatus.INFO, byId(list, StepId.STAYS_ON).status)
        assertTrue(isFullySetUp(list))
        assertNull(firstOpenStep(list))
    }

    @Test
    fun `developer options off sends the user to About phone and holds back the debugging step`() {
        val list = steps(devOptions = false, adb = false, readiness = DeviceReadiness.UNKNOWN)
        val dev = byId(list, StepId.DEVELOPER_OPTIONS)
        assertEquals(StepStatus.TODO, dev.status)
        assertEquals(StepAction.OPEN_ABOUT_PHONE, dev.action)
        assertTrue(dev.detail.contains("Build number"))
        val adb = byId(list, StepId.USB_DEBUGGING)
        assertEquals(StepStatus.WAITING, adb.status)
        assertEquals(StepAction.NONE, adb.action)
        assertEquals(StepId.DEVELOPER_OPTIONS, firstOpenStep(list)!!.id)
        assertFalse(isFullySetUp(list))
    }

    @Test
    fun `debugging off with developer options on opens the developer options page`() {
        val list = steps(adb = false, readiness = DeviceReadiness.UNKNOWN)
        val adb = byId(list, StepId.USB_DEBUGGING)
        assertEquals(StepStatus.TODO, adb.status)
        assertEquals(StepAction.OPEN_DEVELOPER_OPTIONS, adb.action)
        assertEquals("Open Developer options", adb.actionLabel)
        assertEquals(StepId.USB_DEBUGGING, firstOpenStep(list)!!.id)
        assertTrue(list.none { it.id == StepId.STAYS_ON }) // the tip only shows once debugging is on
    }

    @Test
    fun `an unauthorized phone tells the user to tap Allow and offers no button`() {
        val step = byId(steps(readiness = DeviceReadiness.UNAUTHORIZED), StepId.COMPUTER_ALLOWED)
        assertEquals(StepStatus.TODO, step.status)
        assertTrue(step.detail.contains("Always allow from this computer"))
        assertEquals(StepAction.NONE, step.action)
    }

    @Test
    fun `an offline phone says unplug and replug, no phone says plug it in, unknown waits`() {
        assertTrue(byId(steps(readiness = DeviceReadiness.OFFLINE), StepId.COMPUTER_ALLOWED).detail.contains("Unplug"))
        assertTrue(byId(steps(readiness = DeviceReadiness.NO_DEVICE), StepId.COMPUTER_ALLOWED).title.contains("Plug"))
        assertEquals(StepStatus.WAITING, byId(steps(readiness = DeviceReadiness.UNKNOWN), StepId.COMPUTER_ALLOWED).status)
    }

    @Test
    fun `the all files step exists only in the full build`() {
        assertTrue(steps(isFull = false).none { it.id == StepId.ALL_FILES })
        val missing = byId(steps(isFull = true, allFiles = false), StepId.ALL_FILES)
        assertEquals(StepStatus.TODO, missing.status)
        assertEquals(StepAction.OPEN_ALL_FILES_ACCESS, missing.action)
        assertEquals(StepStatus.DONE, byId(steps(isFull = true, allFiles = true), StepId.ALL_FILES).status)
    }

    @Test
    fun `the first open step is the first todo, else the first waiting one`() {
        val list = steps(readiness = DeviceReadiness.UNAUTHORIZED)
        assertEquals(StepId.COMPUTER_ALLOWED, firstOpenStep(list)!!.id)
        val waiting = steps(readiness = DeviceReadiness.UNKNOWN)
        assertEquals(StepId.COMPUTER_ALLOWED, firstOpenStep(waiting)!!.id)
        assertFalse(isFullySetUp(waiting))
    }
}
