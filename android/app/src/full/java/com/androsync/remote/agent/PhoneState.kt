package com.androsync.remote.agent

/** How the phone is connected and powered, as the agent tells the laptop with each poll (Phase T3). */
object PhoneState {
    /** `&net=wifi&battery=charging`; a part that is not known is left out. */
    fun query(net: String?, charging: Boolean?): String = buildString {
        if (net != null) append("&net=").append(net)
        if (charging != null) append("&battery=").append(if (charging) "charging" else "discharging")
    }

    /** Why the agent should stay quiet, or null: only "on mobile data" when the user asked for that. */
    fun pauseReason(net: String?, pauseOnMobile: Boolean): String? =
        if (pauseOnMobile && net == "mobile") "the phone is on mobile data" else null
}
