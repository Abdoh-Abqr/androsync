package com.androsync.remote.data.settings

import com.androsync.remote.data.api.ApiClient

/**
 * The three ways the phone can reach the laptop daemon (PLAN.md "Network,
 * Tailscale and firewall"): a fixed USB loopback URL (works once `adb reverse
 * tcp:8765 tcp:8765` is set up), or a "Remote" URL - reached over Tailscale
 * (works from anywhere the phone has internet) or the same Wi-Fi network -
 * that always comes from a scanned QR code (`androsync qr`) or typed by hand
 * in Settings (task 33e: no address of any particular network is built in,
 * unlike [USB]'s loopback, which is universal rather than personal).
 */
enum class Preset {
    USB,
    TAILSCALE,
    LAN,
    ;

    companion object {
        /** The fixed URL for [USB]; null for [TAILSCALE]/[LAN], which are always user/QR-provided. */
        fun fixedUrlFor(preset: Preset): String? = when (preset) {
            USB -> ApiClient.PRESET_USB_URL
            TAILSCALE, LAN -> null
        }
    }
}
