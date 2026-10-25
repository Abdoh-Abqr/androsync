import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Release signing (PLAN.md task 22 / README "Release build"): reads
// android/keystore.properties, which is gitignored and not present in a
// fresh checkout - assembleRelease still works unsigned when it's absent,
// so this never blocks `assembleDebug`/CI.
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) {
        keystorePropertiesFile.inputStream().use { load(it) }
    }
}

android {
    namespace = "com.androsync.remote"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.androsync.remote"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "0.2.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // Two builds from one code base (task 37g, issue #12). "play" is the remote control for the laptop service and has
    // no file permission, so it can go to Google Play. "full" is the same plus the all-files permission that the
    // planned on-phone agent needs (tasks 37a-37e); it is for GitHub Releases and F-Droid. Both use the same
    // applicationId, so a phone has one or the other; APKs signed with different keys cannot update each other.
    flavorDimensions += "distribution"
    productFlavors {
        create("play") {
            dimension = "distribution"
            buildConfigField("boolean", "IS_FULL", "false")
            buildConfigField("boolean", "HAS_AGENT", "false")
        }
        create("full") {
            dimension = "distribution"
            buildConfigField("boolean", "IS_FULL", "true")
            buildConfigField("boolean", "HAS_AGENT", "true")
        }
    }

    signingConfigs {
        if (keystorePropertiesFile.exists()) {
            create("release") {
                storeFile = file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (keystorePropertiesFile.exists()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    // F-Droid (task 35d): no encrypted dependency list inside the APK, so the build is reproducible and nothing opaque
    // ships in the file.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = false
        }
    }
}

// Neither the launcher JVM nor Android Studio's bundled JBR on this machine
// ships a JDK 17 with javac (the system openjdk-17/21 packages here are
// JRE-only). Declaring a toolchain lets Gradle auto-download a matching JDK
// (via the foojay resolver plugin in settings.gradle.kts) instead of failing
// the compile tasks for lack of a JAVA_COMPILER capability.
kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.lifecycle.runtime.compose)

    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)
    implementation(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization.converter)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    // QR scanning (task 18 "Scan QR"), task 35d: ZXing (Apache-2.0) instead of Google's ML Kit code scanner, which
    // needs proprietary Play Services and is refused by F-Droid. Asks for the camera permission itself when scanning.
    implementation(libs.zxing.embedded)
    implementation(libs.zxing.core)

    // Home-screen widget (task 21).
    implementation(libs.androidx.glance.appwidget)

    debugImplementation(libs.androidx.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.kotlinx.serialization.json)
}

// With product flavors Android Gradle Plugin has no plain `testDebugUnitTest` any more (only one per flavor).
// This keeps the command in the README and in CI working: it runs both flavors.
tasks.register("testDebugUnitTest") {
    group = "verification"
    description = "Runs the debug unit tests of the play and the full flavor."
    dependsOn("testPlayDebugUnitTest", "testFullDebugUnitTest")
}
