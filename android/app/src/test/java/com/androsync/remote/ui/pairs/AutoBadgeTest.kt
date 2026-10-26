package com.androsync.remote.ui.pairs

import com.androsync.remote.data.api.AutoStateDto
import com.androsync.remote.data.api.PairDto
import org.junit.Assert.assertEquals
import org.junit.Test

class AutoBadgeTest {
    private fun pair(auto: Boolean, state: AutoStateDto? = null) =
        PairDto(name = "a", local = "/x", remote = "/sdcard/a", direction = "two-way", conflict = "newest-wins", auto = auto, autoState = state)

    @Test
    fun `no badge when automatic sync is off`() = assertEquals("", autoBadge(pair(false)))

    @Test
    fun `a badge when it is on, with the reason when it is paused`() {
        assertEquals("  ·  auto", autoBadge(pair(true, AutoStateDto("watching"))))
        assertEquals("  ·  auto paused: needs your OK", autoBadge(pair(true, AutoStateDto("paused", "needs your OK"))))
        assertEquals("  ·  auto", autoBadge(pair(true)))
    }
}
