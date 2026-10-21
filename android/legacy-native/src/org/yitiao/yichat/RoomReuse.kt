package org.yitiao.yichat

import kotlinx.serialization.json.*

internal fun reusableDirectRoom(rows: JsonArray, ownMxid: String, peerMxid: String): String? =
    rows.firstOrNull {
        val r = it.jsonObject
        r.s("kind") == "direct" && r.s("room").isNotBlank() &&
            setOf(r.s("owner"), r.s("peer")) == setOf(ownMxid, peerMxid)
    }?.jsonObject?.s("room")
