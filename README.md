# T3 Code Master Config

One source of truth for the three T3 Code Nightly environments and the local Qwen
inference stack behind them.

This repository is intentionally **configuration-only**. It does not vendor model
weights, API keys, session databases, or the custom llama.cpp build. It renders the
small, machine-specific files from three profiles and can merge them into a live
machine with a backup and a dry-run first.

> **Status:** desktop audit complete; laptop topology documented but its live T3
> config still needs a Tailscale SSH check; `amc-5rcsfw3` is offline. See
> [`PROPOSAL.md`](PROPOSAL.md) for the evidence and the rollout plan.

---

## The problem this solves

The same three things were drifting on every machine:

1. **Cursor Plus model settings.** Cursor Plus only allows Composer 2.5, Grok 4.5,
   Grok 4.6, and Grok 4.7 with *fixed* settings. T3 has to persist those option
   values in the model selection. If they are missing, Cursor returns:
   `Model unavailable on Plus...`
2. **Local Qwen model IDs.** The tuned router advertises clean IDs such as
   `qwen/qwen3.8-27b`. LM Studio's native API advertises hashes. T3, OMP, and
   OpenCode were mixing both, so favorites pointed at IDs that no longer existed.
3. **Local endpoints.** Desktop clients pointed at `127.0.0.1:1234`, laptop clients
   pointed at the Tailscale gateway, and LM Studio was sometimes serving a different
   model on a different port. A dead router made every client look broken.

The repo makes one canonical choice for each of those and generates the rest.

---

## Canonical architecture

```
                       Cursor Plus (cloud)
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ T3 Code Nightly                                              │
│  cursor provider ── fixed Plus options                       │
│  pi provider ────── OMP ──────┐                              │
│  opencode provider ─ OpenCode ─┤                             │
└────────────────────────────────┼─────────────────────────────┘
                                 │  OpenAI-compatible /v1
                                 ▼
                 ┌────────────────────────────────┐
                 │ Tuned Qwen router (llama.cpp)  │
                 │ desktop: 127.0.0.1:1234/v1     │
                 │ laptop:  100.95.87.91:1235/v1  │
                 │ 3 model IDs, 32K ctx, 4K out   │
                 └────────────────────────────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 │ LM Studio Bionic (optional)   │
                 │ GUI/model manager, :8088 API  │
                 │ never the canonical endpoint  │
                 └───────────────────────────────┘
```

### Roles, not brand names

| Component | What it actually is | Canonical role |
|---|---|---|
| **LM Studio Bionic** | The LM Studio desktop app/runtime (its app directory is literally `apps/bionic`) | Model manager and optional native server on `:8088` |
| **Tuned Qwen router** | Custom `llama.cpp` `build-intelfa` server with DFlash2 speculative decoding | The one local OpenAI-compatible endpoint |
| **Oh My Pi (OMP)** | An agent client (`omp`) | T3 `pi` provider + standalone `omp-qwen` |
| **OpenCode** | An agent client/server | T3 `opencode` provider, using `qwen-local` |
| **T3 Code Nightly** | Orchestrator/client | Owns Cursor, `pi`, and `opencode` selections |

LM Studio is not a peer of OMP or OpenCode. It is the server; the other two are
clients. Running LM Studio's native server and the tuned router at the same time
means two processes competing for the same 24 GB Arc Pro B60. Pick one owner.

### Canonical local endpoint per machine

| Machine | Profile | Local endpoint | Role |
|---|---|---|---|
| `mars-desktop-cachy` | `desktop` | `http://127.0.0.1:1234/v1` | Runs the tuned router and the Tailscale gateway |
| `mars-laptop` | `laptop` | `http://100.95.87.91:1235/v1` | Uses the desktop's router over Tailscale |
| `amc-5rcsfw3` (AMC work laptop) | `work` | `http://100.95.87.91:1235/v1` | Uses the desktop gateway when online; can switch to a local router |

### Canonical model IDs

All clients use the same three IDs:

- `qwen/qwen3.8-27b` — LM Studio Community Q4_K_M
- `qwen/qwen3.8-27b-unsloth` — Unsloth UD-IQ4_XS
- `qwen/qwen3.8-27b-uncensored` — OrcaRouter Uncensored Q4_K_M

Context is `32768`; output cap is `4096`. The router loads one variant at a time.

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
templates/                OMP, systemd, and gateway source files
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

