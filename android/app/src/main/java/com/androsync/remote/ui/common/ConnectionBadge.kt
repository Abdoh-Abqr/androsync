package com.androsync.remote.ui.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

private val ConnectedDotColor = Color(0xFF2E7D32)
private val UnreachableDotColor = Color(0xFFC62828)

/**
 * Small "dot + label" badge shown in every screen's top app bar (PLAN.md
 * task 18): a green dot and "USB"/"Tailscale"/"LAN" when [state] is
 * [ConnectionState.Connected], a red dot and "Laptop not reachable"
 * otherwise.
 */
@Composable
fun ConnectionBadge(state: ConnectionState, modifier: Modifier = Modifier) {
    val dotColor = if (state is ConnectionState.Connected) ConnectedDotColor else UnreachableDotColor
    Row(
        modifier = modifier.padding(horizontal = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(8.dp)
                .background(color = dotColor, shape = CircleShape),
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            text = connectionBadgeLabel(state),
            style = MaterialTheme.typography.bodySmall,
            maxLines = 1,
            softWrap = false,
        )
    }
}
