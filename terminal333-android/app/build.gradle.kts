import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "com.lab333.terminal"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.lab333.terminal"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "2.0"
        // Default backend URL. Uses Tailscale MagicDNS short hostname so this
        // doesn't break when the Mac's tailnet IP changes. Requires the phone's
        // Tailscale app to have MagicDNS enabled (default in current versions).
        // User can still override at runtime in Settings.
        buildConfigField("String", "DEFAULT_BACKEND", "\"http://100.73.225.4:8080\"")
    }

    signingConfigs {
        create("release") {
            // Keystore + password live outside the source tree (~/lab333/secrets/).
            val props = Properties()
            val propsFile = file(System.getProperty("user.home") + "/lab333/secrets/keystore.properties")
            if (propsFile.exists()) propsFile.inputStream().use { props.load(it) }
            storeFile = file(System.getProperty("user.home") + "/lab333/secrets/terminal333.jks")
            storePassword = props.getProperty("storePassword", "")
            keyAlias = "terminal333"
            keyPassword = props.getProperty("keyPassword", "")
        }
    }

    buildTypes {
        debug { }
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
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
    packaging {
        resources {
            excludes += setOf("META-INF/AL2.0", "META-INF/LGPL2.1", "META-INF/*.kotlin_module")
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.navigation:navigation-compose:2.8.4")

    // Networking
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.jakewharton.retrofit:retrofit2-kotlinx-serialization-converter:1.0.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    // DataStore for settings
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // Pull-to-refresh
    implementation("androidx.compose.material:material:1.7.5")

    debugImplementation("androidx.compose.ui:ui-tooling")
}
