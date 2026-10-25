package com.androsync.remote.data.settings

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.androsync.remote.ui.common.isLoopbackUrl
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
class SettingsRepository internal constructor(private val dataStore: DataStore<Preferences>) {

    constructor(context: Context) : this(context.applicationContext.dataStore)

    private object Keys {
        val BASE_URL = stringPreferencesKey("base_url")
        val TOKEN = stringPreferencesKey("token")
        val PRESET = stringPreferencesKey("preset")
        val AUTO_REFRESH_SECONDS = intPreferencesKey("auto_refresh_seconds")
        val TAILSCALE_URL = stringPreferencesKey("tailscale_url")
        val WIDGET_STATE_JSON = stringPreferencesKey("widget_state_json")
        val SETUP_HINTS = booleanPreferencesKey("setup_hints")
        val LAPTOPS_JSON = stringPreferencesKey("laptops_json")
        val SELECTED_LAPTOP = stringPreferencesKey("selected_laptop")
    }

    /** The URL currently in effect: the LAN URL the user saved, or a fixed preset URL. */
    val baseUrl: Flow<String> = dataStore.data.map { prefs -> prefs[Keys.BASE_URL].orEmpty() }

    /**
     * The saved LAN or custom address, or null when there is none (or only a loopback one, which is the USB path and
     * is always tried anyway). Every caller that looks for the laptop uses this, whatever the old "preset" says.
     */
    val lanUrl: Flow<String?> = baseUrl.map { it.trim().takeIf { u -> u.isNotEmpty() && !isLoopbackUrl(u) } }

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

    /**
     * Every laptop the app knows (task 33c). The selected one is also copied into the plain `base_url`, `tailscale_url`
     * and `token` keys above, which everything else (connection poll, tile, widget, agent) keeps reading, so they all
     * act on the selected laptop without knowing profiles exist. A single setup saved by an older version shows up as
     * the one laptop.
     */
    val laptops: Flow<List<LaptopProfile>> = dataStore.data.map { prefs -> currentLaptops(prefs) }

    val selectedLaptopId: Flow<String?> = dataStore.data.map { prefs ->
        LaptopProfiles.selected(currentLaptops(prefs), prefs[Keys.SELECTED_LAPTOP])?.id
    }

    private fun currentLaptops(prefs: Preferences): List<LaptopProfile> =
        LaptopProfiles.withMigration(
            LaptopProfiles.parse(prefs[Keys.LAPTOPS_JSON]),
            prefs[Keys.BASE_URL].orEmpty(),
            prefs[Keys.TAILSCALE_URL].orEmpty(),
            prefs[Keys.TOKEN].orEmpty(),
        )

    private fun applyToActive(prefs: androidx.datastore.preferences.core.MutablePreferences, laptop: LaptopProfile) {
        prefs[Keys.BASE_URL] = laptop.baseUrl
        prefs[Keys.TAILSCALE_URL] = laptop.tailscaleUrl
        prefs[Keys.TOKEN] = laptop.token
        prefs[Keys.SELECTED_LAPTOP] = laptop.id
    }

    /** After a change to an active field, keep the selected laptop's saved copy equal to it. */
    private fun syncSelected(prefs: androidx.datastore.preferences.core.MutablePreferences) {
        val list = currentLaptops(prefs)
        val selected = LaptopProfiles.selected(list, prefs[Keys.SELECTED_LAPTOP]) ?: return
        val updated = selected.copy(
            baseUrl = prefs[Keys.BASE_URL].orEmpty(),
            tailscaleUrl = prefs[Keys.TAILSCALE_URL].orEmpty(),
            token = prefs[Keys.TOKEN].orEmpty(),
        )
        prefs[Keys.LAPTOPS_JSON] = LaptopProfiles.encode(LaptopProfiles.upsert(list, updated))
        prefs[Keys.SELECTED_LAPTOP] = updated.id
    }

    /** Adds or replaces [laptop]; [select] makes it the one the whole app uses from now on. */
    suspend fun saveLaptop(laptop: LaptopProfile, select: Boolean = true) {
        dataStore.edit { prefs ->
            val list = LaptopProfiles.upsert(currentLaptops(prefs), laptop)
            prefs[Keys.LAPTOPS_JSON] = LaptopProfiles.encode(list)
            if (select || prefs[Keys.SELECTED_LAPTOP] == laptop.id) applyToActive(prefs, laptop)
        }
    }

    suspend fun selectLaptop(id: String) {
        dataStore.edit { prefs ->
            val list = currentLaptops(prefs)
            val laptop = list.firstOrNull { it.id == id } ?: return@edit
            prefs[Keys.LAPTOPS_JSON] = LaptopProfiles.encode(list)  // persists a migrated single setup too
            applyToActive(prefs, laptop)
        }
    }

    /** Forgets one laptop. If it was the selected one the first remaining laptop is selected, or nothing is left. */
    suspend fun removeLaptop(id: String) {
        dataStore.edit { prefs ->
            val rest = LaptopProfiles.remove(currentLaptops(prefs), id)
            if (rest.isEmpty()) {
                prefs.remove(Keys.LAPTOPS_JSON)
                prefs.remove(Keys.SELECTED_LAPTOP)
                prefs.remove(Keys.TOKEN)
                prefs.remove(Keys.BASE_URL)
                prefs.remove(Keys.TAILSCALE_URL)
                return@edit
            }
            prefs[Keys.LAPTOPS_JSON] = LaptopProfiles.encode(rest)
            if (prefs[Keys.SELECTED_LAPTOP] == id || rest.none { it.id == prefs[Keys.SELECTED_LAPTOP] }) {
                applyToActive(prefs, rest.first())
            }
        }
    }

    suspend fun setBaseUrl(url: String) {
        dataStore.edit {
            it[Keys.BASE_URL] = url
            syncSelected(it)
        }
    }

    suspend fun setToken(token: String) {
        dataStore.edit {
            it[Keys.TOKEN] = token
            syncSelected(it)
        }
    }

    suspend fun setPreset(preset: Preset) {
        dataStore.edit { it[Keys.PRESET] = preset.name }
    }

    suspend fun setAutoRefreshSeconds(seconds: Int) {
        dataStore.edit { it[Keys.AUTO_REFRESH_SECONDS] = seconds }
    }

    suspend fun setTailscaleUrl(url: String) {
        dataStore.edit {
            it[Keys.TAILSCALE_URL] = url
            syncSelected(it)
        }
    }

    suspend fun setWidgetStateJson(json: String) {
        dataStore.edit { it[Keys.WIDGET_STATE_JSON] = json }
    }

    /** "Forget setup" (task 22): wipes every saved key, including the token. */
    suspend fun clear() {
        dataStore.edit { it.clear() }
    }
}
