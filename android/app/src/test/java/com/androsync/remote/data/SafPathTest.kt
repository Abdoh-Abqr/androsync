package com.androsync.remote.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SafPathTest {

    @Test
    fun `primary root maps to sdcard`() {
        assertEquals(
            "/sdcard",
            SafPath.treeUriToPath("content://com.android.externalstorage.documents/tree/primary%3A"),
        )
    }

    @Test
    fun `primary subfolder maps under sdcard`() {
        assertEquals(
            "/sdcard/Documents/Obsidian",
            SafPath.treeUriToPath(
                "content://com.android.externalstorage.documents/tree/primary%3ADocuments%2FObsidian",
            ),
        )
    }

    @Test
    fun `sd card volume maps under storage`() {
        assertEquals(
            "/storage/1234-5678/DCIM",
            SafPath.treeUriToPath(
                "content://com.android.externalstorage.documents/tree/1234-5678%3ADCIM",
            ),
        )
    }

    @Test
    fun `sd card root with no subpath`() {
        assertEquals(
            "/storage/1234-5678",
            SafPath.treeUriToPath("content://com.android.externalstorage.documents/tree/1234-5678%3A"),
        )
    }

    @Test
    fun `unsupported authority returns null`() {
        assertNull(
            SafPath.treeUriToPath(
                "content://com.android.providers.downloads.documents/tree/downloads%3A123",
            ),
        )
    }

    @Test
    fun `url-decodes spaces in the relative path`() {
        assertEquals(
            "/sdcard/My Notes/todo list.md",
            SafPath.treeUriToPath(
                "content://com.android.externalstorage.documents/tree/primary%3AMy%20Notes%2Ftodo%20list.md",
            ),
        )
    }

    @Test
    fun `not a tree uri returns null`() {
        assertNull(
            SafPath.treeUriToPath(
                "content://com.android.externalstorage.documents/document/primary%3Anotes.md",
            ),
        )
    }

    @Test
    fun `garbage input returns null`() {
        assertNull(SafPath.treeUriToPath("not a uri at all"))
        assertNull(SafPath.treeUriToPath(""))
    }
}
