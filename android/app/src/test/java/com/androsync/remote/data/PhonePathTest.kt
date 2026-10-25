package com.androsync.remote.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Same tables as laptop/tests/test_phone_path.py: the two copies of the rule must agree. */
class PhonePathTest {

    @Test
    fun `normalize maps labels and tidies slashes`() {
        val cases = mapOf(
            "/Internal storage/Inbox/" to "/sdcard/Inbox",
            "/internal storage" to "/sdcard",
            "/Internal Shared Storage/DCIM" to "/sdcard/DCIM",
            "/storage/emulated/0/Documents" to "/sdcard/Documents",
            "/storage/self/primary" to "/sdcard",
            "/mnt/sdcard/x" to "/sdcard/x",
            "//sdcard//a///b/" to "/sdcard/a/b",
            "  /sdcard/x  " to "/sdcard/x",
            "/sdcard/" to "/sdcard",
            "/" to "/",
            "Documents" to "Documents",
            "/storage/emulated/10/x" to "/storage/emulated/10/x",
            "/storage/ABCD-1234/Music" to "/storage/ABCD-1234/Music",
        )
        for ((given, expected) in cases) assertEquals(given, expected, PhonePath.normalize(given))
    }

    @Test
    fun `shared storage folders are valid`() {
        for (path in listOf("/sdcard", "/sdcard/DCIM", "/storage/ABCD-1234", "/storage/ABCD-1234/Music", "/storage/emulated/10/Documents")) {
            assertNull(path, PhonePath.validate(path))
        }
    }

    @Test
    fun `system and private folders are refused with a reason`() {
        val cases = mapOf(
            "/" to "Pick a folder under /sdcard",
            "/data" to "Pick a folder under /sdcard",
            "/system/app" to "Pick a folder under /sdcard",
            "/storage" to "Pick a folder under /sdcard",
            "/storage/emulated" to "Pick a folder under /sdcard",
            "/storage/self" to "Pick a folder under /sdcard",
            "/sdcard/Android/data" to "belongs to other apps",
            "/sdcard/Android/data/com.example.app" to "belongs to other apps",
            "/sdcard/Android/obb" to "belongs to other apps",
            "/Documents/Obsidian" to "Did you mean /sdcard/Documents/Obsidian?",
            "/sdcardx/foo" to "Did you mean /sdcard/sdcardx/foo?",
            "Documents" to "absolute path starting with /",
            "/sdcard/../data" to "'..'",
        )
        for ((path, fragment) in cases) {
            val message = PhonePath.validate(path)
            assertNotNull(path, message)
            assertTrue("$path: $message", message!!.contains(fragment))
        }
    }

    @Test
    fun `the label path from the incident becomes a valid sdcard folder`() {
        val fixed = PhonePath.normalize("/Internal storage/Master_Inbox/")
        assertEquals("/sdcard/Master_Inbox", fixed)
        assertNull(PhonePath.validate(fixed))
    }
}
