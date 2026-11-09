# Nightly unattended session

Turns the desktop on, logs it in, and leaves T3 Code plus LM Studio Bionic
running without a human at the keyboard. Powers it back off in the morning.

## Schedule

| Time | Where | Action |
|---|---|---|
| 21:58 | laptop | `desktop-nightly-wake.timer` → `desktop-ctl wake cachy --no-wait` |
| 22:00 | desktop | RTC alarm armed the previous morning fires (best effort) |
| 07:00 | desktop | `nightly-shutdown.timer` → guarded power-off |

Two independent wake paths exist on purpose: Wake-on-LAN only works while the
laptop is on the home LAN, and the RTC alarm only works if the BIOS honours it.
Either one is enough.

## Components

### Laptop (`mars-laptop`)

| File | Purpose |
|---|---|
| `~/.config/systemd/user/desktop-nightly-wake.timer` | 21:58 trigger |
| `~/.config/systemd/user/desktop-nightly-wake.service` | runs `desktop-ctl wake cachy --no-wait` |

`--no-wait` means "send the magic packet and return". The unit is a no-op when
the desktop is already reachable.

### Desktop (`mars-desktop-cachy`)

| File | Purpose |
|---|---|
| `~/.config/autostart/bionic.desktop` | starts the LM Studio Bionic UI at graphical login |
| `~/.local/bin/nightly-shutdown` | guarded power-off, `--dry-run` and `--force` |
| `~/.config/systemd/user/nightly-shutdown.timer` | 07:00 trigger |
| `~/.config/systemd/user/nightly-shutdown.service` | runs the script |
| `~/.config/systemd/user/nightly-shutdown.service.d/10-wake-time.conf` | loads the env file |
| `~/.config/nightly-session.env` | `NIGHTLY_WAKE_AT` knob |

Already in place before this was added, and reused rather than replaced:

- Plasma autologin for the desktop user (`/etc/plasmalogin.conf`)
- `t3code.service` (lingering user unit, owns `~/.t3`); its `apply-config.conf`
  drop-in runs `t3cfg-sync --profile desktop --no-pull --apply --components t3`
  as `ExecStartPre`, so T3 settings are applied before the server starts
- `linger=yes` for the desktop user

LM Studio Bionic is the only local model server (since 2026-11-08; see
[`local-inference.md`](local-inference.md)). The old `qwen-inference` and
`qwen-laptop-gateway` units are no longer running.

## Why the GUI autostarts but T3 does not

`~/.config/autostart/t3code.desktop` is deliberately `Hidden=true`. The
lingering `t3code.service` is the sole host for `~/.t3`; a second GUI host at
login would contend for the same store. T3 is therefore already "launched" at
boot as a service. When you sit down and open the AppImage, the service is stopped
so that only one host owns the store (seen on 2026-11-09 at 07:17).

## Shutdown guards

`nightly-shutdown` refuses to power off when any of these is true:

- a session reports `Remote=yes` (someone is SSH'd in)
- the `seat0` graphical session reports `IdleHint=no` (someone is at the desk)
- an `app-t3code-agents-*.slice` is live (an agent is mid-run)

Bypass with `nightly-shutdown --force`.

## Changing the times

Wake time:

```bash
# desktop — arms the RTC alarm
echo 'NIGHTLY_WAKE_AT=22:00' > ~/.config/nightly-session.env
# laptop — must stay two minutes earlier
systemctl --user edit --full desktop-nightly-wake.timer   # OnCalendar=*-*-* 21:58:00
```

Shutdown time:

```bash
systemctl --user edit --full nightly-shutdown.timer       # OnCalendar=*-*-* 07:00:00
systemctl --user daemon-reload && systemctl --user restart nightly-shutdown.timer
```

## Verifying

```bash
# laptop
systemctl --user list-timers desktop-nightly-wake.timer
systemctl --user start desktop-nightly-wake.service && \
  journalctl --user -u desktop-nightly-wake.service -n 5

# desktop
systemctl --user list-timers nightly-shutdown.timer
~/.local/bin/nightly-shutdown --dry-run --force
~/.local/bin/nightly-shutdown --dry-run        # expect SKIP while you are SSH'd in
```

A full cycle can only be proven by powering off and waiting for 21:58, or by
running `sudo rtcwake -m off -t $(( $(date +%s) + 180 ))` and confirming the
machine returns three minutes later.

## Rollback

```bash
# laptop
systemctl --user disable --now desktop-nightly-wake.timer
rm ~/.config/systemd/user/desktop-nightly-wake.{timer,service}

# desktop
systemctl --user disable --now nightly-shutdown.timer
rm ~/.config/systemd/user/nightly-shutdown.{timer,service}
rm -r ~/.config/systemd/user/nightly-shutdown.service.d
rm ~/.config/autostart/bionic.desktop ~/.local/bin/nightly-shutdown ~/.config/nightly-session.env
systemctl --user daemon-reload
```

## Agent skills for waking and shutting down

The agent skills that wake and shut down machines (`wake-laptop`, `wake-falke`,
`shutdown-laptop`, `shutdown-falke`, `desktop-rustdesk`) are not shared with
every project. On the laptop they live in the T3 `common` project
(`~/.t3/projects/common/.agents/skills/`, moved 2026-11-09), so start
machine-control threads there. On the desktop, `wake-laptop`, `shutdown-laptop`
and `desktop-rustdesk` are still in the shared `~/.agents/skills/` folder.
