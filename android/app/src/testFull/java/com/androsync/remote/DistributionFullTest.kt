package com.androsync.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DistributionFullTest {
    @Test
    fun `the full build is full, has the agent and shows the all files row`() {
        assertTrue(Distribution.IS_FULL)
        assertTrue(Distribution.HAS_AGENT)
        assertEquals(true, Distribution.SHOWS_ALL_FILES_ROW)
    }
}
