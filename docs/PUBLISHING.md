# Publishing: who is named where

The maker is shown as **Abdoh-Abqr** everywhere: README, the About screens, the license line, `pyproject.toml`, and the
F-Droid recipe (`AuthorName`). That is a name, not a legal identity, and it is enough for GitHub and F-Droid.

## Before Google Play or the App Store: decide on a legal entity

The stores do not accept an alias as the public name:

- **Google Play, personal account:** Google shows your *legal name*, country and developer email on the store page, and your
  full address if the app earns money. The developer name can differ, but the legal name is still displayed.
  Source: <https://support.google.com/googleplay/android-developer/answer/13628312>
- **App Store, individual enrollment:** your personal legal name is the seller shown on the App Store. Only a registered
  company (a legal entity enrolled as an organization, with a D-U-N-S number) shows a company name. Nicknames and trade names
  are not accepted. Source: <https://developer.apple.com/support/enrollment/>

So: if you want a name other than your own on those two stores, register a company first. GitHub Releases, F-Droid and the
project's own pages need nothing of the kind. Until then the `play` build stays unpublished (public issue about Google Play),
and there is no iOS build.

## Checklist when a store is chosen

1. Decide: personal account (legal name shown) or a registered organization.
2. Use a project email alias for the developer contact, never a work address.
3. Check the privacy policy text ("nothing leaves your devices") and the data-safety answers in `docs/`.
4. Keep `scripts/check_no_personal_data.sh` clean; use the project name in all store texts.
