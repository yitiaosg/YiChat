import groovy.json.JsonSlurper
plugins {
 alias(libs.plugins.android.application)
 alias(libs.plugins.kotlin.android)
 alias(libs.plugins.kotlin.serialization)
 alias(libs.plugins.ksp)
}
android {
 namespace = "org.yitiao.yichat"
 compileSdk = 36
 buildToolsVersion = "36.0.0"
 defaultConfig {
  applicationId = "org.yitiao.yichat.mobile"
  minSdk = 26
  targetSdk = 36
  versionCode = providers.gradleProperty("yichatVersionCode").orElse("12").get().toInt()
  versionName = "0.3.4"
  testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
  buildConfigField("String", "GATEWAY", "\"https://chat.yitiao.org\"")
 }
 // FirebaseInitProvider reads standard resources from external configuration.
 val firebasePath = providers.environmentVariable("YICHAT_FIREBASE_CONFIG").orNull
 if (firebasePath != null) {
  val firebase = JsonSlurper().parse(file(firebasePath)) as Map<*, *>
  val project = firebase["project_info"] as Map<*, *>
  val client = (firebase["client"] as List<*>).map { it as Map<*, *> }.single {
   ((it["client_info"] as Map<*, *>)["android_client_info"] as Map<*, *>)["package_name"] == "org.yitiao.yichat.mobile"
  }
  defaultConfig {
   resValue("string", "google_app_id", (client["client_info"] as Map<*, *>)["mobilesdk_app_id"] as String)
   resValue("string", "gcm_defaultSenderId", project["project_number"] as String)
   resValue("string", "project_id", project["project_id"] as String)
   resValue("string", "google_api_key", ((client["api_key"] as List<*>).first() as Map<*, *>)["current_key"] as String)
  }
 }
 buildFeatures { buildConfig = true }
 compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
 kotlinOptions { jvmTarget = "17" }
 packaging { jniLibs.useLegacyPackaging = false; resources.excludes += setOf("META-INF/AL2.0", "META-INF/LGPL2.1", "META-INF/versions/9/OSGI-INF/MANIFEST.MF") }
 val privatePath = providers.environmentVariable("YICHAT_SIGNING_CONFIG").orNull
 if (privatePath != null) {
  val cfg = JsonSlurper().parse(file(privatePath)) as Map<*, *>
  signingConfigs.create("dedicated") {
   storeFile = file(cfg["storeFile"] as String); storeType = cfg["storeType"] as String
   storePassword = cfg["storePassword"] as String; keyAlias = cfg["keyAlias"] as String; keyPassword = cfg["keyPassword"] as String
   enableV1Signing = false; enableV2Signing = true; enableV3Signing = true; enableV4Signing = false
  }
  buildTypes.getByName("release") { signingConfig = signingConfigs.getByName("dedicated") }
  buildTypes.getByName("debug") { signingConfig = signingConfigs.getByName("dedicated") }
 }
 buildTypes {
  release { isMinifyEnabled = false }
  debug {
   if (providers.gradleProperty("yichatIsolation").orNull == "true")
    buildConfigField("String", "GATEWAY", "\"http://127.0.0.1:18018\"")
  }
 }
 testOptions { unitTests.isReturnDefaultValues = true }
}
ksp { arg("room.schemaLocation", "$projectDir/schemas") }
dependencies {
 implementation("androidx.activity:activity-ktx:1.10.1")
 implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.1")
 // Play services pulls an old Fragment transitively; align with Activity Result APIs.
 implementation("androidx.fragment:fragment:1.8.9")
 implementation("com.google.firebase:firebase-messaging:25.0.1")
 implementation("com.google.firebase:firebase-messaging-directboot:25.0.1")
 implementation(libs.room.runtime); implementation(libs.room.ktx); ksp(libs.room.compiler)
 implementation(libs.work); implementation(libs.serialization); implementation(libs.okhttp); implementation(libs.coroutines)
 implementation(libs.bc); implementation(libs.webkit); implementation(libs.browser)
 testImplementation(libs.junit)
 testImplementation(libs.mockwebserver)
 androidTestImplementation("androidx.test:runner:1.6.2")
 androidTestImplementation("androidx.test.ext:junit:1.2.1")
 androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
}
dependencyLocking { lockAllConfigurations() }

// Build the same source used by chat.yitiao.org, rather than a second frontend.
val webRoot = rootProject.file("../source/yichat/client")
val buildChatWeb by tasks.registering(Exec::class) {
 workingDir(webRoot);commandLine("npm","run","build")
 inputs.dir(webRoot.resolve("src"));inputs.dir(webRoot.resolve("public"));inputs.file(webRoot.resolve("package-lock.json"));inputs.file(webRoot.resolve("index.html"))
 inputs.file(webRoot.resolve("package.json"));inputs.file(webRoot.resolve("vite.config.ts"));inputs.dir(webRoot.resolve("scripts"))
 inputs.file(rootProject.file("../source/static/ycards-render.js"));inputs.file(rootProject.file("../source/static/parachute-scene.js"))
 outputs.dir(webRoot.resolve("dist"))
}
val bundleChatWeb by tasks.registering(Sync::class) {
 dependsOn(buildChatWeb);from(webRoot.resolve("dist"));into(layout.buildDirectory.dir("generated/webAssets/app-shell"))
}
android.sourceSets.getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/webAssets"))
tasks.named("preBuild") { dependsOn(bundleChatWeb) }
