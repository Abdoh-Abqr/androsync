package com.androsync.remote.data

/**
 * Rules for folders on the phone (PLAN task 36c, issue #8).
 *
 * A phone folder must live in shared storage: internal storage (`/sdcard/...`) or an SD card or USB drive
 * (`/storage/<id>/...`). Names that file managers show, such as `/Internal storage`, are not real paths, so they
 * are mapped to `/sdcard`. Anything else, such as `/` or `/data`, is refused because syncing it would pull the
 * phone's system folders.
 *
 * This is a copy of `laptop/androsync/phone_path.py`; the laptop checks again, but this gives the same message
 * under the field before anything is sent. Keep both in step. Pure Kotlin so it runs in plain JVM tests.
 */
object PhonePath {
    private const val STORAGE_HINT =
        "Phone folders must be inside internal storage (/sdcard/...) or an SD card (/storage/XXXX-XXXX/...)"

    // Paths that all mean "the phone's internal shared storage"; matched case-insensitively on a whole segment.
    private val ALIASES = listOf(
        "/storage/emulated/0",
        "/storage/self/primary",
        "/mnt/shell/emulated/0",
        "/mnt/sdcard",
        "/internal shared storage",
        "/internal storage",
    )

    // Top-level folders of Android's own file system; used only to decide if a "Did you mean" hint makes sense.
    private val SYSTEM_DIRS = setOf(
        "data", "system", "vendor", "proc", "sys", "dev", "mnt", "storage", "etc", "cache", "product", "odm",
        "apex", "config", "oem", "persist", "efs", "prism", "optics", "bin", "sbin", "lib", "root", "acct",
        "metadata", "debug_ramdisk", "linkerconfig", "system_ext", "vendor_dlkm", "odm_dlkm", "system_dlkm",
        "data_mirror", "postinstall", "init",
    )

    private val PRIVATE_APP_DIRS = listOf("/sdcard/Android/data", "/sdcard/Android/obb")

    // /storage/<id>, but not /storage/emulated or /storage/self.
    private val REMOVABLE = Regex("^/storage/(?!emulated(?:/|$)|self(?:/|$))[^/]+(?:/.*)?$")

    // A second user or work profile: /storage/emulated/10/...
    private val OTHER_USER = Regex("^/storage/emulated/[1-9][0-9]*(?:/.*)?$")

    /** Trims spaces, collapses `//`, drops a trailing `/` and maps storage names to `/sdcard`. */
    fun normalize(path: String): String {
        var text = path.trim()
        if (!text.startsWith("/")) return text
        text = text.replace(Regex("/{2,}"), "/")
        if (text.length > 1) text = text.trimEnd('/').ifEmpty { "/" }
        val lower = text.lowercase()
        for (alias in ALIASES) {
            if (lower == alias || lower.startsWith("$alias/")) return "/sdcard" + text.substring(alias.length)
        }
        return text
    }

    private fun isUnder(path: String, folder: String) = path == folder || path.startsWith("$folder/")

    /** Returns an error message for an already normalized phone path, or null if it is usable. */
    fun validate(path: String): String? {
        if (!path.startsWith("/")) return "Phone folder must be an absolute path starting with /"
        val segments = path.split("/")
        if (".." in segments) return "Phone folder must not contain '..'."
        for (private in PRIVATE_APP_DIRS) {
            if (isUnder(path, private)) {
                return "$path belongs to other apps and Android does not allow access to it. " +
                    "Choose another folder under /sdcard."
            }
        }
        if (isUnder(path, "/sdcard") || REMOVABLE.matches(path) || OTHER_USER.matches(path)) return null

        val first = if (segments.size > 1) segments[1] else ""
        return if (path == "/" || first in SYSTEM_DIRS) {
            "$STORAGE_HINT, not $path. Pick a folder under /sdcard, for example /sdcard/Documents."
        } else {
            "$STORAGE_HINT, not $path. Did you mean /sdcard$path?"
        }
    }
}
