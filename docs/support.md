# Support

## Try this first

| What you see | What to do |
|---|---|
| The app says "Laptop not reachable" | Is the laptop service running (window says "Running")? Is Tailscale on in both devices? Settings, **Test connection**. See [token.md](token.md) if you changed the token. |
| "unauthorized" or "no device" | USB debugging: Settings, **Connect your phone** walks you through it. |
| "a sync is already running" | Open the sync that is running instead of starting a new one. |
| A sync would delete a lot | It stops and asks. Read the list, then Go ahead or Not now. |
| Something went wrong in a sync | **Undo** the last sync from the app or `androsync undo <pair>`. |

More: the [README](../README.md) troubleshooting section, [agent-mode.md](agent-mode.md), [several-devices.md](several-devices.md).

## Report a problem

1. **Make the log file.**
   - Desktop window: **Help, Report a problem..., Save the log file**.
   - Terminal: `androsync support-bundle` (writes a zip in your home folder).
   - Phone app: **Settings, Help and support, Report a problem** (Share).
2. **Read it.** It holds versions, the service state, the config and the last log lines. The token, IP addresses, phone
   serials, your user and host name, and (unless you choose otherwise) pair names and folders are replaced by
   placeholders. Nothing is sent anywhere; you decide what to share.
3. **Open the form** and attach the file: <https://github.com/Abdoh-Abqr/androsync/issues/new?template=bug_report.yml>
   (it is also the button in the window and the app). For something private, use the email address in the app's
   Help and support screen, if one is listed.

## What is not in the log file

Your files, their names (when hidden), the token, passwords, and anything from the phone's storage. If you spot
something personal anyway, remove it before sharing and tell us so the redaction can be improved.
