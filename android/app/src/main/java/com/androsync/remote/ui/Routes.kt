package com.androsync.remote.ui

/** Navigation Compose routes (PLAN.md task 18, pair editor added in task 23c). */
object Routes {
    const val SETUP = "setup"
    const val SETUP_ADD = "setup-add"
    const val SETUP_EDIT = "setup-edit"
    const val PAIRS = "pairs"
    const val JOB_PATTERN = "job/{jobId}"
    const val SETTINGS = "settings"
    const val JOBS = "jobs"
    const val CONNECT = "connect"
    const val SUPPORT = "support"
    const val PAIR_NEW = "pair/new"
    const val PAIR_EDIT_PATTERN = "pair/{name}"

    fun job(id: String): String = "job/$id"
    fun pairEdit(name: String): String = "pair/$name"
}
