package com.androsync.remote.ui.pairs

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Pure-function tests for [validatePairForm]/[defaultPropagateDeletes] -
 * the parts of [PairEditorViewModel] that do not need a network client, so
 * they run on the plain JVM (PLAN.md task 23c: "form validation").
 */
class PairEditorValidationTest {

    @Test
    fun `valid form returns null`() {
        assertNull(validatePairForm("vault", "/home/x/vault", "/sdcard/vault"))
    }

    @Test
    fun `empty name is rejected`() {
        assertEquals("Name is required", validatePairForm("", "/home/x/vault", "/sdcard/vault"))
    }

    @Test
    fun `blank name is rejected`() {
        assertEquals("Name is required", validatePairForm("   ", "/home/x/vault", "/sdcard/vault"))
    }

    @Test
    fun `empty local folder is rejected`() {
        assertEquals("Laptop folder is required", validatePairForm("vault", "", "/sdcard/vault"))
    }

    @Test
    fun `empty remote folder is rejected`() {
        assertEquals("Phone folder is required", validatePairForm("vault", "/home/x/vault", ""))
    }

    @Test
    fun `remote folder must start with a slash`() {
        assertEquals(
            "Phone folder must be an absolute path starting with /",
            validatePairForm("vault", "/home/x/vault", "sdcard/vault"),
        )
    }

    @Test
    fun `storage labels are accepted because they are corrected on save`() {
        assertNull(validatePairForm("inbox", "/home/x/inbox", "/Internal storage/Inbox/"))
    }

    @Test
    fun `filesystem root and system folders are refused with a hint`() {
        val root = validatePairForm("inbox", "/home/x/inbox", "/")
        assertEquals(true, root?.contains("Pick a folder under /sdcard"))
        val data = validatePairForm("inbox", "/home/x/inbox", "/data/app")
        assertEquals(true, data?.contains("Pick a folder under /sdcard"))
    }

    @Test
    fun `a folder missing its storage prefix suggests the sdcard path`() {
        val message = validatePairForm("notes", "/home/x/notes", "/Documents/Notes")
        assertEquals(true, message?.contains("Did you mean /sdcard/Documents/Notes?"))
    }

    @Test
    fun `default propagate deletes is off for phone-to-laptop`() {
        assertEquals(false, defaultPropagateDeletes(Direction.PHONE_TO_LAPTOP))
    }

    @Test
    fun `default propagate deletes is on for laptop-to-phone and two-way`() {
        assertEquals(true, defaultPropagateDeletes(Direction.LAPTOP_TO_PHONE))
        assertEquals(true, defaultPropagateDeletes(Direction.TWO_WAY))
    }

    private fun editing(local: String, remote: String, isNew: Boolean = false) = PairEditorUiState(
        isNew = isNew, local = local, remote = remote, originalLocal = "/home/x/inbox", originalRemote = "/sdcard/Inbox",
    )

    @Test
    fun `changing the phone or laptop folder of an existing pair is flagged because its history is reset`() {
        assertEquals(true, editing("/home/x/inbox", "/sdcard/Other").foldersChanged)
        assertEquals(true, editing("/home/x/other", "/sdcard/Inbox").foldersChanged)
    }

    @Test
    fun `other spellings of the same folders are not a change, and a new pair never is`() {
        assertEquals(false, editing("/home/x/inbox/", "/Internal storage/Inbox/").foldersChanged)
        assertEquals(false, editing("/home/x/other", "/sdcard/Other", isNew = true).foldersChanged)
    }
}
