import groovy.json.JsonSlurper
plugins {
 alias(libs.plugins.android.application)
 alias(libs.plugins.kotlin.android)
 alias(libs.plugins.kotlin.compose)
 alias(libs.plugins.kotlin.serialization)
 alias(libs.plugins.ksp)
}
android {
 namespace = "org.yitiao.yichat"
 compileSdk = 36
 buildToolsVersion = "36.0.0"
 defaultConfig {
  applicationId = "org.yitiao.yichat"
  minSdk = 26
  targetSdk = 36
  versionCode = providers.gradleProperty("yichatVersionCode").orElse("2").get().toInt()
  versionName = "0.1.0"
  testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
  ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
  buildConfigField("String", "GATEWAY", "\"https://chat.yitiao.org\"")
 }
 buildFeatures { compose = true; buildConfig = true }
 splits { abi { isEnable = true; reset(); include("arm64-v8a", "x86_64"); isUniversalApk = true } }
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
 implementation(platform(libs.compose.bom))
 implementation(libs.activity); implementation(libs.material3); implementation(libs.ui); implementation(libs.ui.tooling.preview)
 implementation(libs.lifecycle); implementation(libs.viewmodel); implementation(libs.navigation)
 implementation(libs.room.runtime); implementation(libs.room.ktx); ksp(libs.room.compiler)
 implementation(libs.work); implementation(libs.serialization); implementation(libs.okhttp); implementation(libs.coroutines)
 implementation(libs.matrix); implementation(libs.bc); implementation(libs.webkit)
 implementation("com.caverock:androidsvg-aar:1.4")
 implementation("androidx.media3:media3-exoplayer:1.7.1")
 implementation("androidx.media3:media3-ui:1.7.1")
 testImplementation(libs.junit)
 testImplementation(libs.mockwebserver)
 androidTestImplementation("androidx.test:runner:1.6.2")
 androidTestImplementation("androidx.test.ext:junit:1.2.1")
 androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
}
dependencyLocking { lockAllConfigurations() }
