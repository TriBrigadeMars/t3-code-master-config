# Runbook

## 0. Before touching a machine

1. Quit T3 Code.
2. Capture a redacted snapshot:
   ```bash
   ./bin/t3cfg snapshot --profile desktop
   ```
3. Review the generated plan:
   ```bash
   ./bin/t3cfg plan --profile desktop
   ```
4. Do not commit anything from `~/.t3/userdata`, `~/.omp`, `~/.config/opencode`,
   or `snapshots/`.

## 1. Desktop (`mars-desktop-cachy`)

LM Studio Bionic runs here and is the only local model engine for all three
machines. It autostarts at login and serves `http://100.95.87.91:8088/v1`.

### 1.1 Confirm Bionic is serving

```bash
curl -fsS http://100.95.87.91:8088/v1/models | jq -r '.data[].id'
lms ps
```

Expected ids include:

```
qwen/qwen3.8-27b
14bfe4b42be4a925d98816db830155f476c605e7
58ebd123013160600229eda180b5b17f3fb7af9d
```

### 1.2 Apply

```bash
./bin/t3cfg apply --profile desktop --only t3,omp,opencode
```

This writes:

- T3 settings patch (merge, backups)
- OMP default provider block + isolated Qwen profile
- OpenCode `qwen-local` provider

It never loads a model. Bionic loads one when a client first asks for it.

### 1.3 Verify

```bash
./bin/t3cfg doctor --profile desktop
omp-qwen --check
```

### 1.5 Reopen T3 and test

- Cursor `Composer 2.5`: one short prompt.
- Cursor `Grok 4.7`: one short prompt; it must be visible in the picker.
- `pi` local Qwen Q4: one short prompt.
- `opencode` `qwen-local/qwen/qwen3.8-27b`: one short prompt.

The first local prompt makes Bionic load the model, which can take a few minutes.

## 2. Laptop (`mars-laptop`)

The laptop uses Bionic on the desktop over Tailscale; it needs no model files.

### 2.1 Confirm it can reach Bionic

On the laptop:

```bash
curl -fsS http://100.95.87.91:8088/v1/models | jq -r '.data[].id'
```

If this fails, check that Bionic is running on the desktop and that both machines
are up in `tailscale status`.

### 2.2 Apply

```bash
git clone https://github.com/TriBrigadeMars/t3-code-master-config.git
cd t3-code-master-config
./bin/t3cfg plan --profile laptop
./bin/t3cfg apply --profile laptop --only t3,omp,opencode
```

### 2.3 Verify on the laptop

```bash
./bin/t3cfg doctor --profile laptop
```

`doctor` must run on the laptop so it checks the laptop's own files.

### 2.4 If Tailscale SSH is blocked

The desktop cannot currently apply the laptop profile because the tailnet SSH
policy requires an interactive check. Run the commands locally on the laptop, or
approve the Tailscale check first. This is an access issue, not a config issue.

## 3. AMC work laptop (`work`)

The work laptop is offline. Like the laptop, it uses Bionic on the desktop over
Tailscale.

When it is online:

```bash
./bin/t3cfg plan --profile work
./bin/t3cfg apply --profile work --only t3,omp,opencode
./bin/t3cfg doctor --profile work
```

If the machine is booted into Linux, copy `profiles/work.json` to a Linux
profile and replace `%USERPROFILE%`/`%APPDATA%` with `~` paths.

## 4. Rollback

Every apply creates a sibling backup:

```
settings.json.bak-t3cfg-<timestamp>
models.yml.bak-t3cfg-<timestamp>
opencode.json.bak-t3cfg-<timestamp>
```

To roll back one file:

```bash
cp ~/.t3/userdata/settings.json.bak-t3cfg-20261102T... ~/.t3/userdata/settings.json
```

Restart T3/OpenCode after restoring.

## 5. Common failures

| Symptom | Check |
|---|---|
| `T3 is running` | Quit T3, or use `--force` only if you accept that T3 may overwrite the file |
| `Bionic /v1/models ... connection refused` | Start Bionic on the desktop; check `tailscale status` |
| First local request takes minutes | Bionic is loading the 17 GB model; later requests are fast |
| VRAM spill or very slow prompts | Another app (e.g. Unsloth Studio) holds a model; unload it there |
| `grok-4.7` still hidden | Apply `--only t3`; confirm `hiddenModels` in doctor |
| Cursor still says `Model unavailable on Plus` | Run `./bin/t3cfg guard --profile <name>`; it names the exact favorite and option defect |
| Cursor works, then breaks again later | T3 rewrote `settings.json` and dropped `options`; the timer's guard/apply loop repairs it on the next pass after T3 quits |
| OpenCode hashes appear | Use `providers.qwen-local`, not built-in `lmstudio`; restart T3 |
| OMP output cap looks wrong | Re-apply OMP; expect `maxTokens: 4096` |
| OMP sees no models | Check `discovery: openai-models-list` and `/v1/models` |

## 6. Drift control

T3 upgrades can rewrite `settings.json` — and, as seen on `mars-laptop`,
rewrites can drop the Cursor favorites' `options`. LM Studio upgrades can
change ports and model IDs. After any upgrade:

```bash
./bin/t3cfg guard --profile <name>
./bin/t3cfg plan --profile <name>
./bin/t3cfg doctor --profile <name>
```

`guard` is the fast tripwire: read-only, no endpoint needed, and it is what the
sync timer runs every pass.

The generated `build/<profile>/manifest.json` records the files and hashes that
would be written. `doctor` checks the live values, not just the rendered files.
