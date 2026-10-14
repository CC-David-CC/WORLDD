# Host desktop settings

## Keep displays awake

Applied on 2026-10-07 to the `dflanag3` desktop account on `sb16` and `sb16-2`.
The user requested disabled lock screens and display timeouts.

Both hosts use an X11 GNOME session on display `:1`.
The settings below persist in the user's GNOME configuration.

| Schema | Key | Value |
| --- | --- | --- |
| `org.gnome.desktop.session` | `idle-delay` | `uint32 0` |
| `org.gnome.desktop.screensaver` | `lock-enabled` | `false` |
| `org.gnome.desktop.screensaver` | `idle-activation-enabled` | `false` |
| `org.gnome.desktop.screensaver` | `ubuntu-lock-on-suspend` | `false` |
| `org.gnome.desktop.lockdown` | `disable-lock-screen` | `true` |
| `org.gnome.settings-daemon.plugins.power` | `idle-dim` | `false` |
| `org.gnome.settings-daemon.plugins.power` | `sleep-inactive-ac-type` | `'nothing'` |
| `org.gnome.settings-daemon.plugins.power` | `sleep-inactive-battery-type` | `'nothing'` |
| `org.gnome.settings-daemon.plugins.power` | `sleep-inactive-ac-timeout` | `0` |
| `org.gnome.settings-daemon.plugins.power` | `sleep-inactive-battery-timeout` | `0` |

Each host also has these user files:

- `~/.local/bin/spacial-desktop-display-awake`
- `~/.config/autostart/spacial-desktop-display-awake.desktop`

The startup script runs `xset s off`, `xset s noblank`, and `xset -dpms` in X11 sessions.
These commands were also applied to the current sessions.
Both existing sessions were unlocked. Both displays received a wake command.

Verification on both hosts showed:

- GNOME idle delay: zero.
- Screen saver timeout and cycle: zero.
- DPMS: disabled.
- Session `LockedHint`: `no`.
- GNOME ScreenSaver `GetActive`: `false`.

The persistent settings and startup files were checked without rebooting either host.
Normal login and SSH access remain available.

## Restore previous settings

Each host has a private backup with `before.json`, `after.json`, and `restore.py`.
The restore script restores previous GNOME values, X11 settings, and startup file contents.
It does not lock the current session or force its display off.

Run from a machine with SSH access while the desktop account is logged in:

```powershell
ssh dflanag3@sb16 python3 /home/dflanag3/.local/state/spacial-desktop/display-awake/20261007T083758Z/restore.py
ssh dflanag3@sb16-2 python3 /home/dflanag3/.local/state/spacial-desktop/display-awake/20261007T083800Z/restore.py
```
