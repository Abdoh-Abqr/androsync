package com.androsync.remote.ui.setup

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SettingsPagesTest {
    @Test
    fun `each button opens the page named in the plan`() {
        assertEquals("android.settings.DEVICE_INFO_SETTINGS", SettingsPages.actionFor(StepAction.OPEN_ABOUT_PHONE))
        assertEquals("android.settings.APPLICATION_DEVELOPMENT_SETTINGS", SettingsPages.actionFor(StepAction.OPEN_DEVELOPER_OPTIONS))
        assertEquals(
            "android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION",
            SettingsPages.actionFor(StepAction.OPEN_ALL_FILES_ACCESS),
        )
    }

    @Test
    fun `a step without a button opens nothing`() {
        assertNull(SettingsPages.actionFor(StepAction.NONE))
    }

    @Test
    fun `only the developer options page asks Settings to highlight the USB debugging switch`() {
        assertEquals("enable_adb", SettingsPages.highlightKeyFor(StepAction.OPEN_DEVELOPER_OPTIONS))
        assertNull(SettingsPages.highlightKeyFor(StepAction.OPEN_ABOUT_PHONE))
        assertNull(SettingsPages.highlightKeyFor(StepAction.OPEN_ALL_FILES_ACCESS))
        assertEquals(":settings:fragment_args_key", SettingsPages.EXTRA_FRAGMENT_ARG_KEY)
    }
}
