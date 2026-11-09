# T3 Code Master Config

One source of truth for the three T3 Code Nightly environments and how they reach
local Qwen models in LM Studio Bionic on the desktop.

This repository is intentionally **configuration-only**. It does not vendor model
weights, API keys, or session databases. It renders the
small, machine-specific files from three profiles and can merge them into a live
machine with a backup and a dry-run first.

> **Status:** desktop and laptop sync from this repo; `amc-5rcsfw3` (work) is
> offline. Since 2026-11-08 LM Studio Bionic is the only local model engine; the
> custom llama.cpp router and its Tailscale gateway were removed.

---

## The problem this solves

The same three things were drifting on every machine:

1. **Cursor Plus model settings.** Cursor Plus only allows Composer 2.5, Grok 4.5,
   Grok 4.6, and Grok 4.7 with *fixed* settings. T3 has to persist those option
   values in the model selection. If they are missing, Cursor returns:
   `Model unavailable on Plus...`
2. **Local Qwen model IDs.** T3, OMP, and OpenCode were mixing ids from two
   different servers, so favorites pointed at ids that no longer existed.
3. **Local endpoints and GPU ownership.** Two engines (LM Studio Bionic and a custom
   llama.cpp router) loaded models onto the same 24 GB GPU and pushed each other
   out of VRAM. Now every client on every machine uses Bionic.

The repo makes one canonical choice for each of those and generates the rest.

---

## Canonical architecture

```
                       Cursor Plus (cloud)
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ T3 Code Nightly (desktop, laptop, work laptop)               │
│  cursor provider ── fixed Plus options                       │
│  pi provider ────── OMP ──────┐                              │
│  opencode provider ─ OpenCode ─┤                             │
└────────────────────────────────┼─────────────────────────────┘
                                 │  OpenAI-compatible /v1 over Tailscale
                                 ▼
                 ┌────────────────────────────────┐
                 │ LM Studio Bionic (desktop)     │
                 │ http://100.95.87.91:8088/v1    │
                 │ loads on request, one at a time│
                 └────────────────────────────────┘
```

### Roles, not brand names

| Component | What it actually is | Canonical role |
|---|---|---|
| **LM Studio Bionic** | The LM Studio desktop app/runtime on the desktop | The only local model engine and server |
| **Oh My Pi (OMP)** | An agent client (`omp`) | T3 `pi` provider + standalone `omp-qwen` |
| **OpenCode** | An agent client/server | T3 `opencode` provider, using `qwen-local` |
| **T3 Code Nightly** | Orchestrator/client | Owns Cursor, `pi`, and `opencode` selections |

### Local endpoint

Every machine uses `http://100.95.87.91:8088/v1`. Bionic binds its server to the
desktop's Tailscale address only, so the desktop uses that address too.

| Machine | Profile | Local endpoint |
|---|---|---|
| `mars-desktop-cachy` | `desktop` | `http://100.95.87.91:8088/v1` (Bionic runs here) |
| `mars-laptop` | `laptop` | `http://100.95.87.91:8088/v1` |
| `amc-5rcsfw3` (AMC work laptop) | `work` | `http://100.95.87.91:8088/v1` |

### Model IDs

All clients use Bionic's ids:

- `qwen/qwen3.8-27b` — Qwen3.8 27B Q4_K_M (default, vision)
- `14bfe4b42be4a925d98816db830155f476c605e7` — Swift 1.5 Qwen3.8 27B Q4_K_S (vision)
- `58ebd123013160600229eda180b5b17f3fb7af9d` — Qwen3.8 27B Uncensored OrcaRouter Q4_K_M (text only)

Clients assume `32768` context and a `4096` output cap. See
[`docs/local-inference.md`](docs/local-inference.md).

### Cursor Plus matrix

| Cursor model | T3 option IDs and values | Native meaning |
|---|---|---|
| `composer-2.5` | `fastMode=false` | `fast=false` |
| `grok-4.6` | `effort=medium`, `fastMode=false` | `reasoning_effort=medium`, `fast=false` |
| `grok-4.7` | `contextWindow=256k`, `fastMode=false`, `reasoning_effort=medium` | `contextWindow=256k`, `fast=false`, `reasoning_effort=medium` |

**Trap:** Grok 4.6 uses T3's `effort` option; Grok 4.7 uses `reasoning_effort`.
They are not interchangeable. Grok 4.7 also must not be in
`providerModelPreferences.cursor.hiddenModels`.

---

## Repository layout

```
profiles/                 one JSON profile per machine
catalog/                  canonical Cursor Plus and Qwen model definitions
templates/                OMP source files
scripts/t3cfg.py          render / plan / apply / doctor / snapshot
scripts/t3cfg_sync.py     pull / apply agent for timers and launch wrappers
bin/t3cfg-sync            shell/Windows wrappers for the pull agent
sync/systemd/             Linux user timer + service templates
docs/                     Cursor Plus, local inference, sync, runbook, security
tests/                    stdlib-only regression tests
.github/workflows/        CI validation
```

Generated output goes to `build/<profile>/`. Live-machine backups go next to the
file being changed as `*.bak-t3cfg-<timestamp>`.

---

## Quick start

```bash
git clone https://github.com/TriBrigadeMars/t3-code-master-config.git
cd t3-code-master-config

# 1. See what would change. No writes.
./bin/t3cfg plan --profile desktop

# 2. Render the exact files for this machine.
./bin/t3cfg render --profile desktop

# 3. Quit T3 Code first. Then apply only the T3 settings.
./bin/t3cfg apply --profile desktop --only t3

# 4. Reopen T3 and verify.
./bin/t3cfg doctor --profile desktop
```

