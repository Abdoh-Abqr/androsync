# Several phones and several laptops

## Two phones on one laptop

- **Each phone has its own history for every pair.** A pair is remembered per phone (by the phone's hardware serial,
  which is the same over USB and over Wi-Fi). Two phones can therefore use pairs with the same name, and what one phone
  does not have can never be taken as "deleted" on the other.
- **Upgrading from an older version:** the history you already have is given to the first phone that syncs after the
  update. A second phone starts empty, so its first sync only adds files and deletes nothing. Sync your usual phone once
  before you plug in the second.
- **Two phones plugged in at once, no phone chosen:** the sync stops with a message and changes nothing. Unplug one, or
  bind the pair to its phone.
- **Bind a pair to a phone:** in the app, open the pair and choose the phone under "Phone"; or set `device: <id>` on the
  pair in the config. Find the ids with `androsync devices` (the ID column). A pair bound to a phone only syncs with
  that phone; "Any phone" is the default.
- **Name your computer:** set `service.name` in the config (default: the host name), for example `name: Home server`. The app and window show it in labels like "Home server ↔ S24 FE".
- **Name your phones:** `androsync phone set <id> --name "S24 FE"`. Add `--wifi 100.x.y.z:5555` and the laptop keeps
  trying to reconnect that phone over Wi-Fi or Tailscale. `androsync phone list` shows them.
- **Undo** only works with the phone that made the sync.
- **Agent mode** has no phone identity yet, so agent pairs keep one history per pair name.

## Two phones sharing one laptop and its folders

Both phones can have the app and control the same laptop: scan the same QR code on each. A sync still runs one at a
time, and either phone sees it running and can stop it. Each phone keeps its own history, so a file one phone deletes
goes to the laptop on that phone's next sync and reaches the other phone on its next sync. Both phones syncing the same
laptop folder is fine; keep an eye on conflicts if both change the same file.

- **Agent mode: every phone's app is its own phone.** Each app has its own id and its own history per pair, so a second
  phone with an empty or different folder can never make the first phone's files look deleted. A pair set to "Any
  phone" needs exactly one app connected; with two connected it stops and names both, so bind the pair to one phone
  (the Phone row in the pair editor). An app is named in Settings ("This phone's name") and the laptop shows that name.
- **A new token** (Settings, "Generate a new token") locks out every other phone until it scans the QR code again.

## Two laptops on one phone

- In the app: **Settings → Laptops → Add another laptop**, and scan the QR code that the other laptop shows
  (`androsync qr` or **Show QR** in its window). The laptop's name comes with the code.
- Switch laptops with the chips at the top of the Pairs screen, or in Settings. The tile, the widget and the agent always
  use the laptop that is in use.
- Each laptop has its own token and its own pairs. Nothing is shared between laptops.
