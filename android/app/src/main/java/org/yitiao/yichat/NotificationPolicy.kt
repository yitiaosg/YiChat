package org.yitiao.yichat

/** No Android dependencies: the same rules govern FCM and the polling fallback. */
data class PushHint(val room:String,val kind:String,val event:String) {
    companion object {
        fun parse(data:Map<String,String>):PushHint? {
            if(data.keys!=setOf("conversation_id","conversation_type","message_id"))return null
            val room=data["conversation_id"].orEmpty();val event=data["message_id"].orEmpty()
            val kind=data["conversation_type"].orEmpty()
            return if(room.startsWith("!")&&room.length in 2..255&&event.startsWith("$")&&event.length in 2..255&&kind in setOf("private","group"))PushHint(room,kind,event) else null
        }
    }
    val notificationId get()=room.hashCode()
    // Tag + ID avoids Java hash collisions between different conversations.
    val tag get()="conversation:$room"
}
data class NoticeMessage(val id:String,val sender:String,val body:String,val time:Long)
data class NoticeState(val hint:PushHint,val generation:String,val messages:List<NoticeMessage> = emptyList())
object NotificationPolicy {
    const val TITLE="Yichat"
    const val PLACEHOLDER="你有新的消息"
    fun suppress(foreground:Boolean,activeRoom:String,room:String)=foreground&&activeRoom==room
    fun title(cached:String?)=cached?.take(80)?.takeIf{it.isNotBlank()}?:TITLE
    fun replace(state:NoticeState,generation:String,messages:List<NoticeMessage>?):NoticeState {
        if(generation!=state.generation||messages.isNullOrEmpty())return state
        return state.copy(messages=(state.messages+messages).distinctBy{it.id}.sortedBy{it.time}.takeLast(20))
    }
}
