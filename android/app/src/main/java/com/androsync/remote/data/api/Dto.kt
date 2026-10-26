package com.androsync.remote.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire-format DTOs for the androsync daemon's HTTP API.
 *
 * These mirror the JSON shapes produced by `laptop/androsync/daemon/server.py`
 * and `laptop/androsync/daemon/jobs.py` (`job_to_dict`, `pairs_status`)
 * exactly - see PLAN.md "HTTP API" for the endpoint list. Field names keep
 * the daemon's snake_case on the wire via [SerialName] while exposing
 * idiomatic camelCase to Kotlin callers.
 */

/** `GET /v1/ping` -> {name, version, transport: "usb"|"wifi"|null, device: serial|null, current_job: {...}|null} */
@Serializable
data class PingDto(
    val name: String,
    val version: String,
    val transport: String? = null,
    val device: String? = null,
    /** The sync running on the laptop right now, whoever started it (task 36b, issue #7); null when idle. */
    @SerialName("current_job") val currentJob: CurrentJobDto? = null,
    /** Every phone adb knows and its state, so the app can say what is missing (task 37f, issue #10). */
    val devices: List<DeviceStateDto> = emptyList(),
    /** "connected" when the phone app's agent is polling the laptop (task 37b), else "absent". */
    val agent: String? = null,
)

/** One phone as adb sees it: [state] is "device" (ready), "unauthorized" (waiting for Allow) or "offline". */
@Serializable
data class DeviceStateDto(
    val serial: String,
    val state: String,
    val transport: String? = null,
    /** The phone's hardware serial: what a pair's `device` holds (task 33a). Same over USB and Wi-Fi. */
    val id: String? = null,
    /** The name the user gave the phone (`androsync phone set`), or null. */
    val name: String? = null,
)

/** The running job as every ping reports it: enough for the "sync running" bar on any screen of any device. */
@Serializable
data class CurrentJobDto(
    val id: String,
    val pairs: List<String> = emptyList(),
    /** "sync" or "undo". */
    val kind: String = "sync",
    /** "queued", "running" or "cancelling" (a job in a final state is no longer reported). */
    val state: String = "running",
    @SerialName("dry_run") val dryRun: Boolean = false,
    /** Epoch seconds. */
    val started: Double? = null,
    /** Latest log line without its time stamp. */
    @SerialName("last_line") val lastLine: String? = null,
)

/** One entry of `GET /v1/pairs` -> [{name, local, remote, direction, conflict, last_run, last_result}] */
@Serializable
data class PairDto(
    val name: String,
    val local: String,
    val remote: String,
    val direction: String,
    val conflict: String,
    /** "adb" (default) or "agent": how the laptop reaches the phone for this pair (task 37d). */
    val transport: String = "adb",
    /** Off = paused (task 30b `androsync undo --pause`): status/run and auto-sync skip it. */
    val enabled: Boolean = true,
    @SerialName("last_run") val lastRun: String? = null,
    @SerialName("last_result") val lastResult: String? = null,
    /** "any" or the id of the phone this pair belongs to (task 33a). */
    val device: String = "any",
    /** Syncs by itself when files change (Phase T2). */
    val auto: Boolean = false,
    @SerialName("run_conditions") val runConditions: RunConditionsDto = RunConditionsDto(),
    @SerialName("auto_state") val autoState: AutoStateDto? = null,
)

/** Wi-Fi only, charging only and a time window for automatic syncs (Phase T3); [schedule] looks like `22:00-07:00`. */
@Serializable
data class RunConditionsDto(
    @SerialName("wifi_only") val wifiOnly: Boolean = false,
    @SerialName("charging_only") val chargingOnly: Boolean = false,
    val schedule: String? = null,
)

/** What automatic sync is doing for a pair: "off", "watching", "waiting" or "paused" (with the [reason]). */
@Serializable
data class AutoStateDto(
    val state: String = "off",
    val reason: String? = null,
)

/**
 * Sync counters, e.g. from a job's `summaries` map or `/v1/status/<pair>`'s `summary`.
 *
 * Also doubles as an *undo* run's summary shape (`restoredLocal`/`restoredRemote`/
 * `skipped`, task 30b) - the two never overlap on a field name, so one DTO
 * covers both `GET /v1/runs/<pair>`'s `summary` (either kind) and a sync
 * job's `summaries` map (always the sync shape) without a second class.
 */
