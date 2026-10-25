package com.androsync.remote.agent

import com.androsync.remote.data.PhonePath
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.UUID
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/** One operation from the laptop's `GET /v1/agent/next` (wire format: docs/agent-protocol.md). */
data class AgentOp(
    val id: String,
    val name: String,
    val args: JsonObject,
    /** The laptop has a body for us: fetch it with [AgentBlobs.download] first. */
    val upload: Boolean,
    /** We must send a body with [AgentBlobs.upload]. */
    val download: Boolean,
)

/** Moves file bodies to and from the laptop. The real one is [AgentClient]; tests use a fake. */
interface AgentBlobs {
    /** `GET /v1/agent/blob/<op>` into [into]. */
    fun download(opId: String, into: File)

    /** `PUT /v1/agent/blob/<op>` with the bytes of [from]. */
    fun upload(opId: String, from: File)
}

/** A failure to report to the laptop as `{"ok": false, "error": message}`. */
class AgentException(message: String, cause: Throwable? = null) : Exception(message, cause)

/**
 * Does the file work the laptop asks for (task 37c, issue #15), with plain `java.io.File` under the All files access
 * permission. The spike (task 37a) showed that `setLastModified`, rename, hashing and scanning behave on the phone.
 *
 * Pure JVM code: [guard] decides which folders may be touched (by default shared storage only, the same rule as
 * `PhonePath`), [onChanged] is told about every path that was written or deleted so the service can run a media scan.
 */
