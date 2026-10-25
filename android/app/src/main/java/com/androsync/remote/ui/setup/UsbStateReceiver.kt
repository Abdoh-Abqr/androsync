package com.androsync.remote.ui.setup

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import androidx.core.content.ContextCompat
import com.androsync.remote.data.settings.SettingsRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.first
import android.os.SystemClock

/**
 * Watches the sticky `USB_STATE` broadcast and posts [SetupHintNotifier.postDebuggingOff] when a cable goes in
 * while USB debugging is off (task 37f, issue #10).
 *
 * Android does not deliver `USB_STATE` to receivers declared in the manifest, so this is registered from
 * [com.androsync.remote.AndroSyncApplication] and works while the app's process is alive (the app stays cached for
 * a while after use). A killed process gets no hint; the Connect-your-phone screen is the fallback.
 */
class UsbStateReceiver(private val settings: SettingsRepository) : BroadcastReceiver() {
    // Null until the first (sticky) delivery: the state at app start is not a "plug-in".
    private var wasConnected: Boolean? = null
    private var lastNudgeMs: Long? = null

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION) return
        val connected = intent.getBooleanExtra("connected", false)
        val previous = wasConnected
        wasConnected = connected
        if (previous == null) return
        val app = context.applicationContext
        val result = goAsync()
        CoroutineScope(Dispatchers.Default).launch {
            try {
                val now = SystemClock.elapsedRealtime()
                val nudge = shouldNudgeAboutDebugging(
                    nudgesEnabled = settings.setupHintsEnabled.first(),
                    usbConnected = connected,
                    adbEnabled = readPhoneSwitches(app).usbDebuggingOn,
                    wasConnected = previous,
                    nowMs = now,
                    lastNudgeMs = lastNudgeMs,
                )
                if (nudge) {
                    lastNudgeMs = now
                    SetupHintNotifier.postDebuggingOff(app)
                }
            } finally {
                result.finish()
            }
        }
    }

    companion object {
        const val ACTION = "android.hardware.usb.action.USB_STATE"

        fun register(context: Context, settings: SettingsRepository) {
            ContextCompat.registerReceiver(
                context,
                UsbStateReceiver(settings),
                IntentFilter(ACTION),
                ContextCompat.RECEIVER_NOT_EXPORTED,
            )
        }
    }
}
