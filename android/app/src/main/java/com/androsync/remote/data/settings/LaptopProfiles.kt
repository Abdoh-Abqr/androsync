package com.androsync.remote.data.settings

import java.util.UUID
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/** One laptop the app can control: where to find it and the token that proves the phone may (task 33c). */
@Serializable
data class LaptopProfile(
    val id: String,
    val name: String,
    /** The saved LAN or custom address; empty when there is none. */
    val baseUrl: String = "",
    val tailscaleUrl: String = "",
    val token: String = "",
)

/** Pure list handling for the saved laptops, kept apart from DataStore so it runs in plain JVM tests. */
object LaptopProfiles {
    /** Id of the laptop made from the single setup of earlier versions. */
    const val MIGRATED_ID = "default"
    const val DEFAULT_NAME = "My laptop"

    private val json = Json { ignoreUnknownKeys = true }

    fun parse(text: String?): List<LaptopProfile> =
        if (text.isNullOrBlank()) emptyList()
        else runCatching { json.decodeFromString(ListSerializer(LaptopProfile.serializer()), text) }.getOrDefault(emptyList())

    fun encode(list: List<LaptopProfile>): String = json.encodeToString(ListSerializer(LaptopProfile.serializer()), list)

    fun newId(): String = UUID.randomUUID().toString().take(8)

    /** Replaces the profile with the same id, or appends. */
    fun upsert(list: List<LaptopProfile>, profile: LaptopProfile): List<LaptopProfile> =
        if (list.any { it.id == profile.id }) list.map { if (it.id == profile.id) profile else it } else list + profile

    fun remove(list: List<LaptopProfile>, id: String): List<LaptopProfile> = list.filterNot { it.id == id }

    /**
     * The saved list, or - when there is none yet but an earlier version saved a single setup - a list with that setup
     * as the one laptop, so nobody has to scan again after updating.
     */
    fun withMigration(list: List<LaptopProfile>, baseUrl: String, tailscaleUrl: String, token: String): List<LaptopProfile> =
        if (list.isEmpty() && token.isNotBlank()) {
            listOf(LaptopProfile(MIGRATED_ID, DEFAULT_NAME, baseUrl, tailscaleUrl, token))
        } else {
            list
        }

    /** The profile that is selected, falling back to the first one. */
    fun selected(list: List<LaptopProfile>, selectedId: String?): LaptopProfile? =
        list.firstOrNull { it.id == selectedId } ?: list.firstOrNull()

    /** A name for a scanned laptop: its own name, else "Laptop 2" style so two are never both called the same. */
    fun displayName(scanned: String?, existing: List<LaptopProfile>): String {
        val base = scanned?.trim().orEmpty().ifEmpty { "Laptop" }
        if (existing.none { it.name == base }) return base
        var n = 2
        while (existing.any { it.name == "$base $n" }) n++
        return "$base $n"
    }
}
