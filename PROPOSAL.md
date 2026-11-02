# Proposal: one master config for three T3 Code Nightly environments

**Date:** 2026-11-02
**Repo:** <https://github.com/TriBrigadeMars/t3-code-master-config>
**Scope:** T3 Code Nightly provider/model settings, Cursor Plus fixed options, OMP,
OpenCode, and the local Qwen 3.8 27B serving stack.

---

## 1. Executive summary

The three environments are not failing for three different reasons. They are failing
for the same three reasons, repeated per machine:

1. **Cursor Plus selections are missing their fixed parameters.** Cursor does not
   accept just a model ID on Plus; it requires the fixed option values. T3 persists
   those values in `favorites[].options`. When they are absent, every Cursor run
   fails with `Model unavailable on Plus...`.
2. **The local model identity is unstable.** The tuned router advertises
   `qwen/qwen3.8-27b`; LM Studio's native API advertises hashes. T3 and OpenCode
   were mixing both, so favorites and hidden-model lists pointed at IDs that did not
   exist on the endpoint currently in use.
3. **The local endpoint owner is ambiguous.** Desktop clients point at
   `127.0.0.1:1234`, but the tuned router there is dead while LM Studio Bionic is
   serving a different model on `:8088` / an ephemeral port. The laptop gateway is
   alive but its `:1234` upstream is dead.

The proposal is a **source-of-truth repo with three profiles, a renderer, and a
dry-run-first applier**. It does not try to make LM Studio, OMP, and OpenCode into
the same thing. It gives each its correct role and makes them agree on one endpoint
and three model IDs.

---

## 2. What was actually inspected

| Source | Evidence |
|---|---|
| Desktop T3 userdata | `~/.t3/userdata/settings.json`, `model-manifest.json`, `server-runtime.json`, `environment-id`, `peer_environment_links` |
| Desktop T3 version | `0.1.7-nightly.20261102.4080`, server on `0.0.0.0:3773` |
| Cursor provider status | T3 provider log `events.c1d294f2-...log`: authenticated, models `grok-4.7`, `grok-4.6`, `composer-2.5`, `grok-4.5`; exact option descriptors |
| Cursor failures | T3 provider logs: repeated `Model unavailable on Plus Plus includes Composer 2.5, Grok 4.7, Grok 4.6, and Grok 4.5 with fixed settings.` |
| Desktop T3 peer links | `peer_environment_links` is empty; no second T3 environment is linked |
| Tailnet | `mars-desktop-cachy` (this host, `100.95.87.91`), `mars-laptop` (`100.82.15.19`, active), `mars-server` (`100.111.54.87`, offline 24 days) |
| Laptop handoff | `~/.local/share/qwen-inference/mars-laptop-omp-handoff.md` documents the `:1235` gateway and laptop OMP baseline |
| Desktop router | `~/.config/qwen-inference/server.json`, `models-preset.ini`, `serve.py`; `qwen-inference.service` enabled but inactive/dead |
| Desktop gateway | `qwen-laptop-gateway.service` active; gateway listens on `100.95.87.91:1235`; upstream `127.0.0.1:1234` is dead |
| LM Studio | Bionic app running; `lms server status` says API on `:8088`; llama-server child on `:39397` serving a Qwen3.6-35B model |
| OMP | `omp/18.13.2` at `~/.bun/bin/omp`; `~/.omp/agent/models.yml` has three LM Studio variants, but `maxTokens: 32768` and `defaultLevel: xhigh` |
| OpenCode | `v2.2.0`; `providers.lmstudio` only has timeouts; built-in provider discovers via LM Studio native `/api/v1/models`, not the router's `/v1/models` |
| Master setup doc | `~/AI_Agents_Master_Setup.md` documents the custom `build-intelfa` runtime, model paths, and machine baseline |

### What was not inspected

- **Laptop live T3/OMP/OpenCode configs.** Tailscale SSH to `mars-laptop` requires
  an interactive check (`https://login.tailscale.com/a/...`). The topology and
  handoff were read on the desktop; the live files still need a one-time login.
