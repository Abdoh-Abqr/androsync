#!/usr/bin/env bash
# Task 37g (issue #12): the play APK must not ask for "All files access", the full APK must.
# Usage: scripts/check_flavor_permissions.sh   (after ./gradlew :app:assembleDebug in android/)
set -euo pipefail
cd "$(dirname "$0")/.."

sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"
aapt2="$(ls "$sdk"/build-tools/*/aapt2 2>/dev/null | sort -V | tail -1 || true)"
[ -x "$aapt2" ] || { echo "aapt2 not found under $sdk/build-tools" >&2; exit 2; }

perm=android.permission.MANAGE_EXTERNAL_STORAGE
fail=0
for flavor in play full; do
  apk="android/app/build/outputs/apk/$flavor/debug/app-$flavor-debug.apk"
  [ -f "$apk" ] || { echo "missing $apk (run ./gradlew :app:assembleDebug in android/)" >&2; exit 2; }
  if "$aapt2" dump permissions "$apk" | grep -q "$perm"; then has=yes; else has=no; fi
  want=$([ "$flavor" = full ] && echo yes || echo no)
  if [ "$has" = "$want" ]; then
    echo "ok: $flavor has $perm: $has"
  else
    echo "FAIL: $flavor has $perm: $has, expected $want" >&2
    fail=1
  fi
done
exit $fail
