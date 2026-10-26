package com.androsync.remote.ui.support

import java.security.MessageDigest

/**
 * Hides what identifies a person, a phone or a laptop before anything is put in a problem report (Phase S1). Pure Kotlin,
 * the same rules as the laptop's `androsync/support.py`: tokens, addresses, phone serials and, when asked, pair names
 * and folders become placeholders.
 */
class Redactor(
    private val secrets: List<String> = emptyList(),
    private val serials: List<String> = emptyList(),
    /** exact text -> replacement (pair names and folders), applied longest first. */
    private val names: Map<String, String> = emptyMap(),
) {
    fun redact(text: String): String {
        var out = text
        for ((original, replacement) in names.entries.sortedByDescending { it.key.length }) {
            if (original.isNotEmpty()) out = out.replace(original, replacement)
        }
        for (secret in secrets.distinct().sortedByDescending { it.length }) {
            if (secret.isNotEmpty()) out = out.replace(secret, "<token>")
        }
        out = BEARER.replace(out) { it.groupValues[1] + "<token>" }
        out = TOKEN_FIELD.replace(out) { it.groupValues[1] + "<token>" }
        for (serial in serials.distinct().sortedByDescending { it.length }) {
            if (serial.length >= 4) out = out.replace(serial, "phone-" + shortHash(serial))
        }
        return IPV4.replace(out) { classifyIp(it.value) ?: it.value }
    }

    companion object {
        private val IPV4 = Regex("""\b(?:\d{1,3}\.){3}\d{1,3}\b""")
        private val BEARER = Regex("""(Bearer\s+)[A-Za-z0-9_\-]{16,}""")
        private val TOKEN_FIELD = Regex("""(["']?token["']?\s*[:=]\s*["']?)[A-Za-z0-9_\-]{16,}""", RegexOption.IGNORE_CASE)

        /** The placeholder for an IPv4 address, or null to keep it (loopback) or when it is not an address. */
        fun classifyIp(text: String): String? {
            val parts = text.split(".").map { it.toIntOrNull() ?: return null }
            if (parts.size != 4 || parts.any { it !in 0..255 }) return null
            val (a, b) = parts[0] to parts[1]
            return when {
                a == 127 || a == 0 -> null
                a == 100 && b in 64..127 -> "<tailscale-ip>"
                a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168) || (a == 169 && b == 254) -> "<lan-ip>"
                else -> "<ip>"
            }
        }

        fun shortHash(text: String): String =
            MessageDigest.getInstance("SHA-256").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }.take(6)
    }
}
