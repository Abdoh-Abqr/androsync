package com.androsync.remote.ui.support

/**
 * A small in-memory log of what the app did lately (connection changes, agent status, token actions), kept only so a
 * problem report can include it. Never written to disk, never sent anywhere by the app itself, lost when the process
 * ends. Lines go through [Redactor] before they leave the screen.
 */
object AppLog {
    const val CAPACITY = 200
    private val lines = ArrayDeque<String>()

    @Synchronized
    fun add(tag: String, message: String, now: Long = System.currentTimeMillis()) {
        lines.addLast("${java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.US).format(now)} [$tag] $message")
        while (lines.size > CAPACITY) lines.removeFirst()
    }

    @Synchronized
    fun snapshot(): List<String> = lines.toList()

    @Synchronized
    fun clear() = lines.clear()
}