@Serializable
data class SummaryDto(
    @SerialName("copied_to_local") val copiedToLocal: Int = 0,
    @SerialName("copied_to_remote") val copiedToRemote: Int = 0,
    @SerialName("deleted_local") val deletedLocal: Int = 0,
    @SerialName("deleted_remote") val deletedRemote: Int = 0,
    val conflicts: Int = 0,
    val asks: Int = 0,
    @SerialName("restored_local") val restoredLocal: Int = 0,
    @SerialName("restored_remote") val restoredRemote: Int = 0,
    val skipped: List<String> = emptyList(),
    val errors: Int = 0,
    @SerialName("run_id") val runId: String? = null,
)

/** `GET /v1/status/<pair>` -> {pair, actions: [...], summary: {...}} (a dry-run plan). */
@Serializable
data class StatusDto(
    val pair: String,
    val actions: List<String> = emptyList(),
    val summary: SummaryDto,
)

/** Request body of `POST /v1/run` -> {pairs: [...]|null, dry_run: bool}. */
@Serializable
data class RunRequest(
    val pairs: List<String>? = null,
    @SerialName("dry_run") val dryRun: Boolean = false,
    /** True after the user said yes to a plan the laptop stopped for being large (task 36d, issue #9). */
    @SerialName("confirm_large") val confirmLarge: Boolean = false,
)

/** 202 response of `POST /v1/run` -> {job_id}. */
@Serializable
data class RunResponse(
    @SerialName("job_id") val jobId: String,
)

/**
 * `GET /v1/jobs` (list) and `GET /v1/jobs/<id>` (single), as produced by
 * `jobs.job_to_dict`. Timestamps are Unix epoch seconds (Python `time.time()`),
 * possibly fractional.
 */
@Serializable
data class JobDto(
    val id: String,
    val pairs: List<String> = emptyList(),
    @SerialName("dry_run") val dryRun: Boolean = false,
    /** "sync" (the default, pre-task-30b jobs never sent it) or "undo". */
    val kind: String = "sync",
    val state: String,
    val created: Double = 0.0,
    val started: Double? = null,
    val finished: Double? = null,
    val summaries: Map<String, SummaryDto> = emptyMap(),
    /** Set only for kind="undo": the run this job undid, and its outcome. */
    @SerialName("undo_run_id") val undoRunId: String? = null,
    @SerialName("undo_summary") val undoSummary: SummaryDto? = null,
    val error: String? = null,
    /** Per pair: why the run was stopped before changing anything (state "needs_confirmation", task 36d). */
    val confirmation: Map<String, List<String>> = emptyMap(),
    @SerialName("log_tail") val logTail: List<String> = emptyList(),
)

/** One entry of `GET /v1/runs/<pair>` (task 30b): a recorded sync or undo run. */
@Serializable
data class RunDto(
    val pair: String,
    @SerialName("run_id") val runId: String,
    /** "sync" or "undo". */
    val kind: String,
    val started: String,
    val finished: Boolean = false,
    @SerialName("finished_at") val finishedAt: String? = null,
    val summary: SummaryDto? = null,
)

/** Request body of `POST /v1/undo/<pair>` -> {run_id?, dry_run?, force?, pause?}. */
@Serializable
data class UndoRequest(
    @SerialName("run_id") val runId: String? = null,
    @SerialName("dry_run") val dryRun: Boolean = false,
    val force: Boolean = false,
    val pause: Boolean = false,
)

/** Every non-2xx response body the daemon sends is `{"error": "..."}`, sometimes plus extra fields. */
@Serializable
data class ErrorDto(
    val error: String,
    @SerialName("job_id") val jobId: String? = null,
)

/** `POST /v1/jobs/<id>/cancel` -> {ok: true}. */
@Serializable
data class OkDto(
    val ok: Boolean = true,
)

/**
 * Request body of `POST /v1/pairs` and `PUT /v1/pairs/<name>` (task 23b).
 *
 * `conflict`/`propagateDeletes`/`ignore` are null when the user leaves them
 * at "use the default" - the daemon treats a JSON `null` the same as an
 * omitted field (see `daemon/server.py`'s `_drop_null_fields`), so the
 * config only ends up with an explicit `conflict:`/`propagate_deletes:`
 * line when the user actually chose a non-default value, matching
 * `config.example.yaml`'s own style.
 */
