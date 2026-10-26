package com.androsync.remote.data.api

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * Exercises [ApiClient] against a [MockWebServer] standing in for the
 * androsync daemon, using JSON shapes copied from
 * `laptop/androsync/daemon/jobs.py` (`pairs_status`, `job_to_dict`) and
 * `laptop/androsync/daemon/server.py`'s route handlers.
 */
class ApiClientTest {

    private lateinit var server: MockWebServer
    private lateinit var client: ApiClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = ApiClient(server.url("/").toString(), token = "test-token")
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `ping parses transport and device including nulls`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"name": "androsync", "version": "0.3.0", "transport": null, "device": null}""",
                ),
        )

        val ping = client.ping()

        assertEquals("androsync", ping.name)
        assertEquals("0.3.0", ping.version)
        assertNull(ping.transport)
        assertNull(ping.device)

        val recorded = server.takeRequest()
        assertEquals("/v1/ping", recorded.path)
        assertEquals("Bearer test-token", recorded.getHeader("Authorization"))
    }

    @Test
    fun `ping parses non-null transport and device`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"name": "androsync", "version": "0.3.0", "transport": "usb", "device": "R58N30ABCDE"}""",
                ),
        )

        val ping = client.ping()

        assertEquals("usb", ping.transport)
        assertEquals("R58N30ABCDE", ping.device)
    }

    @Test
    fun `pairs parses the exact JSON pairs_status produces`() = runTest {
        // Copied from the shape of `androsync.daemon.jobs.pairs_status`: one
        // dict per configured pair, `last_run`/`last_result` null until a job
        // has completed for that pair at least once.
        val body = """
            [
              {
                "name": "photos",
                "local": "/home/user/Pictures/Phone/DCIM",
                "remote": "/sdcard/DCIM",
                "direction": "phone-to-laptop",
                "conflict": "newest-wins",
                "last_run": "2026-09-01T12:03:41.512309",
                "last_result": "copied_to_local=3 copied_to_remote=0 deleted_local=0 deleted_remote=0 conflicts=0 asks=0 errors=0"
              },
              {
                "name": "vault",
                "local": "/home/user/Documents/Obsidian/MyVault",
                "remote": "/sdcard/Documents/Obsidian/MyVault",
                "direction": "two-way",
                "conflict": "newest-wins",
                "last_run": null,
                "last_result": null
              }
            ]
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))

        val pairs = client.pairs()

        assertEquals(2, pairs.size)

        val photos = pairs[0]
        assertEquals("photos", photos.name)
        assertEquals("/home/user/Pictures/Phone/DCIM", photos.local)
        assertEquals("/sdcard/DCIM", photos.remote)
        assertEquals("phone-to-laptop", photos.direction)
        assertEquals("newest-wins", photos.conflict)
        assertEquals("2026-09-01T12:03:41.512309", photos.lastRun)
        assertTrue(photos.lastResult!!.contains("copied_to_local=3"))

        val vault = pairs[1]
        assertEquals("vault", vault.name)
        assertEquals("two-way", vault.direction)
        assertNull(vault.lastRun)
        assertNull(vault.lastResult)
    }

    @Test
    fun `401 response maps to ApiException with code 401`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(401)
                .setBody("""{"error": "unauthorized"}"""),
        )

        try {
            client.pairs()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(401, e.code)
            assertEquals("unauthorized", e.message)
        }
    }

    @Test
    fun `409 busy response carries job_id through ApiException`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(409)
                .setBody("""{"error": "busy", "job_id": "ab12cd34"}"""),
        )

        try {
            client.run(pairs = null, dryRun = false)
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(409, e.code)
            assertEquals("busy", e.message)
            assertEquals("ab12cd34", e.jobId)
        }
    }

    @Test
    fun `run posts the right JSON body and reads job_id`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(202)
                .setBody("""{"job_id": "9f8e7d6c"}"""),
        )

        val response = client.run(pairs = listOf("photos", "vault"), dryRun = true)

        assertEquals("9f8e7d6c", response.jobId)

        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/run", recorded.path)
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""pairs":["photos","vault"]"""))
        assertTrue(sentBody.contains(""""dry_run":true"""))
    }

    @Test
    fun `run with null pairs omits or nulls the pairs field`() = runTest {
        server.enqueue(MockResponse().setResponseCode(202).setBody("""{"job_id": "00000000"}"""))

        client.run(pairs = null, dryRun = false)

        val recorded = server.takeRequest()
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""dry_run":false"""))
        assertTrue(sentBody.contains(""""pairs":null"""))
    }

    @Test
    fun `createPair sends null for unset optional fields`() = runTest {
        // The daemon treats JSON null the same as an omitted key for
        // conflict/propagate_deletes/ignore (daemon/server.py's _drop_null_fields) -
        // this pins down that ApiClient actually sends those fields as null
        // rather than, say, silently dropping them client-side.
        server.enqueue(MockResponse().setResponseCode(201).setBody("[]"))

        client.createPair(
            PairRequest(name = "vault", local = "/home/x/vault", remote = "/sdcard/vault", direction = "two-way"),
        )

        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/pairs", recorded.path)
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""conflict":null"""))
        assertTrue(sentBody.contains(""""propagate_deletes":null"""))
        assertTrue(sentBody.contains(""""ignore":null"""))
    }

    @Test
    fun `createPair sends explicit non-default values`() = runTest {
        server.enqueue(MockResponse().setResponseCode(201).setBody("[]"))

        client.createPair(
            PairRequest(
                name = "photos",
                local = "/home/x/photos",
                remote = "/sdcard/DCIM",
                direction = "phone-to-laptop",
                conflict = "remote-wins",
                propagateDeletes = false,
                ignore = listOf(".trash/**"),
            ),
        )

        val sentBody = server.takeRequest().body.readUtf8()
        assertTrue(sentBody.contains(""""conflict":"remote-wins""""))
        assertTrue(sentBody.contains(""""propagate_deletes":false"""))
        assertTrue(sentBody.contains(""""ignore":[".trash/**"]"""))
    }

    @Test
    fun `updatePair puts to the pair-named path`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("[]"))

        client.updatePair(
            "vault",
            PairRequest(name = "vault", local = "/x", remote = "/sdcard/x", direction = "two-way"),
        )

        val recorded = server.takeRequest()
        assertEquals("PUT", recorded.method)
        assertEquals("/v1/pairs/vault", recorded.path)
    }

    @Test
    fun `updatePair with a renamed pair sends the new name in the body but the old name in the URL`() = runTest {
        // task 29c: PairEditorScreen no longer disables the Name field for an
        // existing pair, so a rename PUTs to the pair's *current* (pre-rename)
        // path with the *new* name in the body - matching daemon/server.py's
        // `_h_pairs_update`, which renames state files when body.name != path name.
        server.enqueue(MockResponse().setResponseCode(200).setBody("[]"))

        client.updatePair(
            "vault-old",
            PairRequest(name = "vault-new", local = "/x", remote = "/sdcard/x", direction = "two-way"),
        )

        val recorded = server.takeRequest()
        assertEquals("PUT", recorded.method)
        assertEquals("/v1/pairs/vault-old", recorded.path)
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""name":"vault-new""""))
    }

    @Test
    fun `deletePair sends purge_state query param only when true`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("[]"))
        server.enqueue(MockResponse().setResponseCode(200).setBody("[]"))

        client.deletePair("vault", purgeState = false)
        client.deletePair("vault", purgeState = true)

        val first = server.takeRequest()
        assertEquals("DELETE", first.method)
        assertEquals("/v1/pairs/vault?purge_state=0", first.path)
        val second = server.takeRequest()
        assertEquals("/v1/pairs/vault?purge_state=1", second.path)
    }

    @Test
    fun `browse parses entries path parent and writable`() = runTest {
        val body = """
            {
              "path": "/sdcard/DCIM",
              "parent": "/sdcard",
              "entries": [
                {"name": "Camera", "is_dir": true, "size": 0, "hidden": false},
                {"name": ".thumbnails", "is_dir": true, "size": 0, "hidden": true},
                {"name": "a.jpg", "is_dir": false, "size": 12345, "hidden": false}
              ],
              "writable": true
            }
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))

        val result = client.browse("remote", "/sdcard/DCIM")

        assertEquals("/sdcard/DCIM", result.path)
        assertEquals("/sdcard", result.parent)
        assertEquals(3, result.entries.size)
        assertEquals(true, result.entries[0].isDir)
        assertEquals(true, result.entries[1].hidden)
        assertEquals(12345L, result.entries[2].size)
        assertTrue(result.writable)

        val recorded = server.takeRequest()
        assertEquals("GET", recorded.method)
        assertEquals("/v1/browse?side=remote&path=%2Fsdcard%2FDCIM", recorded.path)
    }

    @Test
    fun `browse 403 outside roots maps to ApiException`() = runTest {
        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"error": "outside the allowed browse roots"}"""))

        try {
            client.browse("local", "/etc")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(403, e.code)
        }
    }

    @Test
    fun `browse 503 phone not connected maps to ApiException`() = runTest {
        server.enqueue(MockResponse().setResponseCode(503).setBody("""{"error": "phone not connected"}"""))

        try {
            client.browse("remote", "")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(503, e.code)
        }
    }

    @Test
    fun `mkdir posts side and path and reads the path back`() = runTest {
        server.enqueue(MockResponse().setResponseCode(201).setBody("""{"path": "/sdcard/NewFolder"}"""))

        val response = client.mkdir("remote", "/sdcard/NewFolder")

        assertEquals("/sdcard/NewFolder", response.path)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/mkdir", recorded.path)
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""side":"remote""""))
        assertTrue(sentBody.contains(""""path":"/sdcard/NewFolder""""))
    }

    @Test
    fun `pending parses entries with null sides`() = runTest {
        val body = """
            [
              {"rel": "note.md", "local": {"size": 10, "mtime": 1700000000.0}, "remote": null, "reason": "modified on both sides"}
            ]
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))

        val pending = client.pending("vault")

        assertEquals(1, pending.size)
        assertEquals("note.md", pending[0].rel)
        assertEquals(10L, pending[0].local?.size)
        assertNull(pending[0].remote)
    }

    @Test
    fun `job parses job_to_dict shape including summaries`() = runTest {
        val body = """
            {
              "id": "a1b2c3d4",
              "pairs": ["photos"],
              "dry_run": false,
              "state": "done",
              "created": 1893456000.0,
              "started": 1893456001.5,
              "finished": 1893456009.25,
              "summaries": {
                "photos": {
                  "copied_to_local": 3,
                  "copied_to_remote": 0,
                  "deleted_local": 0,
                  "deleted_remote": 0,
                  "conflicts": 0,
                  "asks": 0,
                  "errors": 0
                }
              },
              "error": null,
              "log_tail": ["12:00:01 photos: copy DCIM/a.jpg", "12:00:02 photos: done"]
            }
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))

        val job = client.job("a1b2c3d4")

        assertEquals("done", job.state)
        assertEquals(listOf("photos"), job.pairs)
        assertEquals(3, job.summaries.getValue("photos").copiedToLocal)
        assertEquals(2, job.logTail.size)
        assertNull(job.error)
    }

    @Test
    fun `conflicts parses when rel size and mtime`() = runTest {
        val body = """
            [
              {"when": "20260905-153000", "rel": "note.md", "size": 42, "mtime": 1893456000.0}
            ]
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))

        val versions = client.conflicts("vault")

        assertEquals(1, versions.size)
        assertEquals("20260905-153000", versions[0].whenStamp)
        assertEquals("note.md", versions[0].rel)
        assertEquals(42L, versions[0].size)

        val recorded = server.takeRequest()
        assertEquals("GET", recorded.method)
        assertEquals("/v1/conflicts/vault", recorded.path)
    }

    @Test
    fun `restoreConflict posts rel and when and parses the response`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"rel": "note.md", "when": "20260905-153000"}"""),
        )

        val response = client.restoreConflict("vault", "note.md", whenStamp = "20260905-153000")

        assertEquals("note.md", response.rel)
        assertEquals("20260905-153000", response.whenStamp)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/conflicts/vault/restore", recorded.path)
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""rel":"note.md""""))
        assertTrue(sentBody.contains(""""when":"20260905-153000""""))
    }

    @Test
    fun `restoreConflict without when sends json null`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"rel": "note.md", "when": "20260905-153000"}"""),
        )

        client.restoreConflict("vault", "note.md")

        val recorded = server.takeRequest()
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""when":null"""))
    }

    @Test
    fun `cleanConflicts sends dry_run and parses actions`() = runTest {
        val body = """
            {
              "dry_run": true,
              "actions": [
                {"rel": "a.conflict-laptop-20260101-000000.txt", "original_rel": "a.txt", "identical_to_original": true, "outcome": "would_delete"}
              ]
            }
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))

        val response = client.cleanConflicts("vault", dryRun = true)

        assertTrue(response.dryRun)
        assertEquals(1, response.actions.size)
        assertEquals("a.txt", response.actions[0].originalRel)
        assertEquals(true, response.actions[0].identicalToOriginal)
        assertEquals("would_delete", response.actions[0].outcome)

        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/conflicts/vault/clean", recorded.path)
        assertTrue(recorded.body.readUtf8().contains(""""dry_run":true"""))
    }

    @Test
    fun `runs parses sync and undo run shapes`() = runTest {
        val body = """
            [
              {
                "pair": "vault",
                "run_id": "20260907-153000-1a2b",
                "kind": "undo",
                "started": "2026-09-07T15:30:00.123456",
                "finished": true,
                "finished_at": "2026-09-07T15:30:01.000000",
                "summary": {"restored_local": 0, "restored_remote": 1, "skipped": ["note.md"], "errors": 0, "run_id": "20260907-153005-9c9c"}
              },
              {
                "pair": "vault",
                "run_id": "20260907-152000-aaaa",
                "kind": "sync",
                "started": "2026-09-07T15:20:00.000000",
                "finished": false,
                "summary": null
              }
            ]
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))

        val runs = client.runs("vault")

        assertEquals(2, runs.size)
        val undo = runs[0]
        assertEquals("undo", undo.kind)
        assertEquals("20260907-153000-1a2b", undo.runId)
        assertTrue(undo.finished)
        assertEquals(1, undo.summary?.restoredRemote)
        assertEquals(listOf("note.md"), undo.summary?.skipped)

        val sync = runs[1]
        assertEquals("sync", sync.kind)
        assertFalse(sync.finished)
        assertNull(sync.summary)

        val recorded = server.takeRequest()
        assertEquals("GET", recorded.method)
        assertEquals("/v1/runs/vault", recorded.path)
    }

    @Test
    fun `runs empty pair returns empty list`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200).setBody("[]"))

        val runs = client.runs("vault")

        assertTrue(runs.isEmpty())
    }

    @Test
    fun `undo posts run_id dry_run force and pause`() = runTest {
        server.enqueue(MockResponse().setResponseCode(202).setBody("""{"job_id": "9f8e7d6c"}"""))

        val response = client.undo("vault", runId = "20260907-153000-1a2b", pause = true, force = true, dryRun = false)

        assertEquals("9f8e7d6c", response.jobId)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/v1/undo/vault", recorded.path)
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""run_id":"20260907-153000-1a2b""""))
        assertTrue(sentBody.contains(""""dry_run":false"""))
        assertTrue(sentBody.contains(""""force":true"""))
        assertTrue(sentBody.contains(""""pause":true"""))
    }

    @Test
    fun `undo without run_id sends json null`() = runTest {
        server.enqueue(MockResponse().setResponseCode(202).setBody("""{"job_id": "00000000"}"""))

        client.undo("vault")

        val recorded = server.takeRequest()
        assertEquals("/v1/undo/vault", recorded.path)
        val sentBody = recorded.body.readUtf8()
        assertTrue(sentBody.contains(""""run_id":null"""))
        assertTrue(sentBody.contains(""""dry_run":false"""))
        assertTrue(sentBody.contains(""""force":false"""))
        assertTrue(sentBody.contains(""""pause":false"""))
    }

    @Test
    fun `undo 404 no runs recorded maps to ApiException`() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"error": "no runs recorded for pair 'vault'"}"""))

        try {
            client.undo("vault")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(404, e.code)
        }
    }

    @Test
    fun `run sends confirm_large only as asked and the job reply carries the reasons`() = runTest {
        server.enqueue(MockResponse().setBody("""{"job_id":"j9"}"""))
        server.enqueue(MockResponse().setBody("""{"job_id":"j10"}"""))
        server.enqueue(
            MockResponse().setBody(
                """{"id":"j9","pairs":["vault"],"state":"needs_confirmation","confirmation":{"vault":["It would delete 785 files on the laptop."]}}""",
            ),
        )
        client.run(listOf("vault"))
        client.run(listOf("vault"), confirmLarge = true)
        val first = server.takeRequest().body.readUtf8()
        val second = server.takeRequest().body.readUtf8()
        assertTrue(first.contains(""""confirm_large":false"""))
        assertTrue(second.contains(""""confirm_large":true"""))

        val job = client.job("j9")
        assertEquals("needs_confirmation", job.state)
        assertEquals(listOf("It would delete 785 files on the laptop."), job.confirmation["vault"])
    }

    @Test
    fun `pairs parses the transport, and an older daemon without it means adb`() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """[{"name":"a","local":"/x","remote":"/sdcard/a","direction":"two-way","conflict":"newest-wins","transport":"agent"},
                   {"name":"b","local":"/y","remote":"/sdcard/b","direction":"two-way","conflict":"newest-wins"}]""",
            ),
        )
        assertEquals(listOf("agent", "adb"), client.pairs().map { it.transport })
    }

    @Test
    fun `a pair request sends transport agent, and null for the default adb`() = runTest {
        server.enqueue(MockResponse().setResponseCode(201).setBody("[]"))
        server.enqueue(MockResponse().setResponseCode(201).setBody("[]"))
        client.createPair(PairRequest(name = "a", local = "/x", remote = "/sdcard/a", direction = "two-way", transport = "agent"))
        client.createPair(PairRequest(name = "b", local = "/x", remote = "/sdcard/b", direction = "two-way"))
        assertTrue(server.takeRequest().body.readUtf8().contains(""""transport":"agent""""))
        assertTrue(server.takeRequest().body.readUtf8().contains(""""transport":null"""))
    }

    @Test
    fun `the ping parses the agent field`() = runTest {
        server.enqueue(MockResponse().setBody("""{"name":"androsync","version":"0.1.0","agent":"connected"}"""))
        assertEquals("connected", client.ping().agent)
    }

    @Test
    fun `rotateToken posts to the rotate endpoint and returns the new token`() = runTest {
        server.enqueue(MockResponse().setBody("""{"token":"brand-new"}"""))
        assertEquals("brand-new", client.rotateToken())
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/v1/token/rotate", request.path)
    }

    @Test
    fun `pairs parse the automatic sync flag and state, and a request sends auto only when on`() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """[{"name":"a","local":"/x","remote":"/sdcard/a","direction":"two-way","conflict":"newest-wins","auto":true,"auto_state":{"state":"paused","reason":"needs your OK"}}]""",
            ),
        )
        val pair = client.pairs().single()
        assertEquals(true, pair.auto)
        assertEquals("paused", pair.autoState?.state)
        server.takeRequest() // the GET /v1/pairs above
        server.enqueue(MockResponse().setResponseCode(201).setBody("[]"))
        client.createPair(PairRequest(name = "b", local = "/x", remote = "/sdcard/b", direction = "two-way", auto = true))
        assertTrue(server.takeRequest().body.readUtf8().contains(""""auto":true"""))
    }

    @Test
    fun `pairs parse run conditions, and a request sends them only when set`() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """[{"name":"a","local":"/x","remote":"/sdcard/a","direction":"two-way","conflict":"newest-wins","auto":true,"run_conditions":{"wifi_only":true,"charging_only":false,"schedule":"22:00-07:00"}}]""",
            ),
        )
        val conditions = client.pairs().single().runConditions
        assertEquals(true, conditions.wifiOnly)
        assertEquals("22:00-07:00", conditions.schedule)
        server.takeRequest()
        server.enqueue(MockResponse().setResponseCode(201).setBody("[]"))
        client.createPair(PairRequest(name = "b", local = "/x", remote = "/sdcard/b", direction = "two-way", runConditions = RunConditionsDto(wifiOnly = true)))
        assertTrue(server.takeRequest().body.readUtf8().contains(""""wifi_only":true"""))
    }
}
