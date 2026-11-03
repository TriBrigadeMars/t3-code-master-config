# Cursor Plus in T3 Code Nightly

Cursor Plus does not accept a bare model ID for every model. It requires the
fixed option values for the included model. T3 stores those values in
`favorites[].options` inside `~/.t3/userdata/settings.json`.

When the options are missing, Cursor returns:

```
Model unavailable on Plus Plus includes Composer 2.5, Grok 4.7, Grok 4.6,
and Grok 4.5 with fixed settings. Switch to an included model to continue.
```

A favorite can also lose its options *after* being fixed: T3 rewrites
`settings.json` at runtime and does not always preserve `favorites[].options`.
This re-broke `mars-laptop` on 2026-11-02. Treat any optionless Cursor favorite
as a live defect; see [Guard](#the-guard) below.

## The exact T3 option matrix

| Model | T3 option ID | Value | Native Cursor parameter |
|---|---|---|---|
| `composer-2.5` | `fastMode` | `false` | `fast=false` |
| `grok-4.6` | `effort` | `medium` | `reasoning_effort=medium` |
| `grok-4.6` | `fastMode` | `false` | `fast=false` |
| `grok-4.7` | `contextWindow` | `256k` | `contextWindow=256k` |
| `grok-4.7` | `fastMode` | `false` | `fast=false` |
| `grok-4.7` | `reasoning_effort` | `medium` | `reasoning_effort=medium` |

The trap is the option name:

- **Grok 4.6 uses `effort`.**
- **Grok 4.7 uses `reasoning_effort`.**

They are not interchangeable. A shared preset that uses `effort` for both will
silently break Grok 4.7.

## Required settings shape

```json
{
  "provider": "cursor",
  "model": "composer-2.5",
  "options": [
    { "id": "fastMode", "value": "false" }
  ]
}
```

```json
{
  "provider": "cursor",
  "model": "grok-4.7",
  "options": [
    { "id": "contextWindow", "value": "256k" },
    { "id": "fastMode", "value": "false" },
    { "id": "reasoning_effort", "value": "medium" }
  ]
}
```

Grok 4.7 also must not be hidden:

```json
"cursor": {
  "hiddenModels": ["grok-4.5"]
}
```

`grok-4.5` may remain hidden. If you want to use it, remove it from
`profile.cursor.keep_hidden` and add `grok-4.5` to `profile.cursor.favorites`.
Its options are the same shape as Grok 4.6: `effort=medium`, `fastMode=false`.

## Why T3's option ID is `fastMode`, not `fast`

T3's provider descriptor calls the option `fastMode`. Its Cursor adapter maps it
to the Cursor Agent SDK's native `fast` parameter. Write `fastMode` in
`settings.json`; do not write `fast` there.

## The guard

`doctor` is a full audit (needs the endpoint reachable). `guard` is the narrow,
read-only regression tripwire for exactly this failure class:

```bash
./bin/t3cfg guard --profile laptop
```

It exits nonzero with the exact defect and the remediation command when any
Cursor favorite has missing/wrong `options`, or when `grok-4.7` is hidden. It
touches nothing and is safe to run while T3 is open — which matters, because
that is when T3 rewrites the file and reintroduces the defect.

Layers:

1. `apply` refuses to write any merged state whose Cursor favorites would
   trigger `Model unavailable on Plus` (defense-in-depth against a corrupted
   catalog).
2. `t3cfg-sync` runs `guard` on every pass — even when T3 settings are deferred
   while T3 is open — logs any drift, and `--strict` makes it a failure.
3. The timer re-applies T3 settings on the next pass after T3 quits, so an
   optionless favorite from a T3 rewrite is repaired automatically.

## Postmortem: 2026-11-02 mars-laptop

- Symptom: `composer-2.5` and `grok-4.7` both failed with the error above.
- Root cause: the laptop's Cursor favorites were stored without `options` at
  all (verified from `settings.json.bak-t3cfg-20261102T154913`).
- Why only that machine: the repo was fixed, but the laptop's live settings
  were still the pre-fix state, and the sync timer was not installed there.
- Re-break mechanism found later the same day: T3 rewrote `settings.json`
  while running and stripped `favorites[].options` again; `guard` caught it.
- Fix: apply `--only t3` with T3 quit; the timer + guard loop keeps it fixed.

## Apply and verify

Quit T3 first; it can rewrite `settings.json` while running.

```bash
./bin/t3cfg plan  --profile desktop
./bin/t3cfg apply --profile desktop --only t3
./bin/t3cfg doctor --profile desktop
```

The doctor checks:

- Favorite exists for `composer-2.5`, `grok-4.6`, and `grok-4.7`.
- Each `options` array exactly matches the catalog.
- `grok-4.7` is not in `hiddenModels`.
- Recent provider logs do not contain the `Model unavailable on Plus` error
  (warning only).

Then run one short prompt on each model in T3. T3 uses its bundled Cursor Agent
SDK; a separate `cursor-agent` binary is not required for T3.

## If Cursor changes the requirements

Cursor can change the fixed option descriptors. Capture the new descriptors from
a recent T3 provider log (search for the model slug), update
`catalog/cursor-plus.json`, and rerun the tests. Do not guess the option IDs.