@Serializable
data class PairRequest(
    val name: String,
    val local: String,
    val remote: String,
    val direction: String,
    val conflict: String? = null,
    @SerialName("propagate_deletes") val propagateDeletes: Boolean? = null,
    val ignore: List<String>? = null,
    val enabled: Boolean? = null,
    /** Omitted for the default, adb; "agent" makes the phone app do the file work (task 37d). */
    val transport: String? = null,
    /** Omitted for any phone; otherwise the id of the phone the pair belongs to (task 33a). */
    val device: String? = null,
    /** Sent only when on; omitted means off (Phase T2). */
    val auto: Boolean? = null,
    /** Sent only when any condition is set; omitted clears them (Phase T3). */
    @SerialName("run_conditions") val runConditions: RunConditionsDto? = null,
)

/** One entry of `GET /v1/browse` -> {..., entries: [{name, is_dir, size, hidden}], ...}. */
@Serializable
data class BrowseEntryDto(
    val name: String,
    @SerialName("is_dir") val isDir: Boolean,
    val size: Long,
    val hidden: Boolean,
)

/** `GET /v1/browse?side=local|remote&path=<abs>` -> {path, parent, entries, writable}. */
@Serializable
data class BrowseDto(
    val path: String,
    val parent: String? = null,
    val entries: List<BrowseEntryDto> = emptyList(),
    val writable: Boolean = false,
)

/** Request body of `POST /v1/mkdir` -> {side, path}. */
@Serializable
data class MkdirRequest(
    val side: String,
    val path: String,
)

/** Response of `POST /v1/token/rotate` -> {token}. */
@Serializable
data class TokenResponse(
    val token: String,
)

/** 201 response of `POST /v1/mkdir` -> {path}. */
@Serializable
data class MkdirResponse(
    val path: String,
)

/** One entry of `GET /v1/pending/<pair>` -> [{rel, local, remote, reason}]. */
@Serializable
data class PendingEntryDto(
    val rel: String,
    val local: PendingSideDto? = null,
    val remote: PendingSideDto? = null,
    val reason: String = "",
)

@Serializable
data class PendingSideDto(
    val size: Long,
    val mtime: Double,
)

/**
 * One entry of `GET /v1/conflicts/<pair>` -> [{when, rel, size, mtime}] (task 29b/29c):
 * one saved folder-mode conflict backup under `.androsync/conflicts/<when>/<rel>` on the laptop.
 * `when` is a Kotlin keyword, so the property is renamed on the Kotlin side only.
 */
@Serializable
data class ConflictVersionDto(
    @SerialName("when") val whenStamp: String,
    val rel: String,
    val size: Long,
    val mtime: Double,
)

/** Request body of `POST /v1/conflicts/<pair>/restore` -> {rel, when?}. */
@Serializable
data class ConflictRestoreRequest(
    val rel: String,
    @SerialName("when") val whenStamp: String? = null,
)

/** Response of `POST /v1/conflicts/<pair>/restore` -> {rel, when}. */
@Serializable
data class ConflictRestoreResponse(
    val rel: String,
    @SerialName("when") val whenStamp: String,
)

/** Request body of `POST /v1/conflicts/<pair>/clean` -> {dry_run}. */
@Serializable
data class ConflictsCleanRequest(
    @SerialName("dry_run") val dryRun: Boolean = true,
)

/**
 * One entry of a clean response's `actions` list: what `clean()` did (or, under
 * `dry_run`, would do) for one pre-task-29b `*.conflict-*` duplicate.
 */
@Serializable
data class CleanActionDto(
    val rel: String,
    @SerialName("original_rel") val originalRel: String,
    @SerialName("identical_to_original") val identicalToOriginal: Boolean,
    val outcome: String,
)

/** Response of `POST /v1/conflicts/<pair>/clean` -> {dry_run, actions: [...]}. */
@Serializable
data class ConflictsCleanResponse(
    @SerialName("dry_run") val dryRun: Boolean,
    val actions: List<CleanActionDto> = emptyList(),
)
