import java.util.Properties
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.quarki3.terminal"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.lab333.terminal"
        minSdk = 26
        targetSdk = 35
        versionCode = 19
        versionName = "3.13"
        val lp = Properties()
        val lpf = rootProject.file("local.properties")
        if (lpf.exists()) lpf.inputStream().use { lp.load(it) }
        buildConfigField("String", "DEFAULT_TERMINAL_URL",
            "\"https://quarki-stack.tail33de20.ts.net/terminal/?key=${lp.getProperty("ownerApiKey", "")}\"")
        // Embedded Claude terminal (Robert 2026-08-20): tailnet ttyd URL +
        // basic-auth, auto-supplied by the app for that host only.
        buildConfigField("String", "TERMINAL_URL",
            "\"${lp.getProperty("terminalUrl", "")}\"")
        buildConfigField("String", "TERMINAL_AUTH",
            "\"${lp.getProperty("terminalAuth", "")}\"")

    }
    signingConfigs {
        create("release") {
            val props = Properties()
            val pf = file(System.getProperty("user.home") + "/lab333/secrets/keystore.properties")
            if (pf.exists()) pf.inputStream().use { props.load(it) }
            storeFile = file(System.getProperty("user.home") + "/lab333/secrets/terminal333.jks")
            storePassword = props.getProperty("storePassword", "")
            keyAlias = "terminal333"
            keyPassword = props.getProperty("keyPassword", "")
        }
    }


    buildTypes {
        debug { }
        release {
            signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    buildFeatures { buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.biometric:biometric:1.1.0")
}