- **`mars-server` live configs.** Tailscale reports it offline for 24 days. The
  server profile is therefore provisional and CI-rendered only.

This is stated explicitly so the repo does not pretend the other two machines were
read when they were not.

---

## 3. Root-cause detail

### 3.1 Cursor Plus: the exact trap

T3's Cursor provider reports these descriptors (from the provider log):

| Model | T3 option descriptors |
|---|---|
| `composer-2.5` | `fastMode` = only `false` |
| `grok-4.6` | `effort` = only `medium`; `fastMode` = only `false` |
| `grok-4.7` | `contextWindow` = only `256k`; `fastMode` = only `false`; `reasoning_effort` = only `medium` |

The current desktop `settings.json` has:

```json
{
  "provider": "cursor",
  "model": "composer-2.5"
}
```

It has no `options`. That is the direct cause of the Composer failures. The last
known-good backup (`settings.json.bak-omp-integration`) had the `fastMode=false`
option; the current file lost it.

`grok-4.7` is also listed in:

```json
"cursor": {
  "hiddenModels": ["grok-4.5", "grok-4.7"]
}
```

So even after the options are fixed, Grok 4.7 is not offered in the picker.

The repo's catalog encodes the exact option IDs, including the easily missed
difference between `effort` (4.6) and `reasoning_effort` (4.7).

### 3.2 Local Qwen: three identities for one model

The same model appears under at least three names:

| Source | Example ID |
|---|---|
| Tuned router `/v1/models` | `qwen/qwen3.8-27b` |
| LM Studio native API | `lmstudio/58ebd123...` (hash) |
| OMP provider | `lm-studio/qwen/qwen3.8-27b` |
| OpenCode built-in provider | `lmstudio/qwen/qwen3.8-27b` or a hash |

T3's current favorites include `opencode/lmstudio/58ebd123...`, a hash that is not
served by the router. T3's `opencode.hiddenModels` also hides the clean
`lmstudio/qwen/qwen3.8-27b`, so the clean ID cannot be selected there.

The fix is not to rename one client's model. It is to make every client speak to
the same endpoint and use the same advertised IDs.

### 3.3 OpenCode's built-in LM Studio provider is the wrong tool for the router

The OpenCode v2 binary contains the built-in provider definition:

- origin: `http://127.0.0.1:1234`
- discovery path: **`/api/v1/models`**
- strip suffix: `/api/v1` or `/v1`

The tuned router is a `llama.cpp` server. It exposes the OpenAI-compatible
`/v1/models`, not LM Studio's native `/api/v1/models`. Therefore OpenCode's
built-in `lmstudio` provider cannot discover the router even when `:1234` is up.

The repo adds a custom provider instead:

```json
{
  "providers": {
    "qwen-local": {
      "name": "Qwen Local (tuned router)",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "http://127.0.0.1:1234/v1"
      },
      "models": {
        "qwen/qwen3.8-27b": { "...": "..." }
      }
    }
  }
}
```

This is the same package the existing `cliproxy` and `copilot` providers use, so
it is a supported config shape, not a hack.

### 3.4 The endpoint is dead, so every client looks broken

At audit time:

- `ss -ltnp` showed no listener on `1234`, `8888`, or `8317`.
- `qwen-inference.service` was inactive (dead) since 13:46; it had exited 0.
- `qwen-laptop-gateway.service` was active, but its upstream `127.0.0.1:1234`
  was down.
- LM Studio Bionic was running; `lms server status` reported port `8088`; its
  child `llama-server` was on `39397` serving a Qwen3.6-35B-A3B model.

So the laptop gateway was forwarding to nothing, and desktop clients pointed at
nothing. The correct fix is an ownership decision, not another client config:

- **Recommended:** the tuned router owns `:1234`; LM Studio is used as a model
  manager with its server stopped (or at least not loading a second large model).
