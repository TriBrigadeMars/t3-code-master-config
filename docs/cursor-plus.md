# Cursor Plus in T3 Code Nightly

Cursor Plus does not accept a bare model ID for every model. It requires the
fixed option values for the included model. T3 stores those values in
`favorites[].options` inside `~/.t3/userdata/settings.json`.

When the options are missing, Cursor returns:

```
Model unavailable on Plus Plus includes Composer 2.5, Grok 4.7, Grok 4.6,
and Grok 4.5 with fixed settings. Switch to an included model to continue.
```

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
