# Automatic pull

## The honest answer

Git is pull-based. GitHub cannot reach into a workstation and make it pull. A
server-side Action can only run on GitHub-hosted runners or on a self-hosted
runner you install on that machine.

So “force the other two environments to pull” means one of these:

1. Install a small pull agent on each machine (recommended).
2. Add the machine as a self-hosted GitHub Actions runner and run a deployment
   job there.
3. Use T3’s own scheduled tasks to run the sync command on each environment.

The repo now includes option 1: `bin/t3cfg-sync`.

## What the sync agent does

```bash
./bin/t3cfg-sync --profile desktop --apply --strict
```

It:

- refuses to run on anything except `main`
- refuses if tracked files are modified
- fetches `origin/main` and does **`git pull --ff-only`**
- takes a lock so two syncs cannot overlap
- renders the profile before writing anything
- applies OMP/OpenCode even while T3 is open
- **defers T3 settings while T3 is running** (so it cannot fight the UI) and
  logs that it did so
- runs the read-only Cursor Plus `guard` on every pass, **including when T3
  settings are deferred while T3 is open** (so drift is always detected)
- runs `t3cfg doctor`
- with `--strict`, fails if T3 was deferred, guard sees Cursor Plus drift, or
  the doctor reports a FAIL

Useful variants:

```bash
# Pull and show the plan, but do not apply
./bin/t3cfg-sync --profile laptop

# Apply only the local clients
./bin/t3cfg-sync --profile desktop --apply --components omp,opencode

# Require T3 to be closed; fail instead of deferring
./bin/t3cfg-sync --profile desktop --apply --no-defer-t3 --strict

# Test the sync logic without touching the remote
./bin/t3cfg-sync --profile desktop --no-pull --apply
```

Logs go to `~/.config/t3-master/sync.log`.

## Linux: systemd user timer

On each machine, after cloning the repo:

```bash
cd ~/t3-code-master-config
mkdir -p ~/.config/systemd/user

sed 's/@PROFILE@/desktop/' sync/systemd/t3cfg-sync.service.tmpl \
  > ~/.config/systemd/user/t3cfg-sync.service
cp sync/systemd/t3cfg-sync.timer ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now t3cfg-sync.timer
systemctl --user list-timers t3cfg-sync.timer
```

Use `laptop` or `server` in the `sed` command for the other machines. The timer
runs two minutes after boot and every 15 minutes after that. It is persistent, so
a missed run is caught up at the next boot.

For the laptop, the repository path may be under `/home/mars`; the unit uses
`%h`, so it follows the local user.

## Windows: Task Scheduler

On the AMC work laptop (`amc-5rcsfw3`), run PowerShell as the user who owns the repo:

```powershell
$repo = "$env:USERPROFILE\t3-code-master-config"
$action = New-ScheduledTaskAction `
  -Execute "python" `
  -Argument "`"$repo\scripts\t3cfg_sync.py`" --profile work --apply --strict" `
  -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtStartup
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
  -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask `
  -TaskName "T3 Code Master Config Sync" `
  -Action $action `
  -Trigger @($trigger, $repeat) `
  -RunLevel Highest `
  -Force
```

The `t3cfg-sync.cmd` wrapper is available if you prefer the Task Scheduler
“Start a program” UI:

```
C:\Users\<you>\t3-code-master-config\bin\t3cfg-sync.cmd --profile work --apply --strict
```

## “Require” at T3 launch

If you want the config to be checked before T3 opens, point your launcher at a
wrapper instead of the T3 binary:

```bash
#!/usr/bin/env sh
set -eu
T3_PROFILE=desktop
"$HOME/t3-code-master-config/bin/t3cfg-sync" --profile "$T3_PROFILE" --apply --strict || exit 1
exec "/path/to/t3code" "$@"
```

Then update the desktop entry / shortcut to run that wrapper. This is the only
way to make the check “required” at launch without modifying T3 itself.

## T3 Connect is not an environment link

This is the distinction that matters:

- `t3 connect status` being on and showing a connected tunnel means **this
  machine is reachable from other devices**.
- `t3 environment list` returning `No linked environments` means **this
  machine's agents cannot address another T3 environment**.
- The “4 connections” in the relay status are Cloudflare tunnel edges for this
  one environment, not four T3 environments.

To let this environment's agents work on the laptop/server, link them from this
desktop:

```bash
t3 environment link <their-t3-connect-url> --access full-access
# If the other side requires a pairing code:
#   t3 pair                      # run there
#   t3 environment link <url> --pairing-code <code> --access full-access
```

Get the URL from that environment's T3 Connect/Connections settings. After
linking, `t3 environment list` shows the environment and its id. The repo's sync
timer still has to be installed **on that machine**; linking authorizes this
desktop's agents to work there, but it does not install a systemd timer or
Windows scheduled task for you.

## Current access blockers

- `mars-laptop` was online but its timer needed an interactive Tailscale SSH
  check from the desktop. The timer and guard are installed on the laptop as of
  2026-11-02 (installed locally). Verify with
  `systemctl --user list-timers t3cfg-sync.timer` on the laptop.
- The AMC work laptop (`amc-5rcsfw3`) is offline on the tailnet. Its `work` profile is ready, but its timer
  cannot be installed until it is online.
- The desktop has T3 Connect enabled, but `t3 environment list` currently says
  `No linked environments`. Use `t3 environment link` above, or run the
  bootstrap locally on each machine.

## Safety properties

- `git pull --ff-only` only; no reset, no force, no discard.
- Dirty tracked files abort the sync.
- One sync at a time via `~/.config/t3-master/sync.lock`.
- T3 settings are deferred while T3 is running; the Cursor fix is applied on the
  next run after T3 is closed. The read-only guard still detects drift during
  that window and logs it.
- `apply` refuses to write T3 settings whose merged Cursor favorites would
  trigger `Model unavailable on Plus`.
- The sync never loads or unloads models; Bionic does that on request.
- No secrets are placed in the repo or the log.
- A broken render aborts before any live write.

## Why not a GitHub Action alone?

A GitHub-hosted runner has no route to your private desktop/laptop/server. A
self-hosted runner would work, but it is a larger install than the timer and
still needs one runner per machine. The timer is the smallest reliable mechanism.
