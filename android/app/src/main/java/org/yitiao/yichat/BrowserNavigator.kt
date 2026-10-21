package org.yitiao.yichat

import android.app.Activity
import android.content.Intent
import android.net.Uri
import androidx.browser.customtabs.CustomTabsClient
import androidx.browser.customtabs.CustomTabsIntent
import androidx.browser.customtabs.CustomTabsService
import androidx.browser.auth.AuthTabIntent
import androidx.activity.result.ActivityResultLauncher
import java.net.URI

/** Browser-owned UI and cookies. No embedded WebView or session extraction. */
object BrowserUrls {
    fun allowed(value: String): Boolean = runCatching {
        val uri = URI(value)
        uri.scheme == "https" && uri.rawUserInfo == null && uri.port == -1 &&
            uri.host in setOf("chat.yitiao.org", "yitiao.org", "yidt.yitiao.org")
    }.getOrDefault(false)
}

object BrowserNavigator {
    fun open(activity: Activity, uri: Uri, authResult: ActivityResultLauncher<Intent>) {
        val isolated = BuildConfig.DEBUG && BuildConfig.GATEWAY == "http://127.0.0.1:18018" &&
            uri.scheme == "http" && uri.host == "127.0.0.1" && uri.port == 18018 && uri.userInfo == null
        require(BrowserUrls.allowed(uri.toString()) || isolated) { "拒绝非站内 HTTPS 浏览器链接" }
        // Keep a compatible default browser first; fall back to another actual
        // installed provider when the default browser has no Custom Tabs service.
        val providers = activity.packageManager.queryIntentServices(
            Intent(CustomTabsService.ACTION_CUSTOM_TABS_CONNECTION), 0
        ).map { it.serviceInfo.packageName }.distinct().sorted()
        val provider = checkNotNull(CustomTabsClient.getPackageName(activity, providers)) {
            "没有支持授权窗口的系统浏览器"
        }
        if (uri.scheme == "https" && uri.host == "chat.yitiao.org" && uri.path == "/auth/native/login") {
            // Auth Tab intercepts the exact HTTPS callback and returns it directly,
            // even when Android's ordinary "open supported links" setting is off.
            // Older providers fall back to Custom Tabs and the Activity intent.
            val auth = AuthTabIntent.Builder().build()
            auth.intent.setPackage(provider)
            auth.launch(authResult, uri, "chat.yitiao.org", "/app/callback")
            return
        }
        val tab = CustomTabsIntent.Builder()
            .setShowTitle(true)
            .setShareState(CustomTabsIntent.SHARE_STATE_OFF)
            .setInstantAppsEnabled(false)
            .setSendToExternalDefaultHandlerEnabled(true)
            .build()
        tab.intent.setPackage(provider)
        tab.launchUrl(activity, uri)
    }
    fun external(activity: Activity, uri: Uri) {
        require(WebRoutes.external(uri.toString())) { "不支持此链接" }
        val providers = activity.packageManager.queryIntentServices(Intent(CustomTabsService.ACTION_CUSTOM_TABS_CONNECTION),0)
            .map { it.serviceInfo.packageName }.distinct()
        val provider = CustomTabsClient.getPackageName(activity,providers)
        if(provider != null) {
            val tab=CustomTabsIntent.Builder().setShowTitle(true).setShareState(CustomTabsIntent.SHARE_STATE_OFF)
                .setInstantAppsEnabled(false).setSendToExternalDefaultHandlerEnabled(true).build()
            tab.intent.setPackage(provider);tab.launchUrl(activity,uri)
        } else activity.startActivity(Intent(Intent.ACTION_VIEW,uri).addCategory(Intent.CATEGORY_BROWSABLE))
    }
}
