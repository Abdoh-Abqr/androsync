# The token

The token is a long random secret (43 characters) that proves a caller is allowed to control the laptop service. The
phone app, the desktop window and `curl` all send it; anything without it gets `401 unauthorized`, even on the laptop
itself.

## Where it lives

| Place | What is there |
|---|---|
| Laptop, `~/.config/androsync/config.yaml` | `service: token: ...`. Made by `androsync service install` (kept if one exists). The file is readable by you only (mode 600). |
| The running service | Holds it in memory and checks every request (`Authorization: Bearer <token>`, compared in constant time). |
| The QR code | `androsync qr` and the window's **Show QR** put the token, the laptop's name and its addresses into the code. |
| The phone app | Kept per laptop in the app's private settings. Nothing else on the phone can read it. |

It is plain HTTP over your own network, so the token is the protection: do not paste it into chats, issues or logs.
The "Report a problem" bundle removes it.

## A new token

Do this when a phone is lost, you shared the token by accident, or you want to lock out other phones.

- **From the app:** Settings, **Generate a new token**. The laptop makes a new one, saves it in `config.yaml`, starts
  using it at once and hands it to the app, which saves it.
- **From the window:** **Show QR**, **Make a new token...**.
- **From a terminal:** `androsync service new-token`, then `androsync service restart`. The token is not printed.

After a new token every other phone or tool that still has the old one is locked out until it scans the QR code again.
If you edit `config.yaml` by hand, restart the service; it reads the file only when it starts.
