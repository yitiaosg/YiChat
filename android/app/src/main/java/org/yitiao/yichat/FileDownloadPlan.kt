package org.yitiao.yichat
import kotlinx.serialization.json.*
/** Only explicit photo messages are prefetched. Ordinary files retain descriptors. */
object FileDownloadPlan {
 fun ids(value:JsonElement,imagesOnly:Boolean=false):Set<String> {
  val result=mutableSetOf<String>()
  fun visit(v:JsonElement){when(v){is JsonObject->{val id=Regex("mxc://chat\\.yitiao\\.org/([a-f0-9]{48})").matchEntire(v.s("url"))?.groupValues?.get(1);if(id!=null&&(!imagesOnly||v.s("msgtype")=="m.image"))result.add(id);v.values.forEach(::visit)};is JsonArray->v.forEach(::visit);else->{}}}
  visit(value);return result
 }
}
