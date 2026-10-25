package com.androsync.remote.data.settings

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.androsync.remote.data.api.ApiClient
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "androsync_settings")

/** Default poll interval for the "not reachable while app is open" ping loop (PLAN.md). */
const val DEFAULT_AUTO_REFRESH_SECONDS = 5

/**
 * Persists connection settings (laptop URL, bearer token, which [Preset] is
 * active, and the auto-refresh interval) across app restarts via
 * Jetpack DataStore Preferences.
 */
class SettingsRepository(context: Context) {

    private val dataStore = context.applicationContext.dataStore

    private object Keys {
        val BASE_URL = stringPreferencesKey("base_url")
        val TOKEN = stringPreferencesKey("token")
        val PRESET = stringPreferencesKey("preset")
        val AUTO_REFRESH_SECONDS = intPreferencesKey("auto_refresh_seconds")
        val TAILSCALE_URL = stringPreferencesKey("tailscale_url")
        val WIDGET_STATE_JSON = stringPreferencesKey("widget_state_json")
        val SETUP_HINTS = booleanPreferencesKey("setup_hints")
    }

    /** The URL currently in effect: the LAN URL the user saved, or a fixed preset URL. */
    val baseUrl: Flow<String> = dataStore.data.map { prefs ->
        prefs[Keys.BASE_URL] ?: ApiClient.PRESET_USB_URL
    }

    val token: Flow<String> = dataStore.data.map { prefs -> prefs[Keys.TOKEN] ?: "" }

    val preset: Flow<Preset> = dataStore.data.map { prefs ->
        prefs[Keys.PRESET]?.let { name -> runCatching { Preset.valueOf(name) }.getOrNull() }
            ?: Preset.USB
    }

    val autoRefreshSeconds: Flow<Int> = dataStore.data.map { prefs ->
        prefs[Keys.AUTO_REFRESH_SECONDS] ?: DEFAULT_AUTO_REFRESH_SECONDS
    }

    /**
     * The Tailscale/"Remote" preset's URL (task 22: editable in Settings) - blank until the user
     * scans a QR code or types one in, never a built-in default (task 33e).
     */
    val tailscaleUrl: Flow<String> = dataStore.data.map { prefs -> prefs[Keys.TAILSCALE_URL] ?: "" }

    /** Last-known widget content (task 21), cached for offline display; see `widget/WidgetState.kt`. */
    val widgetStateJson: Flow<String?> = dataStore.data.map { prefs -> prefs[Keys.WIDGET_STATE_JSON] }

    /** Whether to post "USB debugging is off" when a cable is plugged in (task 37f); on by default. */
    val setupHintsEnabled: Flow<Boolean> = dataStore.data.map { prefs -> prefs[Keys.SETUP_HINTS] ?: true }

    suspend fun setSetupHintsEnabled(enabled: Boolean) {
        dataStore.edit { it[Keys.SETUP_HINTS] = enabled }
    }

    suspend fun setBaseUrl(url: String) {
        dataStore.edit { it[Keys.BASE_URL] = url }
    }

    suspend fun setToken(token: String) {
        dataStore.edit { it[Keys.TOKEN] = token }
    }

    suspend fun setPreset(preset: Preset) {
        dataStore.edit { it[Keys.PRESET] = preset.name }
    }

    suspend fun setAutoRefreshSeconds(seconds: Int) {
        dataStore.edit { it[Keys.AUTO_REFRESH_SECONDS] = seconds }
    }

    suspend fun setTailscaleUrl(url: String) {
        dataStore.edit { it[Keys.TAILSCALE_URL] = url }
    }

    suspend fun setWidgetStateJson(json: String) {
        dataStore.edit { it[Keys.WIDGET_STATE_JSON] = json }
    }

    /** "Forget setup" (task 22): wipes every saved key, including the token. */
    suspend fun clear() {
        dataStore.edit { it.clear() }
    }
}
