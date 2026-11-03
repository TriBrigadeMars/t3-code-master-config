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

The desktop is the only machine that runs the tuned router.

### 1.1 Stop the competing server

LM Studio Bionic must not hold a large model while the tuned router is running.
In LM Studio, unload the loaded model or quit the app. Confirm:

```bash
/home/lqfalke/.lmstudio/bin/lms ps
```

If a large model is listed, unload it before continuing.

### 1.2 Apply

```bash
./bin/t3cfg apply --profile desktop --only t3,omp,opencode,qwen
```

This writes:

- T3 settings patch (merge, backups)
- OMP default provider block + isolated Qwen profile
- OpenCode `qwen-local` provider
- Qwen router/gateway config and systemd units

It does not start or load a model.

### 1.3 Start the router and gateway

```bash
systemctl --user daemon-reload
systemctl --user enable --now qwen-inference.service
systemctl --user enable --now qwen-laptop-gateway.service
```

### 1.4 Verify

```bash
curl -fsS http://127.0.0.1:1234/v1/models | jq -r '.data[].id'
./bin/t3cfg doctor --profile desktop
```

Expected IDs:

```
qwen/qwen3.8-27b
qwen/qwen3.8-27b-unsloth
qwen/qwen3.8-27b-uncensored
```

### 1.5 Reopen T3 and test

- Cursor `Composer 2.5`: one short prompt.
- Cursor `Grok 4.7`: one short prompt; it must be visible in the picker.
- `pi` local Qwen Q4: one short prompt.
- `opencode` `qwen-local/qwen/qwen3.8-27b`: one short prompt.

## 2. Laptop (`mars-laptop`)

The laptop consumes the desktop gateway; it does not need the GGUF files or the
intelfa build.

### 2.1 Confirm the gateway

On the laptop:

```bash
curl -fsS http://100.95.87.91:1235/health
curl -fsS http://100.95.87.91:1235/v1/models | jq -r '.data[].id'
```

If this fails, fix the desktop router/gateway first. The laptop cannot fix a dead
upstream.

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

`doctor` must run on the laptop; running it on the desktop only checks the
desktop side of the endpoint.

### 2.4 If Tailscale SSH is blocked

The desktop cannot currently apply the laptop profile because the tailnet SSH
policy requires an interactive check. Run the commands locally on the laptop, or
approve the Tailscale check first. This is an access issue, not a config issue.

## 3. Server (`mars-server`)

The server is offline. Its profile defaults to the desktop gateway.

When it is online:

```bash
./bin/t3cfg plan --profile server
./bin/t3cfg apply --profile server --only t3,omp,opencode
./bin/t3cfg doctor --profile server
```

If the server is booted into CachyOS, copy `profiles/server.json` to a Linux
profile and replace `%USERPROFILE%`/`%APPDATA%` with `~` paths.

If the server should own its own router instead of using the desktop:

1. Set `qwen.role` to `server`.
2. Set `qwen.endpoint` to `http://127.0.0.1:1234/v1`.
3. Add the `qwen` component to `--only`.
4. Ensure the intelfa build, GGUFs, `serve.py`, and `tune.py` exist on that
   machine.

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
| `Local /v1/models ... connection refused` | Start `qwen-inference.service`; check `:1234` |
| Laptop `/v1/models` fails | Check `qwen-laptop-gateway.service` and the desktop router |
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
