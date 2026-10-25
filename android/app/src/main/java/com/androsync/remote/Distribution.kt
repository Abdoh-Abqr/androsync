package com.androsync.remote

/**
 * Which build this is (task 37g, issue #12). [IS_FULL] comes from the Gradle product flavor: true for `full`
 * (GitHub Releases, F-Droid), false for `play` (Google Play, no file permission).
 */
object Distribution {
    val IS_FULL: Boolean = BuildConfig.IS_FULL

    /** True in the build that contains the on-phone agent (task 37c): `full` only. */
    val HAS_AGENT: Boolean = BuildConfig.HAS_AGENT

    /** Whether the checklist should show the "All files access" row. */
    val SHOWS_ALL_FILES_ROW: Boolean get() = IS_FULL && HAS_AGENT
}