- **Alternative:** LM Studio owns `:8088`; all clients point there and accept the
  loss of the tuned `build-intelfa` / DFlash2 stack and clean IDs.

Do not run both with large models loaded on a 24 GB GPU.

---

## 4. Target state by machine

### Desktop (`mars-desktop-cachy`)

| Item | Target |
|---|---|
| T3 Cursor favorites | `composer-2.5` with `fastMode=false`; `grok-4.6` with `effort=medium`, `fastMode=false`; `grok-4.7` with `contextWindow=256k`, `fastMode=false`, `reasoning_effort=medium` |
| Cursor hidden models | `grok-4.5` only; `grok-4.7` visible |
| T3 `pi` favorites | Three Qwen variants via `lm-studio/...` |
| T3 `opencode` favorite | `qwen-local/qwen/qwen3.8-27b` |
| OMP default agent | `lm-studio` provider points at `http://127.0.0.1:1234/v1`; 32K ctx; 4K out; medium effort |
| OMP isolated profile | `~/.omp/profiles/qwen/agent/` + `omp-qwen` launcher |
| OpenCode | `providers.qwen-local` at `http://127.0.0.1:1234/v1` with three explicit models |
| Router | `qwen-inference.service` running before T3; gateway active |
| LM Studio | GUI/model manager; native server stopped or not loading a competing model |

### Laptop (`mars-laptop`)

Same T3/Cursor/OMP/OpenCode layout, except every local base URL is
`http://100.95.87.91:1235/v1`. The laptop does not need the GGUF files, the
`models-preset.ini`, or the intelfa build. Its `omp-qwen` preflight must query the
remote `/v1/models` and fail closed when the desktop is unreachable.

### Server (`mars-server`)

Same layout again, defaulting to the desktop gateway when Tailscale is up. If the
server is later given its own GPU/router, switch its profile's `qwen.role` to
`server` and endpoint to `http://127.0.0.1:1234/v1`.

---

## 5. Exact desktop changes the repo will make

These are the changes `./bin/t3cfg apply --profile desktop` will propose. The
current values are from the audit; the target values are generated from
`catalog/cursor-plus.json` and `catalog/qwen-local.json`.

### T3 `settings.json`

1. Upsert `favorites` entries:
   - `cursor/composer-2.5` + `[{"id":"fastMode","value":"false"}]`
   - `cursor/grok-4.6` + `[{"id":"effort","value":"medium"},{"id":"fastMode","value":"false"}]`
   - `cursor/grok-4.7` + `[{"id":"contextWindow","value":"256k"},{"id":"fastMode","value":"false"},{"id":"reasoning_effort","value":"medium"}]`
   - `pi/lm-studio/qwen/qwen3.8-27b`
   - `pi/lm-studio/qwen/qwen3.8-27b-unsloth`
   - `pi/lm-studio/qwen/qwen3.8-27b-uncensored`
   - `opencode/qwen-local/qwen/qwen3.8-27b`
2. Remove `opencode/lmstudio/58eb...` and any other stale
   `opencode/lmstudio/*` favorites.
3. Remove `grok-4.7` from `providerModelPreferences.cursor.hiddenModels`.
4. Remove the three canonical Qwen IDs from the `pi` and `opencode` hidden lists.
5. Set `providerInstances.pi.config.binaryPath` to `~/.bun/bin/omp` (v18.13.2),
   not the stale `~/.local/bin/omp` (v18.12.6).
6. Leave `defaultModelSelection` at Codex `gpt-6-luna` on the desktop unless the
   user opts into local-by-default.

### OMP `~/.omp/agent/models.yml`

- Replace the `lm-studio` provider block with:
  - base URL `http://127.0.0.1:1234/v1`
  - `api: openai-completions`
  - `auth: none`
  - `discovery.type: openai-models-list`
  - three explicit models, 32K context, 4K output, medium effort, Qwen chat template
- Preserve every other provider block.

### OpenCode `~/.config/opencode/opencode.json`

