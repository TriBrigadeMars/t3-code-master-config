# Security notes

## The repository contains no secrets

The repo stores profiles, catalogs, templates, and scripts. It does not store:

- API keys
- OAuth access/refresh tokens
- Cursor credentials
- OpenCode provider headers
- OMP provider API keys
- T3 session databases
- model weights

`t3cfg snapshot` writes redacted copies. `t3cfg apply` writes only the local
provider blocks and model preferences; it does not copy provider headers or keys
from the repo because there are none.

## Known exposure on the desktop

`~/.omp/agent/models.yml` currently contains a plaintext GitHub token in the
`unsloth-studio` and `copilot` provider blocks. Treat that token as exposed:

1. Revoke/rotate it in GitHub.
2. Remove it from `models.yml` or move it to an environment/secret store.
3. Check backups and logs for copies.
4. Do not commit the file.

The generated OMP block uses no token. The apply step preserves unrelated
provider blocks, so it does not silently delete the old token; rotate it
separately.

`~/.config/opencode/opencode.json` also contains inline provider keys for
providers such as `mimo` and `openrouter`. The repo only adds
`providers.qwen-local`; it preserves the existing file and its keys. Do not copy
that file into the repo.

## Redaction

`t3cfg snapshot`:

- Replaces values for keys matching `apiKey`, `api_key`, `token`,
  `authorization`, `password`, `secret`, and `credential`.
- Replaces token-shaped strings such as GitHub, `sk-`, and `tp-` values.
- Redacts YAML lines whose keys are `apiKey`, `token`, `authorization`,
  `password`, or `secret`.

Redaction is best-effort. Review a snapshot before sharing it.

## Bionic exposure

LM Studio Bionic's server binds to the desktop's Tailscale address
(`100.95.87.91:8088`) and has authentication off. It is not a public endpoint, but
every device on the tailnet can use it. To narrow that, either restrict TCP 8088
in the tailnet ACL to the three T3 machines, or turn on **Require authentication**
in Bionic's server settings and add the key to each client's provider config
(outside this repo).

## File permissions

The applier writes:

- T3 `settings.json` as mode `0600` when it creates/replaces it
- OMP `models.yml` as `0600`
- OpenCode `opencode.json` as `0600`
- the `omp-qwen` launcher as `0755`

Backups inherit the original file mode via `shutil.copy2`.

## CI

CI renders all three profiles and scans generated files for token-shaped values.
It does not read a live machine's config and cannot detect a token that only
exists in an untracked local file. Run `t3cfg doctor` on each machine after
changes.
