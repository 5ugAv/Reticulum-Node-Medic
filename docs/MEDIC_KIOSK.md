# The medic as a kiosk — on-device settings outside this repo

Node Medic runs fullscreen over the Raspberry Pi OS desktop (labwc). Some
desktop pieces can draw OVER the app. This file records what has been
changed on a medic's own configuration, why, and how to undo it, because
none of it lives in the repo and a fresh card would not carry it.

## 2026-09-22 — the desktop panel (wf-panel-pi) is not started

**What was seen.** A grey rounded box reading "Reticulum Node Medic"
floated over the front page after a UI restart and vanished when tapped
(operator photo, 22:5x). Nothing in the app draws its own title; the medic
runs no notification service. The panel's window list shows a hover
tooltip with the focused window's title in exactly that style, and the
new window came up under wherever the pointer last rested.

**What was done.** labwc reads `~/.config/labwc/autostart` INSTEAD of
`/etc/xdg/labwc/autostart` when the user file exists. The medic account
got a copy of the system file with the `wf-panel-pi` line removed:

```
/usr/bin/lwrespawn /usr/bin/pcmanfm-pi &
/usr/bin/kanshi &
/usr/bin/lxsession-xdg-autostart
# Node Medic kiosk (2026-09-22): wf-panel-pi removed — …
```

The running panel and its respawner were stopped by hand once; from the
next login it is simply not started. No package removed, nothing
system-wide edited.

**What it costs.** With the app stopped, the desktop shows wallpaper and
no menu bar — recovery is over SSH. The panel's own tray bubbles
(Wi-Fi, Bluetooth, volume, the under-voltage warning, USB eject) are
gone; all of them drew under the fullscreen app anyway. Connectivity is
unaffected (NetworkManager, rnsd, the reporter and SSH do not involve
the panel). About 55 MB of RAM is freed.

**Undo.** `rm ~/.config/labwc/autostart` and log the session in again
(or reboot).

**Not yet proven.** That the tooltip was the panel's is reasoned from the
desktop's behaviour, not observed. If the box appears again after a
restart with the panel gone, the theory was wrong — look elsewhere.

**Shell gotcha (again).** Stopping the panel over ssh with
`pkill -f "lwrespawn /usr/bin/wf-panel-pi"` killed the ssh shell itself,
because the pattern matched the caller's own command line. Use `pkill -x
wf-panel-pi` (exact process name) or bracket patterns (`wf-pane[l]`).
