package com.androsync.remote.data.settings

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** Several laptops in one app (task 33c): the pure list rules, then the repository on a real (temporary) DataStore. */
class LaptopProfilesTest {
    private fun laptop(id: String, name: String = id, token: String = "tok-$id", lan: String = "", ts: String = "") =
        LaptopProfile(id, name, baseUrl = lan, tailscaleUrl = ts, token = token)

    @Test
    fun `upsert replaces a laptop with the same id and appends a new one`() {
        val list = listOf(laptop("a"), laptop("b"))
        assertEquals(listOf("a", "b", "c"), LaptopProfiles.upsert(list, laptop("c")).map { it.id })
        assertEquals("renamed", LaptopProfiles.upsert(list, laptop("b", "renamed")).first { it.id == "b" }.name)
        assertEquals(2, LaptopProfiles.upsert(list, laptop("b", "renamed")).size)
    }

    @Test
    fun `encode and parse round trip, and rubbish parses to an empty list`() {
        val list = listOf(laptop("a", lan = "http://192.0.2.1:8765", ts = "http://100.64.0.1:8765"))
        assertEquals(list, LaptopProfiles.parse(LaptopProfiles.encode(list)))
        assertEquals(emptyList<LaptopProfile>(), LaptopProfiles.parse("not json"))
        assertEquals(emptyList<LaptopProfile>(), LaptopProfiles.parse(null))
    }

    @Test
    fun `an earlier single setup becomes the one laptop, and an empty setup stays empty`() {
        val migrated = LaptopProfiles.withMigration(emptyList(), "http://192.0.2.1:8765", "", "secret")
        assertEquals(1, migrated.size)
        assertEquals(LaptopProfiles.MIGRATED_ID, migrated[0].id)
        assertEquals("secret", migrated[0].token)
        assertEquals(emptyList<LaptopProfile>(), LaptopProfiles.withMigration(emptyList(), "", "", ""))
        assertEquals(listOf(laptop("a")), LaptopProfiles.withMigration(listOf(laptop("a")), "x", "y", "z"))
    }

    @Test
    fun `selected falls back to the first laptop`() {
        val list = listOf(laptop("a"), laptop("b"))
        assertEquals("b", LaptopProfiles.selected(list, "b")!!.id)
        assertEquals("a", LaptopProfiles.selected(list, "gone")!!.id)
        assertNull(LaptopProfiles.selected(emptyList(), "a"))
    }

    @Test
    fun `two scanned laptops with the same name get distinct names`() {
        val existing = listOf(laptop("a", "work"))
        assertEquals("work 2", LaptopProfiles.displayName("work", existing))
        assertEquals("home", LaptopProfiles.displayName("home", existing))
        assertEquals("Laptop", LaptopProfiles.displayName(null, emptyList()))
        assertEquals("work 3", LaptopProfiles.displayName("work", existing + laptop("b", "work 2")))
    }

    // --- the repository on a real DataStore ---------------------------------------------------------------

    @get:Rule
    val tmp = TemporaryFolder()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @After
    fun tearDown() = scope.cancel()

    private fun repo(): SettingsRepository {
        val store = PreferenceDataStoreFactory.create(scope = scope) { File(tmp.root, "s.preferences_pb") }
        return SettingsRepository(store)
    }

    @Test
    fun `an old single setup shows up as one selected laptop`() = runBlocking {
        val r = repo()
        r.setToken("old-token")
        r.setBaseUrl("http://192.0.2.1:8765")
        val laptops = r.laptops.first()
        assertEquals(1, laptops.size)
        assertEquals("old-token", laptops[0].token)
        assertEquals(laptops[0].id, r.selectedLaptopId.first())
    }

    @Test
    fun `saving a second laptop with select makes the whole app use it, and selecting the first switches back`() = runBlocking {
        val r = repo()
        r.saveLaptop(laptop("home", token = "tok-home", ts = "http://100.64.0.1:8765"))
        r.saveLaptop(laptop("work", token = "tok-work", ts = "http://100.64.0.2:8765"))
        assertEquals("tok-work", r.token.first())
        assertEquals("http://100.64.0.2:8765", r.tailscaleUrl.first())
        assertEquals("work", r.selectedLaptopId.first())

        r.selectLaptop("home")
        assertEquals("tok-home", r.token.first())
        assertEquals("http://100.64.0.1:8765", r.tailscaleUrl.first())
        assertEquals(listOf("home", "work"), r.laptops.first().map { it.id })
    }

    @Test
    fun `editing the active address updates the selected laptop's saved copy only`() = runBlocking {
        val r = repo()
        r.saveLaptop(laptop("home", token = "tok-home"))
        r.saveLaptop(laptop("work", token = "tok-work"))
        r.setTailscaleUrl("http://100.64.0.9:8765")
        r.setToken("tok-work-new")
        val byId = r.laptops.first().associateBy { it.id }
        assertEquals("tok-work-new", byId.getValue("work").token)
        assertEquals("http://100.64.0.9:8765", byId.getValue("work").tailscaleUrl)
        assertEquals("tok-home", byId.getValue("home").token)
    }

    @Test
    fun `removing the selected laptop selects the next, and removing the last one clears the connection`() = runBlocking {
        val r = repo()
        r.saveLaptop(laptop("home", token = "tok-home"))
        r.saveLaptop(laptop("work", token = "tok-work"))
        r.removeLaptop("work")
        assertEquals("home", r.selectedLaptopId.first())
        assertEquals("tok-home", r.token.first())
        r.removeLaptop("home")
        assertEquals("", r.token.first())
        assertEquals(emptyList<LaptopProfile>(), r.laptops.first())
    }

    @Test
    fun `the saved LAN address is empty by default and a loopback one is ignored`() = runBlocking {
        val r = repo()
        assertEquals("", r.baseUrl.first())
        assertNull(r.lanUrl.first())
        r.setBaseUrl("http://127.0.0.1:8765")
        assertNull(r.lanUrl.first())
        r.setBaseUrl(" http://192.0.2.5:8765 ")
        assertEquals("http://192.0.2.5:8765", r.lanUrl.first())
    }
}
