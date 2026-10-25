package com.androsync.remote.ui.common

import com.androsync.remote.data.api.ApiClient
import com.androsync.remote.data.settings.Preset
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * JSON payload encoded in the QR code printed by `androsync qr` (PLAN.md
 * "CLI"): every URL the daemon is reachable at (USB, Tailscale, LAN, in no
 * particular order) plus the bearer token.
 */
@Serializable
data class QrPayload(val urls: List<String> = emptyList(), val token: String = "", val name: String? = null)

private val json = Json { ignoreUnknownKeys = true }

/** Parses a scanned QR code's raw text; null if it isn't the expected JSON shape. */
fun parseQrPayload(raw: String): QrPayload? =
    runCatching { json.decodeFromString(QrPayload.serializer(), raw) }.getOrNull()

/**
 * Picks the URL to fill in after a scan (PLAN.md task 18: "fill the token
 * and pick the first URL matching the current preset (or the first URL)"):
 * the one whose value or host matches [preset]'s fixed URL (USB only, task
 * 33e - Tailscale/LAN have none), else - for a non-USB preset - the first
 * URL that is not the USB loopback one, since the user picked a non-USB
 * preset before scanning; else just the first URL. Returns null only when
 * [urls] is empty.
 */
fun pickUrlForPreset(urls: List<String>, preset: Preset): String? {
    if (urls.isEmpty()) return null
    val fixed = Preset.fixedUrlFor(preset)
    if (fixed != null) {
        urls.firstOrNull { it.trimEnd('/') == fixed.trimEnd('/') }?.let { return it }
        val fixedHost = hostOf(fixed)
        if (fixedHost != null) {
            urls.firstOrNull { hostOf(it) == fixedHost }?.let { return it }
        }
        return urls.first()
    }
    val usbHost = hostOf(ApiClient.PRESET_USB_URL)
    return urls.firstOrNull { hostOf(it) != usbHost } ?: urls.first()
}

private fun hostOf(url: String): String? = runCatching { java.net.URI(url).host }.getOrNull()

/** What a scanned QR code means for the saved addresses (the USB address never needs saving: it is fixed). */
data class QrAddresses(val tailscale: String?, val lan: String?)

/**
 * Sorts the scanned URLs by kind: a Tailscale address (the CGNAT range starting at 100.64 up to 100.127, or a `.ts.net` name) goes to the Tailscale
 * field, any other non-loopback address to the LAN field. Before this the scan put the one URL it picked into the LAN
 * field only, so the Tailscale URL had to be copied by hand, and agent mode (which has no USB) could not find the laptop.
 */
fun classifyQrUrls(urls: List<String>): QrAddresses {
    var tailscale: String? = null
    var lan: String? = null
    for (url in urls) {
        val host = hostOf(url) ?: continue
        when {
            host == "localhost" || host.startsWith("127.") -> Unit
            isTailscaleHost(host) -> if (tailscale == null) tailscale = url
            else -> if (lan == null) lan = url
        }
    }
    return QrAddresses(tailscale = tailscale, lan = lan)
}

private fun isTailscaleHost(host: String): Boolean {
    if (host.endsWith(".ts.net")) return true
    val parts = host.split(".")
    if (parts.size != 4) return false
    val first = parts[0].toIntOrNull() ?: return false
    val second = parts[1].toIntOrNull() ?: return false
    return first == 100 && second in 64..127
}

/** True when [url] points at a Tailscale address (see [classifyQrUrls]), whichever settings field it was saved in. */
fun isTailscaleUrl(url: String): Boolean = hostOf(url)?.let(::isTailscaleHost) == true

/**
 * Repairs the saved addresses when a Tailscale address ended up in the LAN field (the old QR scan and the Setup form
 * both did that): returns the corrected `(lan, tailscale)` pair, or null when nothing needs to move.
 * The LAN field becomes empty; an already saved Tailscale address is never overwritten.
 */
fun repairSavedAddresses(lan: String, tailscale: String): Pair<String, String>? {
    if (lan.isBlank() || !isTailscaleUrl(lan)) return null
    return if (tailscale.isBlank()) "" to lan else "" to tailscale
}