Apply the whole machine (T3 settings, OMP models, and the OpenCode provider):

```bash
./bin/t3cfg apply --profile desktop --only all
```

Every apply is backed up and refuses to touch T3 settings while `t3code` is
running unless `--force` is passed. Prefer quitting T3.

### Machine-specific commands

```bash
# laptop
./bin/t3cfg plan  --profile laptop
./bin/t3cfg apply --profile laptop --only t3,omp,opencode

# AMC work laptop, when it is online again
./bin/t3cfg plan  --profile work
```

Each profile is applied on its own machine.

### Automatic pull

Git cannot push into a workstation, so each machine needs a pull agent. T3
Connect being on only makes this machine reachable; it does not create an
environment link (`t3 environment list` must show the other machine). The repo
includes the pull agent:

```bash
./bin/t3cfg-sync --profile desktop --apply
```

It refuses a dirty tracked worktree, uses `git pull --ff-only`, takes a lock,
renders before writing, applies the profile, and **defers T3 settings while T3 is
open**. Add `--strict` when you want the run to fail if T3 settings were deferred
or `doctor` fails.

Install the systemd user timer on each Linux machine:

```bash
cd ~/t3-code-master-config
mkdir -p ~/.config/systemd/user
sed 's/@PROFILE@/desktop/' sync/systemd/t3cfg-sync.service.tmpl \
  > ~/.config/systemd/user/t3cfg-sync.service
cp sync/systemd/t3cfg-sync.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now t3cfg-sync.timer
```

Windows Task Scheduler and the launch-wrapper “require” pattern are in
[`docs/auto-sync.md`](docs/auto-sync.md).

---

## What the applier changes

### T3 Code (`--only t3`)

`~/.t3/userdata/settings.json` is merged, not replaced:

- Upserts Cursor favorites with the exact Plus options.
- Removes `grok-4.7` from Cursor's `hiddenModels`.
- Upserts `pi` favorites for the three Qwen variants and removes the ids that only
  the removed router advertised (`qwen/qwen3.8-27b-unsloth`, `-uncensored`).
- Upserts one `opencode` favorite through the new `qwen-local` provider.
- Removes the stale `opencode/lmstudio/<hash>` favorite.
- Unhides the canonical local model IDs.
- Optionally sets the profile's default model.

### OMP (`--only omp`)

- Replaces only the `lm-studio` provider block in `~/.omp/agent/models.yml`;
  other providers are preserved.
- Uses `discovery.type: openai-models-list`, which reads Bionic's `/v1/models`.
- Creates an isolated `~/.omp/profiles/qwen/agent/` baseline and an
  `~/.local/bin/omp-qwen` launcher.
- Sets 32K context, 4K output, and `defaultLevel: medium`.

### OpenCode (`--only opencode`)

- Adds `providers.qwen-local` with package
  `@opencode/ai/providers/openai-compatible`, `settings.baseURL` pointing at the
  canonical endpoint, and explicit model limits.
- Leaves every other OpenCode provider untouched.
- Does **not** use OpenCode's built-in `lmstudio` provider, which assumes
  `127.0.0.1:1234`; Bionic listens on the Tailscale address.

---

## Verification

`doctor` checks the files and the live endpoint; `guard` is the read-only
tripwire for the `Model unavailable on Plus` class (missing/wrong Cursor
favorite `options`, grok-4.7 hidden). The sync timer runs `guard` on every
pass, even while T3 is open:

```bash
./bin/t3cfg doctor --profile desktop
./bin/t3cfg doctor --profile laptop
./bin/t3cfg guard --profile laptop
```

It reports PASS/WARN/FAIL for:

- Cursor favorite options and hidden-model state.
- OMP model IDs, endpoint, context, output cap, and effort.
- OpenCode `qwen-local` provider and model limits.
- Bionic's `/v1/models` reachability and all three model ids.
- Recent `Model unavailable on Plus` errors in T3 provider logs (warning only).

Run it on the machine you are checking.

---

## Security

- No API keys, OAuth tokens, passwords, or email addresses are committed.
- The applier only writes the local provider blocks; it does not copy provider
  headers or keys.
- `snapshot` redacts secrets before writing.
- One existing OMP file on the desktop contains a plaintext GitHub token. Do not
  commit it, and rotate it. See [`docs/security.md`](docs/security.md).

---

## Further reading

- [`docs/cursor-plus.md`](docs/cursor-plus.md) — the fixed Cursor Plus option
  matrix, including the `effort` vs `reasoning_effort` trap.
- [`docs/local-inference.md`](docs/local-inference.md) — Bionic as the one
  engine: endpoint, model ids, GPU sharing, and troubleshooting.
- [`docs/runbook.md`](docs/runbook.md) — per-machine apply, rollback, and
  troubleshooting.
- [`docs/security.md`](docs/security.md) — token exposure, redaction, and
  repository policy.
- [`docs/auto-sync.md`](docs/auto-sync.md) — automatic pull/apply on the
  laptop and work laptop, including the one-time bootstrap.
- [`docs/nightly-session.md`](docs/nightly-session.md) — the nightly desktop
  wake (21:58 from the laptop) and guarded 07:00 shutdown for unattended runs.
