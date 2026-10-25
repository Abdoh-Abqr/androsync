package com.androsync.remote

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Guard against a real IP address being compiled back into the app (task
 * 33e). AndroSync Remote must never ship with any particular laptop's or
 * network's address built in - the USB preset's loopback address
 * (`127.0.0.1`) is the only fixed URL; everything else comes from a scanned
 * QR code or is typed into Settings. A `100.x.x.x` literal anywhere under
 * `app/src/main` is almost certainly a tailnet address that leaked back in
 * from a real setup during development.
 */
class NoHardcodedIpsTest {

    private val ipv4 = Regex("""\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b""")

    @Test
    fun `no tailnet-style IP literal anywhere under app src main`() {
        val mainSrc = resolveMainSrc()
        assertTrue("could not find app/src/main from the test working directory", mainSrc.isDirectory)

        val hits = mainSrc.walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .flatMap { file -> ipv4.findAll(file.readText()).map { "${file.relativeTo(mainSrc)}: ${it.value}" } }
            .toList()

        assertTrue("found a real-looking 100.x.x.x address compiled into the app:\n" + hits.joinToString("\n"), hits.isEmpty())
    }

    /** Gradle's unit test working directory is the module dir (`android/app/`); fall back to searching upward. */
    private fun resolveMainSrc(): File {
        val direct = File("src/main")
        if (direct.isDirectory) return direct
        var dir: File? = File(".").absoluteFile
        while (dir != null) {
            val candidate = File(dir, "app/src/main")
            if (candidate.isDirectory) return candidate
            dir = dir.parentFile
        }
        return direct
    }
}