- Add `providers.qwen-local` (OpenAI-compatible package) at
  `http://127.0.0.1:1234/v1` with the three explicit models.
- Preserve `providers.copilot`, `cliproxy`, `mimo`, `openrouter`, and all headers.

### Systemd / router (desktop)

- Keep `qwen-inference.service` and `qwen-laptop-gateway.service`.
- Write the profile-derived `server.json`, `models-preset.ini`, and gateway peer
  allow-list.
- Do not start the router automatically; the runbook starts it after LM Studio's
  large model is unloaded.

---

## 6. Rollout plan

### Phase 0 — safety (before any apply)

1. Quit T3 Code on the machine being changed.
2. Run `./bin/t3cfg snapshot --profile <name>` to capture a redacted copy.
3. Rotate the plaintext GitHub token found in `~/.omp/agent/models.yml`. It is
   already in a local config and may be in backups; treat it as exposed.
4. Do not commit any `~/.t3/userdata`, `~/.omp`, or `~/.config/opencode` file
   directly. The repo only stores templates and catalogs.

### Phase 1 — desktop

1. `render` and `plan`; review `build/desktop/`.
2. Apply only `t3` first. Reopen T3 and run one short Cursor Composer 2.5 prompt
   and one Grok 4.7 prompt.
3. Apply `omp` and `opencode`; restart T3 (and OpenCode if standalone).
4. Free the GPU: unload the LM Studio model, start `qwen-inference.service`, then
   confirm `curl -s http://127.0.0.1:1234/v1/models`.
5. Run `doctor`.

### Phase 2 — laptop

1. Approve the Tailscale SSH check for the laptop, or run the commands locally on
   the laptop.
2. `render`/`plan` with `--profile laptop`.
3. Apply `t3`, `omp`, `opencode`.
4. Confirm the laptop can reach `http://100.95.87.91:1235/health` and
   `/v1/models` while the desktop router is up.
5. Run `doctor` **on the laptop**.

### Phase 3 — server

1. When `mars-server` is online, confirm whether it is in Windows or CachyOS.
2. Use the `server` profile; adjust paths if Windows user data is not under
   `%USERPROFILE%\.t3\userdata`.
3. Apply and run `doctor`. If the server runs its own router, switch the profile's
   endpoint to `http://127.0.0.1:1234/v1`.

### Phase 4 — keep it from drifting

- CI renders all three profiles and runs the regression tests on every push.
- Each machine pulls the repo and runs `plan` after T3/OMP/OpenCode upgrades.
- T3 upgrades may rewrite `settings.json`; `doctor` catches lost Cursor options.
- LM Studio upgrades may change its native port or model IDs; the canonical path
  never depends on them.

---

## 7. Acceptance criteria

A machine is "done" when all of these are true:

- [ ] `t3cfg doctor --profile <name>` reports no FAIL.
- [ ] Cursor `composer-2.5` completes a short run on Plus.
- [ ] Cursor `grok-4.7` is visible and completes a short run on Plus.
- [ ] Cursor `grok-4.6` still completes a short run on Plus.
- [ ] T3 `pi` lists and runs all three Qwen variants.
- [ ] T3 `opencode` lists `qwen-local/qwen/qwen3.8-27b` and runs it.
- [ ] `/v1/models` returns exactly the three canonical IDs.
- [ ] The laptop reaches the desktop gateway with the same three IDs.
- [ ] No plaintext API key or token is present in any committed file.

---

## 8. Decisions requested

1. **Local endpoint owner:** tuned router on `:1234` (recommended) or LM Studio
   native server on `:8088`?
2. **Desktop default model:** keep Codex `gpt-6-luna`, or switch the desktop to
   local Qwen by default?
3. **Laptop access:** approve the Tailscale SSH check so the live laptop config can
   be audited and applied, or run the two commands locally on the laptop.
4. **Server timing:** leave the server profile provisional until it is online, or
   prepare a Windows-specific path/profile now?

The repo is safe to merge and render before those decisions. Only `apply` on the
laptop/server depends on them.
