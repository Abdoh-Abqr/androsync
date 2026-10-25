# Getting AndroSync into F-Droid

Status: the app is ready for F-Droid. The merge request to F-Droid is **not sent yet**; it needs the public repository
and a GitLab account (see "Steps").

## What was done to make it acceptable

- **Only free dependencies.** The QR scanner is ZXing (Apache-2.0), not Google's ML Kit that needs Play Services.
  `scripts/check_foss_deps.sh` fails CI if a Google, Firebase, analytics or billing library is added.
  The full app has no ads, no tracking and no network use except to your own laptop.
- **Which build.** F-Droid builds the `full` flavor: it contains agent mode, which needs "All files access". F-Droid has
  no objection to that permission; Google Play does (see [agent-mode.md](agent-mode.md)).
- **Reproducible.** No `local.properties` or signing key is needed to build; the encrypted dependency list is left out of
  the APK (`dependenciesInfo`).
- **Store texts.** `fastlane/metadata/android/en-US/`: title, short and full description, icon, and a changelog per
  version code (`changelogs/2.txt` for 0.2.0).
- **Donate link and license** are in the recipe: [fdroid/com.androsync.remote.yml](fdroid/com.androsync.remote.yml).

## Steps (you, about 30 minutes, then 1 to 3 weeks of waiting)

1. Make a release tag in the public repository: `python3 scripts/bump_version.py <version>`, add a `CHANGELOG.md`
   section, commit, tag `v<version>`, push the tag. The recipe's `commit:` must be that tag, and `versionCode` must match.
2. Create a GitLab account and fork <https://gitlab.com/fdroid/fdroiddata>.
3. Copy `docs/fdroid/com.androsync.remote.yml` to `metadata/com.androsync.remote.yml` in your fork.
4. Test it with the F-Droid build tool (`fdroid build -v -l com.androsync.remote`, see the fdroiddata README for the
   Docker image) and `fdroid lint com.androsync.remote`.
5. Open the merge request. F-Droid volunteers review it; answer their questions in the request.

After that, every new tag is picked up by itself (`UpdateCheckMode: Tags`).

## Not done

- Phone screenshots for the store page (`fastlane/metadata/android/en-US/images/phoneScreenshots/`): taking them shows
  your own folders and pairs, so they are best made on a test setup. The listing works without them.