Apply the whole machine (T3 settings, OMP models, OpenCode provider, and the Qwen
router files on the desktop):

```bash
./bin/t3cfg apply --profile desktop --only all
```

Every apply is backed up and refuses to touch T3 settings while `t3code` is
running unless `--force` is passed. Prefer quitting T3.

### Machine-specific commands

```bash
# laptop: local inference is the desktop gateway
./bin/t3cfg plan  --profile laptop
./bin/t3cfg apply --profile laptop --only t3,omp,opencode

# server: when it is online again
./bin/t3cfg plan  --profile work
```

The laptop and server profiles are rendered and tested in CI, but they cannot be
applied from this desktop until those machines are reachable.

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
- Upserts `pi` favorites for the three Qwen variants.
- Upserts one `opencode` favorite through the new `qwen-local` provider.
- Removes the stale `opencode/lmstudio/<hash>` favorite.
- Unhides the canonical local model IDs.
- Optionally sets the profile's default model.

### OMP (`--only omp`)

- Replaces only the `lm-studio` provider block in `~/.omp/agent/models.yml`;
  other providers are preserved.
- Uses `discovery.type: openai-models-list` so discovery reads `/v1/models`,
  which the tuned router actually serves. The built-in `lm-studio` discovery
  expects `/api/v1/models` and will not see the router.
- Creates an isolated `~/.omp/profiles/qwen/agent/` baseline and an
  `~/.local/bin/omp-qwen` launcher.
- Sets 32K context, 4K output, and `defaultLevel: medium`.

### OpenCode (`--only opencode`)

- Adds `providers.qwen-local` with package
  `@opencode/ai/providers/openai-compatible`, `settings.baseURL` pointing at the
  canonical endpoint, and explicit model limits.
- Leaves every other OpenCode provider untouched.
- Does **not** use the built-in `lmstudio` provider for the router: that provider
  calls LM Studio's native `/api/v1/models`, not the router's `/v1/models`.

### Qwen router (`--only qwen`, desktop only)

- Writes `~/.config/qwen-inference/server.json` and `models-preset.ini`.
- Writes the Tailscale gateway and user systemd units.
- Does **not** start the router or load a model. That is deliberate: starting it
  while LM Studio has a 35B model loaded will fight over VRAM.

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
- `/v1/models` reachability and all three canonical IDs.
- Desktop router and gateway systemd units.
- Recent `Model unavailable on Plus` errors in T3 provider logs (warning only).

For the laptop, run it **on the laptop**. Running it on the desktop only tests the
desktop side of the gateway.

---

## Security

- No API keys, OAuth tokens, passwords, or email addresses are committed.
- The applier only writes the local provider blocks; it does not copy provider
  headers or keys.
- `snapshot` redacts secrets before writing.
- One existing OMP file on the desktop contains a plaintext GitHub token. Do not
  commit it, and rotate it. See [`docs/security.md`](docs/security.md).

---

## Current audit at a glance

| Item | Desktop | Laptop | Server |
|---|---|---|---|
| T3 Code Nightly | `0.1.7-nightly.20261102.4080` | documented, live config pending | offline |
| Cursor Plus | authenticated; `grok-4.7` hidden; `composer-2.5` favorite lost options | same profile intended | same profile intended |
| Tuned router | service exists but is **dead**; `:1234` not listening | gateway running, upstream dead | n/a |
| LM Studio Bionic | running; native API on `:8088`, loaded model on `:39397` | unknown | unknown |
| OMP | `18.13.2` at `~/.bun/bin/omp`; local models defined | documented in handoff | unknown |
| OpenCode | `v2.2.0`; built-in `lmstudio` provider, no generic router provider | unknown | unknown |

The full evidence and exact diffs are in [`PROPOSAL.md`](PROPOSAL.md).

---

## Further reading

- [`docs/cursor-plus.md`](docs/cursor-plus.md) — the fixed Cursor Plus option
  matrix, including the `effort` vs `reasoning_effort` trap.
- [`docs/local-inference.md`](docs/local-inference.md) — why OpenCode needs a
  custom `qwen-local` provider, and how to own the GPU cleanly.
- [`docs/runbook.md`](docs/runbook.md) — per-machine apply, rollback, and
  troubleshooting.
- [`docs/security.md`](docs/security.md) — token exposure, redaction, and
  repository policy.
- [`docs/auto-sync.md`](docs/auto-sync.md) — automatic pull/apply on the
  laptop and server, including the one-time bootstrap.
