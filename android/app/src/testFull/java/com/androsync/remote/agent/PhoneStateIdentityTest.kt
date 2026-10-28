package com.androsync.remote.agent

import org.junit.Assert.assertEquals
import org.junit.Test

class PhoneStateIdentityTest {
    @Test
    fun `identity is url encoded and limited to 40 characters`() {
        assertEquals("&name=S24+FE&model=SM-S721B", PhoneState.identity("S24 FE", "SM-S721B"))
        assertEquals("&name=" + "a".repeat(40), PhoneState.identity("a".repeat(60), null))
    }

    @Test
    fun `blank parts are left out`() {
        assertEquals("", PhoneState.identity(" ", null))
        assertEquals("&model=Tab", PhoneState.identity(null, "Tab"))
    }
}
