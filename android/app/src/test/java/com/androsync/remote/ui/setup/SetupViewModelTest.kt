package com.androsync.remote.ui.setup

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.androsync.remote.data.settings.SettingsRepository
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** The Setup form: a scanned QR code fills the labelled fields, and Save puts each address where it belongs. */
@OptIn(ExperimentalCoroutinesApi::class)
class SetupViewModelTest {
    @get:Rule
    val tmp = TemporaryFolder()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @Before
    fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())

    @After
    fun tearDown() {
        Dispatchers.resetMain()
        scope.cancel()
    }

    private fun repo() = SettingsRepository(PreferenceDataStoreFactory.create(scope = scope) { File(tmp.root, "s.preferences_pb") })

    /** `save` runs on the view model's scope and DataStore's own thread, so wait until it reports it is done. */
    private suspend fun saveAndWait(vm: SetupViewModel) {
        var saved = false
        vm.save { saved = true }
        withTimeout(10_000) { while (!saved) delay(10) }
    }

    private val qr = """{"name":"work-laptop","urls":["http://127.0.0.1:8765","http://100.64.0.7:8765"],"token":"tok"}"""

    @Test
    fun `a scan fills the Tailscale field, the token and the laptop name, and leaves the LAN field empty`() {
        val vm = SetupViewModel(repo())
        vm.onQrScanned(qr)
        val s = vm.uiState.value
        assertEquals("http://100.64.0.7:8765", s.tailscaleUrl)
        assertEquals("", s.lanUrl)
        assertEquals("tok", s.token)
        assertEquals("work-laptop", s.scannedName)
    }

    @Test
    fun `a scan with both a Tailscale and a LAN address fills both fields`() {
        val vm = SetupViewModel(repo())
        vm.onQrScanned("""{"urls":["http://127.0.0.1:8765","http://100.64.0.7:8765","http://192.0.2.5:8765"],"token":"t"}""")
        assertEquals("http://100.64.0.7:8765", vm.uiState.value.tailscaleUrl)
        assertEquals("http://192.0.2.5:8765", vm.uiState.value.lanUrl)
    }

    @Test
    fun `saving a scan stores the Tailscale address in the Tailscale field and nothing in the LAN field`() = runBlocking {
        val r = repo()
        val vm = SetupViewModel(r)
        vm.onQrScanned(qr)
        saveAndWait(vm)
        assertEquals("http://100.64.0.7:8765", r.tailscaleUrl.first())
        assertEquals("", r.baseUrl.first())
        assertEquals("tok", r.token.first())
        assertEquals("work-laptop", r.laptops.first().single().name)
    }

    @Test
    fun `a Tailscale address typed into the LAN field is saved as a Tailscale address`() = runBlocking {
        val r = repo()
        val vm = SetupViewModel(r)
        vm.onTokenChange("tok")
        vm.onLanUrlChange("http://100.64.0.9:8765")
        saveAndWait(vm)
        assertEquals("http://100.64.0.9:8765", r.tailscaleUrl.first())
        assertEquals("", r.baseUrl.first())
    }

    @Test
    fun `saving without a token is refused`() {
        val vm = SetupViewModel(repo())
        var saved = false
        vm.save { saved = true }
        assertEquals(false, saved)
    }

    @Test
    fun `a QR code that is not ours is rejected`() {
        val vm = SetupViewModel(repo())
        vm.onQrScanned("hello")
        assertEquals(true, vm.uiState.value.testState is TestConnectionState.Error)
    }
}
