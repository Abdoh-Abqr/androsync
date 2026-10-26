package com.androsync.remote.ui.setup

import com.androsync.remote.data.api.DeviceStateDto
import com.androsync.remote.data.settings.Preset

/**
 * The "Connect your phone" checklist (task 37f, issue #10).
 *
 * Android does not let an app switch USB debugging on by itself, so the app shows where the switch is, opens the
 * exact Settings page, and re-checks when the user comes back. All the decisions are in [computeChecklist], a pure
 * function with no Android types, so every combination is covered by plain JUnit tests.
 */
enum class StepId { DEVELOPER_OPTIONS, USB_DEBUGGING, COMPUTER_ALLOWED, STAYS_ON, ALL_FILES }

/** DONE = ticked, TODO = needs the user, WAITING = cannot be checked yet, INFO = a tip, never "done". */
enum class StepStatus { DONE, TODO, WAITING, INFO }

/** What the step's button opens. NONE = no button. */
enum class StepAction { NONE, OPEN_ABOUT_PHONE, OPEN_DEVELOPER_OPTIONS, OPEN_ALL_FILES_ACCESS }

data class ChecklistStep(
    val id: StepId,
    val title: String,
    val detail: String,
    val status: StepStatus,
    val action: StepAction = StepAction.NONE,
    val actionLabel: String? = null,
)

/** What the laptop says about the phone, from the ping's `devices` list. */
enum class DeviceReadiness {
    /** The app cannot reach the laptop, so nothing is known. */
    UNKNOWN,

    /** The laptop is reachable but adb sees no phone. */
    NO_DEVICE,

    /** Attached, waiting for the user to tap Allow on the phone. */
    UNAUTHORIZED,

    /** Attached but not answering (cable or debugging toggled). */
    OFFLINE,

    /** Ready to sync. */
    READY,
}

/** Reduces the ping to one [DeviceReadiness]. [laptopReachable] is false when the app cannot reach the daemon. */
fun deviceReadiness(laptopReachable: Boolean, devices: List<DeviceStateDto>): DeviceReadiness = when {
    !laptopReachable -> DeviceReadiness.UNKNOWN
    devices.any { it.state == "device" } -> DeviceReadiness.READY
    devices.any { it.state == "unauthorized" } -> DeviceReadiness.UNAUTHORIZED
    devices.any { it.state == "offline" } -> DeviceReadiness.OFFLINE
    else -> DeviceReadiness.NO_DEVICE
}

/**
 * The cause to name first when adb sees no phone but the phone app reaches the laptop over [via] (Phase V3), or null
 * when there is nothing to add (no phone app connected, or the app reached the laptop through the cable).
 */
fun phoneProblemLead(via: Preset, agentConnected: Boolean): String? {
    if (!agentConnected) return null
    val route = when (via) {
        Preset.TAILSCALE -> "over Tailscale"
        Preset.LAN -> "over the local network"
        else -> return null
    }
    return "You are connected $route. Pairs that use USB or Wi-Fi debugging need a cable or Wi-Fi debugging."
}

/**
 * Builds the steps in order. [developerOptionsOn] and [usbDebuggingOn] come from `Settings.Global`;
 * [allFilesAccess] is only used when [isFull] (the build that has the file agent, task 37g).
 */
fun computeChecklist(
    developerOptionsOn: Boolean,
    usbDebuggingOn: Boolean,
    readiness: DeviceReadiness,
    isFull: Boolean = false,
    allFilesAccess: Boolean = false,
): List<ChecklistStep> {
    val steps = mutableListOf<ChecklistStep>()

    steps += if (developerOptionsOn) {
        ChecklistStep(StepId.DEVELOPER_OPTIONS, "Developer options are on", "", StepStatus.DONE)
    } else {
        ChecklistStep(
            StepId.DEVELOPER_OPTIONS,
            "Turn on Developer options",
            "Open About phone and tap \"Build number\" seven times (on Samsung it is under Software information).",
            StepStatus.TODO,
            StepAction.OPEN_ABOUT_PHONE,
            "Open About phone",
        )
    }

    steps += when {
        usbDebuggingOn -> ChecklistStep(StepId.USB_DEBUGGING, "USB debugging is on", "", StepStatus.DONE)
        !developerOptionsOn -> ChecklistStep(
            StepId.USB_DEBUGGING,
            "Switch on USB debugging",
            "Turn on Developer options first. Then the USB debugging switch appears in them.",
            StepStatus.WAITING,
        )
        else -> ChecklistStep(
            StepId.USB_DEBUGGING,
            "Switch on USB debugging",
            "In Developer options, scroll to the Debugging section and switch on \"USB debugging\". Come back here afterwards.",
            StepStatus.TODO,
            StepAction.OPEN_DEVELOPER_OPTIONS,
            "Open Developer options",
        )
    }

    steps += when (readiness) {
        DeviceReadiness.READY -> ChecklistStep(StepId.COMPUTER_ALLOWED, "This computer is allowed", "", StepStatus.DONE)
        DeviceReadiness.UNAUTHORIZED -> ChecklistStep(
            StepId.COMPUTER_ALLOWED,
            "Allow this computer",
            "Look at the phone screen for \"Allow USB debugging?\", tick \"Always allow from this computer\" and tap Allow.",
            StepStatus.TODO,
        )
        DeviceReadiness.OFFLINE -> ChecklistStep(
            StepId.COMPUTER_ALLOWED,
            "Reconnect the cable",
            "The laptop sees the phone but it does not answer. Unplug the cable and plug it in again.",
            StepStatus.TODO,
        )
        DeviceReadiness.NO_DEVICE -> ChecklistStep(
            StepId.COMPUTER_ALLOWED,
            "Plug the phone into the laptop",
            "The laptop does not see a phone. Check the cable, and that the cable can carry data.",
            StepStatus.TODO,
        )
        DeviceReadiness.UNKNOWN -> ChecklistStep(
            StepId.COMPUTER_ALLOWED,
            "Allow this computer",
            "The app cannot reach the laptop yet, so it cannot tell. Plug the cable in and wait a few seconds.",
            StepStatus.WAITING,
        )
    }

    if (usbDebuggingOn) {
        steps += ChecklistStep(
            StepId.STAYS_ON,
            "Keep it on",
            "Some phones switch USB debugging off after a restart or after a while. If syncing stops, come back here. " +
                "In Developer options look for \"Disable adb authorization timeout\".",
            StepStatus.INFO,
            StepAction.OPEN_DEVELOPER_OPTIONS,
            "Open Developer options",
        )
    }

    if (isFull) {
        steps += if (allFilesAccess) {
            ChecklistStep(StepId.ALL_FILES, "All files access is allowed", "", StepStatus.DONE)
        } else {
            ChecklistStep(
                StepId.ALL_FILES,
                "Allow access to all files",
                "This build reads and writes your folders itself, so Android needs this permission.",
                StepStatus.TODO,
                StepAction.OPEN_ALL_FILES_ACCESS,
                "Allow all files access",
            )
        }
    }

    return steps
}

/** The first step the user still has to do, or null when everything is done. */
fun firstOpenStep(steps: List<ChecklistStep>): ChecklistStep? =
    steps.firstOrNull { it.status == StepStatus.TODO } ?: steps.firstOrNull { it.status == StepStatus.WAITING }

/** True when the phone is ready to sync: every step that can be checked is done. */
fun isFullySetUp(steps: List<ChecklistStep>): Boolean =
    steps.none { it.status == StepStatus.TODO || it.status == StepStatus.WAITING }
