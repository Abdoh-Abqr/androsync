#!/usr/bin/env bash
# Task 35d (issue #24): F-Droid only accepts apps built from free software. Fail if a proprietary Google or tracking
# library creeps into the Android build files. Run from anywhere; CI runs it on every push.
set -euo pipefail
cd "$(dirname "$0")/.."

banned='com\.google\.android\.gms|com\.google\.firebase|com\.google\.mlkit|com\.crashlytics|io\.fabric|com\.google\.android\.play|com\.android\.billingclient|com\.facebook\.android|io\.sentry|com\.mixpanel|com\.amplitude|com\.appsflyer'

if grep -nE "$banned" android/gradle/libs.versions.toml android/app/build.gradle.kts android/build.gradle.kts android/settings.gradle.kts 2>/dev/null; then
  echo "check_foss_deps.sh: a proprietary dependency is declared above; F-Droid would refuse the app." >&2
  exit 1
fi
echo "check_foss_deps.sh: clean"
