package com.androsync.remote.agent

/**
 * What else to switch on so the phone's own battery manager leaves the agent alone (task 37c). Android's standard
 * "unrestricted battery" exemption is not enough on every phone: several makers add a second layer of their own.
 * Pure text, matched on `Build.MANUFACTURER`.
 */
fun vendorBatteryAdvice(manufacturer: String): String = when (manufacturer.trim().lowercase()) {
    "samsung" ->
        "Samsung: Settings → Battery → Background usage limits → Never sleeping apps → add AndroSync."
    "realme", "oppo", "oneplus" ->
        "Realme/Oppo/OnePlus: Settings → Apps → AndroSync → Battery usage → Allow background activity, " +
            "and switch on Auto-launch (Settings → Apps → Auto launch)."
    "xiaomi", "redmi", "poco" ->
        "Xiaomi: Settings → Apps → AndroSync → Autostart on, and Battery saver → No restrictions."
    "huawei", "honor" ->
        "Huawei/Honor: Settings → Apps → AndroSync → Battery → App launch → Manage manually, all three switches on."
    else ->
        "If the agent still stops, look in your phone's battery settings for a list of apps allowed to run in the background."
}