class AgentOps(
    private val guard: (String) -> String = ::sharedStorageOnly,
    private val onChanged: (File) -> Unit = {},
) {
    /** Runs [op] and returns the JSON `result`. Throws [AgentException] with a message fit for the user. */
    fun run(op: AgentOp, blobs: AgentBlobs): JsonElement {
        return try {
            when (op.name) {
                "scan" -> scan(op)
                "stat" -> stat(op)
                "hash_many" -> hashMany(op)
                "fetch" -> fetch(op, blobs)
                "store" -> store(op, blobs)
                "delete" -> delete(op)
                "rename" -> rename(op)
                "mkdirs" -> mkdirs(op)
                "exists" -> buildJsonObject { put("exists", resolveRoot(op).isDirectory) }
                "ls" -> ls(op)
                else -> throw AgentException("unknown operation '${op.name}'")
            }
        } catch (e: AgentException) {
            throw e
        } catch (e: IOException) {
            throw AgentException("${op.name}: ${e.message ?: e.javaClass.simpleName}", e)
        } catch (e: SecurityException) {
            throw AgentException("${op.name}: permission denied (${e.message}). Is All files access allowed?", e)
        }
    }

    // -- arguments ---------------------------------------------------------------------------------------------------

    private fun str(op: AgentOp, key: String): String =
        op.args[key]?.jsonPrimitive?.contentOrNull ?: throw AgentException("${op.name}: missing '$key'")

    private fun resolveRoot(op: AgentOp, key: String = "root"): File = File(guard(str(op, key)))

    /** `rel` must be a clean relative path: no `..`, no empty or `.` segments, not absolute. */
    private fun cleanRel(rel: String): String {
        val parts = rel.split("/")
        if (rel.isEmpty() || parts.any { it.isEmpty() || it == "." || it == ".." }) {
            throw AgentException("unsafe relative path: '$rel'")
        }
        return rel
    }

    private fun fileOf(root: File, rel: String): File = File(root, cleanRel(rel))

    // -- operations --------------------------------------------------------------------------------------------------

    private fun scan(op: AgentOp): JsonElement {
        val root = resolveRoot(op)
        if (!root.isDirectory) return buildJsonObject { put("exists", false); put("files", JsonArray(emptyList())) }
        val files = buildJsonArray {
            val stack = ArrayDeque<Pair<File, String>>()
            stack.addLast(root to "")
            while (stack.isNotEmpty()) {
                val (dir, prefix) = stack.removeLast()
                val children = dir.listFiles() ?: continue
                for (child in children) {
                    val rel = if (prefix.isEmpty()) child.name else "$prefix/${child.name}"
                    if (Files.isSymbolicLink(child.toPath())) continue
                    when {
                        child.isDirectory -> stack.addLast(child to rel)
                        child.isFile -> add(buildJsonArray {
                            add(JsonPrimitive(rel))
                            add(JsonPrimitive(child.length()))
                            add(JsonPrimitive(child.lastModified() / 1000.0))
                        })
                    }
                }
            }
        }
        return buildJsonObject { put("exists", true); put("files", files) }
    }

    private fun stat(op: AgentOp): JsonElement {
        val file = fileOf(resolveRoot(op), str(op, "rel"))
        if (!file.isFile) return buildJsonObject { put("exists", false) }
        return buildJsonObject {
            put("exists", true)
            put("size", file.length())
            put("mtime", file.lastModified() / 1000.0)
        }
    }

    private fun hashMany(op: AgentOp): JsonElement {
        val root = resolveRoot(op)
        val rels = op.args["rels"]?.jsonArray ?: throw AgentException("hash_many: missing 'rels'")
        val hashes = buildJsonObject {
            for (item in rels) {
                val rel = item.jsonPrimitive.content
                put(rel, sha256(fileOf(root, rel)))
            }
        }
        return buildJsonObject { put("hashes", hashes) }
    }

    private fun fetch(op: AgentOp, blobs: AgentBlobs): JsonElement {
        val file = fileOf(resolveRoot(op), str(op, "rel"))
        if (!file.isFile) throw AgentException("fetch: no such file: ${str(op, "rel")}")
        val size = file.length()
        val mtime = file.lastModified() / 1000.0
        blobs.upload(op.id, file)
        return buildJsonObject { put("size", size); put("mtime", mtime) }
    }

    private fun store(op: AgentOp, blobs: AgentBlobs): JsonElement {
        val root = resolveRoot(op)
        val target = fileOf(root, str(op, "rel"))
        val mtimeMs = Math.round((op.args["mtime"]?.jsonPrimitive?.doubleOrNull ?: throw AgentException("store: missing 'mtime'")) * 1000)
        val size = op.args["size"]?.jsonPrimitive?.longOrNull
        // The body lands in a hidden folder first and is moved into place when complete, so a Stop or a dropped
        // connection never leaves a half-written file in the synced folder.
        val incoming = File(root, ".androsync/incoming/${UUID.randomUUID()}")
        if (!incoming.mkdirs() && !incoming.isDirectory) throw AgentException("store: cannot create ${incoming.path}")
        val part = File(incoming, "part")
        try {
            blobs.download(op.id, part)
            if (size != null && part.length() != size) {
                throw AgentException("store: received ${part.length()} bytes, expected $size")
            }
            part.setLastModified(mtimeMs)
            target.parentFile?.let { if (!it.isDirectory && !it.mkdirs()) throw AgentException("store: cannot create ${it.path}") }
            Files.move(part.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
            if (Math.abs(target.lastModified() - mtimeMs) > 1000) target.setLastModified(mtimeMs)
        } finally {
            part.delete()
            incoming.delete()
        }
        onChanged(target)
        return buildJsonObject {}
    }

    private fun delete(op: AgentOp): JsonElement {
        val root = resolveRoot(op)
        val file = fileOf(root, str(op, "rel"))
        if (file.isDirectory) throw AgentException("delete: ${str(op, "rel")} is a folder")
        if (file.exists() && !file.delete()) throw AgentException("delete: could not delete ${str(op, "rel")}")
        onChanged(file)
        var parent = file.parentFile
        val rootPath = root.canonicalPath
        while (parent != null && parent.canonicalPath != rootPath && parent.canonicalPath.startsWith(rootPath)) {
            val inside = parent.list()
            if (inside == null || inside.isNotEmpty() || !parent.delete()) break
            parent = parent.parentFile
        }
        return buildJsonObject {}
    }

    private fun rename(op: AgentOp): JsonElement {
        val root = resolveRoot(op)
        val from = fileOf(root, str(op, "from"))
        val to = fileOf(root, str(op, "to"))
        to.parentFile?.let { if (!it.isDirectory && !it.mkdirs()) throw AgentException("rename: cannot create ${it.path}") }
        Files.move(from.toPath(), to.toPath(), StandardCopyOption.REPLACE_EXISTING)
        onChanged(from)
        onChanged(to)
        return buildJsonObject {}
    }

    private fun mkdirs(op: AgentOp): JsonElement {
        val root = resolveRoot(op)
        val rel = op.args["rel_dir"]?.jsonPrimitive?.contentOrNull.orEmpty()
        val dir = if (rel.isEmpty()) root else fileOf(root, rel)
        if (!dir.isDirectory && !dir.mkdirs()) throw AgentException("mkdirs: cannot create ${dir.path}")
        return buildJsonObject {}
    }

    private fun ls(op: AgentOp): JsonElement {
        val dir = resolveRoot(op, "path")
        val children = dir.listFiles() ?: throw AgentException("ls: cannot read ${dir.path}")
        val entries = buildJsonArray {
            for (child in children.sortedBy { it.name }) {
                add(buildJsonObject {
                    put("name", child.name)
                    put("is_dir", child.isDirectory)
                    put("size", if (child.isFile) child.length() else 0L)
                })
            }
        }
        return buildJsonObject { put("entries", entries) }
    }

    companion object {
        /** The default folder rule: normalize like `PhonePath`, refuse anything outside shared storage. */
        fun sharedStorageOnly(path: String): String {
            val normalized = PhonePath.normalize(path)
            PhonePath.validate(normalized)?.let { throw AgentException(it) }
            return normalized
        }

        fun sha256(file: File): String {
            val digest = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input ->
                val buffer = ByteArray(1 shl 20)
                while (true) {
                    val n = input.read(buffer)
                    if (n < 0) break
                    digest.update(buffer, 0, n)
                }
            }
            return digest.digest().joinToString("") { "%02x".format(it) }
        }
    }
}
