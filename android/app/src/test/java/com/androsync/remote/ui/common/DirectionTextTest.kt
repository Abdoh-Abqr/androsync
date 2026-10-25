package com.androsync.remote.ui.common

import org.junit.Assert.assertEquals
import org.junit.Test

class DirectionTextTest {

    @Test
    fun `laptop-to-phone renders a right arrow`() {
        assertEquals("laptop → phone", directionArrowText("laptop-to-phone"))
    }

    @Test
    fun `phone-to-laptop renders a left arrow, phone still on the right`() {
        assertEquals("laptop ← phone", directionArrowText("phone-to-laptop"))
    }

    @Test
    fun `two-way renders a double arrow`() {
        assertEquals("laptop ↔ phone", directionArrowText("two-way"))
    }

    @Test
    fun `unknown direction falls back to the raw string`() {
        assertEquals("sideways", directionArrowText("sideways"))
    }
}
