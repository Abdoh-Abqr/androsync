package com.androsync.remote.ui.support

/** What a problem report is made of (Phase S1). Everything here is plain text; the builder redacts it all. */
data class ReportInfo(
    val appVersion: String,
    val flavor: String,
    val androidVersion: String,
    val model: String,
    val laptopVersion: String?,
    val connection: String,
    val agentStatus: String?,
    val phones: List<String> = emptyList(),
    val jobs: List<String> = emptyList(),
    val appLog: List<String> = emptyList(),
)

/** Builds the text a person shares: versions first, then the laptop view, recent jobs and the app's own log. */
object ReportBuilder {
    fun build(info: ReportInfo, redactor: Redactor): String {
        val text = buildString {
            appendLine("AndroSync problem report")
            appendLine("app: ${info.appVersion} (${info.flavor})")
            appendLine("android: ${info.androidVersion}, ${info.model}")
            appendLine("laptop: ${info.laptopVersion ?: "not reachable"}")
            appendLine("connection: ${info.connection}")
            if (info.agentStatus != null) appendLine("agent: ${info.agentStatus}")
            if (info.phones.isNotEmpty()) {
                appendLine()
                appendLine("phones the laptop sees:")
                info.phones.forEach { appendLine("  $it") }
            }
            if (info.jobs.isNotEmpty()) {
                appendLine()
                appendLine("recent syncs:")
                info.jobs.forEach { appendLine("  $it") }
            }
            appendLine()
            appendLine("app log (last ${info.appLog.size} lines):")
            if (info.appLog.isEmpty()) appendLine("  (empty)") else info.appLog.forEach { appendLine("  $it") }
            appendLine()
            appendLine("Describe what happened and what you expected above this line.")
        }
        return redactor.redact(text)
    }
}
