package com.androsync.remote.data

import java.net.URI

/**
 * Converts an Android Storage Access Framework tree URI (from
 * `ActivityResultContracts.OpenDocumentTree`, e.g.
 * `content://com.android.externalstorage.documents/tree/primary%3ADocuments%2FObsidian`)
 * to the plain filesystem path the laptop's ADB-based sync engine needs
 * (`/sdcard/Documents/Obsidian`) - PLAN.md task 23c.
 *
 * Pure string parsing (`java.net.URI`, not `android.net.Uri`/`DocumentsContract`)
 * so it is unit-testable on the plain JVM. Only the primary volume
 * (`primary:...` -> `/sdcard/...`) and other `externalstorage` volumes (an
 * SD card's `XXXX-XXXX:...` -> `/storage/XXXX-XXXX/...`) are supported;
 * any other provider (Downloads, a cloud document provider, ...) returns
 * null since it is not backed by a real filesystem path ADB can reach.
 */
object SafPath {
    private const val EXTERNAL_STORAGE_AUTHORITY = "com.android.externalstorage.documents"
    private const val TREE_PREFIX = "/tree/"

    fun treeUriToPath(uri: String): String? {
        val parsed = runCatching { URI(uri) }.getOrNull() ?: return null
        if (parsed.host != EXTERNAL_STORAGE_AUTHORITY) return null

        val path = parsed.path ?: return null
        if (!path.startsWith(TREE_PREFIX)) return null
        val documentId = path.removePrefix(TREE_PREFIX)

        val colonIndex = documentId.indexOf(':')
        val volumeId = if (colonIndex >= 0) documentId.substring(0, colonIndex) else documentId
        val relativePath = if (colonIndex >= 0) documentId.substring(colonIndex + 1) else ""
        if (volumeId.isEmpty()) return null

        val root = if (volumeId == "primary") "/sdcard" else "/storage/$volumeId"
        return if (relativePath.isEmpty()) root else "$root/$relativePath"
    }
}
