package com.androsync.remote.ui.pairs

import com.androsync.remote.data.api.DeviceStateDto
import com.androsync.remote.data.api.PairDto

const val ANY_PHONE = "any"

/** One phone a pair can be bound to, as the editor and the filter show it. */
data class PhoneChoice(val id: String, val label: String)

/**
 * The phones to offer: every ready phone the laptop sees, plus [alsoKeep] (the phone a pair is already bound to, even if
 * it is not attached right now, so editing such a pair never silently rebinds it). Named phones show their name.
 */
fun phoneChoices(devices: List<DeviceStateDto>, alsoKeep: List<String> = emptyList()): List<PhoneChoice> {
    val byId = linkedMapOf<String, PhoneChoice>()
    for (d in devices) {
        if (d.state != "device") continue
        val id = d.id ?: d.serial
        byId.putIfAbsent(id, PhoneChoice(id, d.name ?: id))
    }
    for (id in alsoKeep) {
        if (id == ANY_PHONE || id.isBlank() || id in byId) continue
        val known = devices.firstOrNull { (it.id ?: it.serial) == id }
        byId[id] = PhoneChoice(id, known?.name ?: "$id (not connected)")
    }
    return byId.values.toList()
}

/** The name to show on a pair card for the phone it is bound to, or null for "any". */
fun phoneLabel(deviceId: String, devices: List<DeviceStateDto>): String? {
    if (deviceId == ANY_PHONE || deviceId.isBlank()) return null
    val known = devices.firstOrNull { (it.id ?: it.serial) == deviceId }
    return known?.name ?: deviceId
}

/** The pairs to list for [selected] (a phone id), or all of them for null: that phone's pairs and the "any" ones. */
fun filterPairsForPhone(pairs: List<PairDto>, selected: String?): List<PairDto> =
    if (selected == null) pairs else pairs.filter { it.device == ANY_PHONE || it.device == selected }
