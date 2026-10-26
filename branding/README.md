# Logo

**Put your logo here: `branding/logo.svg`.**

Requirements for the file:
- A **square** SVG with a `viewBox` (108 x 108 is ideal), for example `viewBox="0 0 108 108"`.
- A **transparent background**. The colour behind it is given when you apply it.
- Keep the artwork inside the **central 66 x 66 area** (from 21 to 87 on a 108 canvas). Android crops an adaptive icon
  to a circle, a squircle or a rounded square, and anything outside would be cut off.
- Simple shapes read best at 16 px. One or two colours is plenty.

Then run, from the repository folder:

```sh
python3 branding/apply_logo.py --background "#283593"   # the colour behind the logo
```

It writes the Android icon (adaptive and themed), the desktop launcher icon, the F-Droid store icon and
`branding/logo-512.png`. Rebuild the app and look at it on the phone. `python3 branding/apply_logo.py --check` only
verifies the file. Needs ImageMagick (`sudo apt install imagemagick`).

The three candidates I drew are `mark-a.svg`, `mark-b.svg`, `mark-c.svg` (full icons with a background; made by
`make_marks.py`). They are only examples and are not used anywhere yet.
