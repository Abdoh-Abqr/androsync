package com.androsync.remote.ui.common

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ActionLineTest {

    @Test
    fun `a delete line is split into the description and the two full paths`() {
        val line = "x  phone  notes/a.md   deleted on laptop, so removed from the phone" +
            "   (laptop: /home/me/vault/notes/a.md  ·  phone: /sdcard/Obsidian/Notes/notes/a.md)"
        val parsed = parseActionLine(line)
        assertEquals("x  phone  notes/a.md   deleted on laptop, so removed from the phone", parsed.head)
        assertEquals("/home/me/vault/notes/a.md", parsed.laptopPath)
        assertEquals("/sdcard/Obsidian/Notes/notes/a.md", parsed.phonePath)
    }

    @Test
    fun `a copy line parses the same way`() {
        val parsed = parseActionLine("→ phone  a.txt   new on laptop   (laptop: /l/a.txt  ·  phone: /sdcard/a.txt)")
        assertEquals("→ phone  a.txt   new on laptop", parsed.head)
        assertEquals("/l/a.txt", parsed.laptopPath)
        assertEquals("/sdcard/a.txt", parsed.phonePath)
    }

    @Test
    fun `paths with spaces and parentheses survive`() {
        val parsed = parseActionLine("x  phone  a (1).pdf   deleted on laptop, so removed from the phone   (laptop: /home/me/My Docs/a (1).pdf  ·  phone: /sdcard/My Docs/a (1).pdf)")
        assertEquals("/home/me/My Docs/a (1).pdf", parsed.laptopPath)
        assertEquals("/sdcard/My Docs/a (1).pdf", parsed.phonePath)
    }

    @Test
    fun `a line from an older daemon without paths is shown as it is`() {
        val parsed = parseActionLine("x phone  a.txt   deleted on laptop")
        assertEquals("x phone  a.txt   deleted on laptop", parsed.head)
        assertNull(parsed.laptopPath)
        assertNull(parsed.phonePath)
    }
}
