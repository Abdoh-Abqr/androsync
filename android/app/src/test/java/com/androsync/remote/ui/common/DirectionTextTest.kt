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

    @Test
    fun `names replace laptop and phone, and any phone is the general text`() {
        assertEquals("Ubuntu ↔ S24 FE", directionText("two-way", "Ubuntu", "S24 FE"))
        assertEquals("Ubuntu → S24 FE", directionText("laptop-to-phone", "Ubuntu", "S24 FE"))
        assertEquals("Ubuntu ← S24 FE", directionText("phone-to-laptop", "Ubuntu", "S24 FE"))
        assertEquals("Ubuntu ↔ any phone", directionText("two-way", "Ubuntu", null))
    }

    @Test
    fun `an older laptop without a name reads Laptop`() {
        assertEquals("Laptop ↔ any phone", directionText("two-way", null, null))
        assertEquals("Laptop ↔ any phone", directionText("two-way", " ", ""))
        assertEquals("odd", directionText("odd", "Ubuntu", "S24 FE"))
    }
}
