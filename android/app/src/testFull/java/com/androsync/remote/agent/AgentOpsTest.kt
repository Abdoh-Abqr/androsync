package com.androsync.remote.agent

import java.io.File
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** The file operations on a real temp folder. Task 37c (issue #15). */
class AgentOpsTest {
    @get:Rule
    val tmp = TemporaryFolder()

    private val changed = mutableListOf<String>()
    private val ops = AgentOps(guard = { it }, onChanged = { changed += it.name })

    /** Stands in for the laptop's body endpoints. */
    private class FakeBlobs(var toDownload: ByteArray = ByteArray(0)) : AgentBlobs {
        var uploaded: ByteArray? = null
        override fun download(opId: String, into: File) {
            into.writeBytes(toDownload)
        }

        override fun upload(opId: String, from: File) {
            uploaded = from.readBytes()
        }
    }

    private fun op(name: String, args: JsonObject, upload: Boolean = false, download: Boolean = false) =
        AgentOp("id1", name, args, upload, download)

    private fun args(vararg pairs: Pair<String, Any>) = buildJsonObject {
        for ((k, v) in pairs) when (v) {
            is String -> put(k, v)
            is Long -> put(k, v)
            is Int -> put(k, v)
            is Double -> put(k, v)
            is List<*> -> put(k, buildJsonArray { v.forEach { add(JsonPrimitive(it as String)) } })
            else -> error("unsupported $v")
        }
    }

    private fun run(name: String, vararg a: Pair<String, Any>, blobs: AgentBlobs = FakeBlobs(), upload: Boolean = false, download: Boolean = false): JsonElement =
        ops.run(op(name, args(*a), upload, download), blobs)

    private fun file(root: File, rel: String, text: String, mtimeSeconds: Long = 1_700_000_000L): File {
        val f = File(root, rel)
        f.parentFile.mkdirs()
        f.writeText(text)
        f.setLastModified(mtimeSeconds * 1000)
        return f
    }

    @Test
    fun `scan lists regular files with size and mtime in seconds, and ignores folders`() {
        val root = tmp.newFolder("phone")
        file(root, "a.txt", "hello", 1_700_000_100)
        file(root, "sub/deep/b.txt", "world!", 1_700_000_200)
        File(root, "empty-dir").mkdirs()
        val result = run("scan", "root" to root.path).jsonObject
        assertTrue(result["exists"]!!.jsonPrimitive.boolean)
        val files = result["files"]!!.jsonArray.associate {
            val row = it.jsonArray
            row[0].jsonPrimitive.content to (row[1].jsonPrimitive.content.toLong() to row[2].jsonPrimitive.double)
        }
        assertEquals(mapOf("a.txt" to (5L to 1_700_000_100.0), "sub/deep/b.txt" to (6L to 1_700_000_200.0)), files)
    }

    @Test
    fun `scan of a missing folder says it does not exist`() {
        val result = run("scan", "root" to File(tmp.root, "nope").path).jsonObject
        assertFalse(result["exists"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `stat reports a file, and absence`() {
        val root = tmp.newFolder("phone")
        file(root, "x.txt", "abc", 1_650_000_000)
        val hit = run("stat", "root" to root.path, "rel" to "x.txt").jsonObject
        assertEquals(3, hit["size"]!!.jsonPrimitive.content.toInt())
        assertEquals(1_650_000_000.0, hit["mtime"]!!.jsonPrimitive.double, 0.001)
        assertFalse(run("stat", "root" to root.path, "rel" to "no.txt").jsonObject["exists"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `hash_many returns sha256 per file`() {
        val root = tmp.newFolder("phone")
        file(root, "a.txt", "abc")
        val hashes = run("hash_many", "root" to root.path, "rels" to listOf("a.txt")).jsonObject["hashes"]!!.jsonObject
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", hashes["a.txt"]!!.jsonPrimitive.content)
    }

    @Test
    fun `fetch uploads the bytes and reports size and mtime`() {
        val root = tmp.newFolder("phone")
        file(root, "pic.bin", "payload", 1_600_000_000)
        val blobs = FakeBlobs()
        val result = run("fetch", "root" to root.path, "rel" to "pic.bin", blobs = blobs, download = true).jsonObject
        assertEquals("payload", String(blobs.uploaded!!))
        assertEquals(7, result["size"]!!.jsonPrimitive.content.toInt())
        assertEquals(1_600_000_000.0, result["mtime"]!!.jsonPrimitive.double, 0.001)
    }

    @Test
    fun `store writes the body into place with the exact mtime and leaves no leftovers`() {
        val root = tmp.newFolder("phone")
        val blobs = FakeBlobs("new content".toByteArray())
        run("store", "root" to root.path, "rel" to "a/b/c.txt", "mtime" to 1_577_836_800.123, "size" to 11L, blobs = blobs, upload = true)
        val stored = File(root, "a/b/c.txt")
        assertEquals("new content", stored.readText())
        assertEquals(1_577_836_800_123L, stored.lastModified())
        assertTrue(changed.contains("c.txt"))
        val incoming = File(root, ".androsync/incoming")
        assertTrue(incoming.listFiles().isNullOrEmpty())
    }

    @Test
    fun `store replaces an existing file`() {
        val root = tmp.newFolder("phone")
        file(root, "f.txt", "old")
        run("store", "root" to root.path, "rel" to "f.txt", "mtime" to 1_700_000_500.0, "size" to 3L, blobs = FakeBlobs("new".toByteArray()), upload = true)
        assertEquals("new", File(root, "f.txt").readText())
    }

    @Test
    fun `store with a wrong size fails and leaves the target untouched`() {
        val root = tmp.newFolder("phone")
        file(root, "f.txt", "keep me")
        val e = assertThrows(AgentException::class.java) {
            run("store", "root" to root.path, "rel" to "f.txt", "mtime" to 1.0, "size" to 99L, blobs = FakeBlobs("short".toByteArray()), upload = true)
        }
        assertTrue(e.message!!.contains("expected 99"))
        assertEquals("keep me", File(root, "f.txt").readText())
        assertTrue(File(root, ".androsync/incoming").listFiles().isNullOrEmpty())
    }

    @Test
    fun `delete removes the file and now-empty parents but never the root`() {
        val root = tmp.newFolder("phone")
        file(root, "x/y/z.txt", "z")
        file(root, "keep/k.txt", "k")
        run("delete", "root" to root.path, "rel" to "x/y/z.txt")
        assertFalse(File(root, "x").exists())
        assertTrue(File(root, "keep/k.txt").exists())
        run("delete", "root" to root.path, "rel" to "keep/k.txt")
        assertTrue(root.isDirectory)
    }

    @Test
    fun `deleting a missing file is fine, deleting a folder is refused`() {
        val root = tmp.newFolder("phone")
        run("delete", "root" to root.path, "rel" to "ghost.txt")
        File(root, "dir").mkdirs()
        assertThrows(AgentException::class.java) { run("delete", "root" to root.path, "rel" to "dir") }
    }

    @Test
    fun `rename moves a file, creates parents and keeps the mtime`() {
        val root = tmp.newFolder("phone")
        file(root, "old.txt", "o", 1_600_000_000)
        run("rename", "root" to root.path, "from" to "old.txt", "to" to "new/place/name.txt")
        assertFalse(File(root, "old.txt").exists())
        assertEquals(1_600_000_000_000L, File(root, "new/place/name.txt").lastModified())
    }

    @Test
    fun `mkdirs creates folders and an empty rel_dir means the root itself`() {
        val root = File(tmp.root, "fresh/root")
        run("mkdirs", "root" to root.path, "rel_dir" to "")
        assertTrue(root.isDirectory)
        run("mkdirs", "root" to root.path, "rel_dir" to "a/b")
        assertTrue(File(root, "a/b").isDirectory)
    }

    @Test
    fun `exists is true only for folders`() {
        val root = tmp.newFolder("phone")
        file(root, "f.txt", "f")
        assertTrue(run("exists", "root" to root.path).jsonObject["exists"]!!.jsonPrimitive.boolean)
        assertFalse(run("exists", "root" to File(root, "f.txt").path).jsonObject["exists"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `ls lists children with kind and size`() {
        val root = tmp.newFolder("phone")
        file(root, "f.txt", "abc")
        File(root, "d").mkdirs()
        val entries = run("ls", "path" to root.path).jsonObject["entries"]!!.jsonArray.map { it.jsonObject }
        assertEquals(listOf("d", "f.txt"), entries.map { it["name"]!!.jsonPrimitive.content })
        assertEquals(listOf(true, false), entries.map { it["is_dir"]!!.jsonPrimitive.boolean })
        assertEquals(3, entries[1]["size"]!!.jsonPrimitive.content.toInt())
    }

    @Test
    fun `unsafe relative paths are refused`() {
        val root = tmp.newFolder("phone")
        for (bad in listOf("../escape", "a/../../b", "/abs", "a//b", "./x")) {
            assertThrows("rel $bad", AgentException::class.java) { run("delete", "root" to root.path, "rel" to bad) }
        }
    }

    @Test
    fun `the default guard refuses folders outside shared storage and app-private folders`() {
        val strict = AgentOps()
        for (bad in listOf("/data/data/com.x", "/", "/system", "/sdcard/Android/data/com.x")) {
            assertThrows("root $bad", AgentException::class.java) {
                strict.run(op("exists", args("root" to bad)), FakeBlobs())
            }
        }
    }

    @Test
    fun `an unknown operation is reported, not crashed on`() {
        val e = assertThrows(AgentException::class.java) { run("format_disk") }
        assertTrue(e.message!!.contains("unknown operation"))
    }

    @Test
    fun `the json helpers keep numbers precise`() {
        assertEquals(1_577_836_800.123, Json.parseToJsonElement("1577836800.123").jsonPrimitive.double, 0.0)
    }
}
