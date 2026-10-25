package com.androsync.remote

import org.junit.Assert.assertFalse
import org.junit.Test

class DistributionPlayTest {
    @Test
    fun `the play build is not full and never shows the all files row`() {
        assertFalse(Distribution.IS_FULL)
        assertFalse(Distribution.HAS_AGENT)
        assertFalse(Distribution.SHOWS_ALL_FILES_ROW)
    }
}
