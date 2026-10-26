package com.androsync.remote.ui.common

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ShareAppTest {
    @Test
    fun `share text names the project, links the repository and carries nothing personal`() {
        val text = shareText()
        assertTrue(text.startsWith("AndroSync keeps folders identical"))
        assertTrue(text.endsWith(ProjectLinks.REPO))
        assertFalse(text.contains("token", ignoreCase = true))
    }
}
