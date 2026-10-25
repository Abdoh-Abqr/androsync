pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

plugins {
    // Lets Gradle auto-download a matching JDK for the toolchain declared in
    // app/build.gradle.kts (`kotlin { jvmToolchain(17) }`) when none of the
    // locally installed JVMs has a full JDK (javac) at that language level.
    id("org.gradle.toolchains.foojay-resolver-convention") version "0.9.0"
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "AndroSync Remote"
include(":app")
