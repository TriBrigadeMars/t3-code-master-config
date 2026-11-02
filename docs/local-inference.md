# Local inference: one endpoint, three clients

## Roles

| Component | What it is | What it is not |
|---|---|---|
| **LM Studio Bionic** | Desktop GUI and model manager; optional native API on `:8088` | Not the canonical client endpoint |
| **Tuned Qwen router** | Custom `llama.cpp` `build-intelfa` server with DFlash2 | Not an LM Studio feature |
| **Tailscale gateway** | Desktop bridge on `100.95.87.91:1235` | Not a second model server |
| **Oh My Pi (OMP)** | Agent client | Not a server |
| **OpenCode** | Agent client | Not a server |
| **T3 Code Nightly** | Orchestrator/client | Not a model server |

The router and LM Studio are the two possible servers. OMP, OpenCode, and T3 are
clients. Do not treat OMP/OpenCode as local inference providers in their own
right; they only point at one.

## Endpoints

| Machine | Canonical endpoint | Role |
|---|---|---|
| `mars-desktop-cachy` | `http://127.0.0.1:1234/v1` | Router owner |
| `mars-laptop` | `http://100.95.87.91:1235/v1` | Gateway client |
| `mars-server` | `http://100.95.87.91:1235/v1` | Gateway client (provisional) |

The desktop gateway listens only on the desktop Tailscale IP and allows only
the laptop and desktop peers:

- `100.82.15.19` (laptop)
- `100.95.87.91` (desktop)

If the laptop cannot reach `:1235`, check the tailnet ACL for TCP 1235.

## Canonical model IDs

| API model ID | Weights | Notes |
|---|---|---|
| `qwen/qwen3.8-27b` | LM Studio Community Q4_K_M | Default |
| `qwen/qwen3.8-27b-unsloth` | Unsloth UD-IQ4_XS | + mmproj-F16 |
| `qwen/qwen3.8-27b-uncensored` | OrcaRouter Uncensored Q4_K_M | Local GGUF, not OpenRouter |

All three are vision-capable, served on one router, and use:

- `32768` context
- `4096` max output
- `medium` default thinking effort
- one loaded model at a time (`models_max: 1`)

## The OpenCode trap

OpenCode's built-in `lmstudio` provider is defined as:

```
origin:    http://127.0.0.1:1234
discovery: GET /api/v1/models
```

That is **LM Studio's native API**. The tuned router is a `llama.cpp` server and
exposes the OpenAI-compatible **`/v1/models`**. The built-in provider therefore
cannot discover the router; it sees LM Studio hashes when LM Studio is on
`:1234`, and nothing when the router is the owner.

The repo adds a separate provider:

```json
"qwen-local": {
  "name": "Local Qwen (tuned router)",
  "package": "@opencode/ai/providers/openai-compatible",
  "settings": { "baseURL": "http://127.0.0.1:1234/v1" },
  "models": { "qwen/qwen3.8-27b": { "...": "..." } }
}
```

This is the same provider package already used by the working `cliproxy` and
`mimo` blocks in `opencode.json`.

## The OMP discovery trap

OMP's `discovery.type: lm-studio` targets LM Studio's native API too. The router
serves `/v1/models`, so the generated OMP block uses:

```yaml
discovery:
  type: openai-models-list
```

Explicit model definitions remain authoritative; discovery is only a consistency
check.

## GPU ownership

The Intel Arc Pro B60 has 24 GB VRAM and the host has 32 GB RAM. Run one large
model server, not two.

### Recommended: router owns `:1234`

1. Unload LM Studio's loaded model, or quit LM Studio.
2. Start the router:
   ```bash
   systemctl --user start qwen-inference.service
   ```
3. Start the gateway:
   ```bash
   systemctl --user start qwen-laptop-gateway.service
   ```
4. Verify:
   ```bash
   curl -fsS http://127.0.0.1:1234/v1/models | jq -r '.data[].id'
   ```
5. Start T3/OMP/OpenCode.

The router loads the selected variant on demand. The first request can take
longer while the 27B model loads.

### Alternative: LM Studio owns `:8088`

Point every client at `http://127.0.0.1:8088/v1` instead. This loses the tuned
Intel flash-attention / DFlash2 stack and the clean model IDs. The repo does not
recommend this.

## The current failure mode

At audit time:

- `qwen-inference.service` was inactive/dead.
- `:1234` had no listener.
- LM Studio Bionic was running; `lms server status` reported `:8088`, and its
  child `llama-server` was on `:39397` serving a Qwen3.6-35B model.
- `qwen-laptop-gateway.service` was active, forwarding to the dead `:1234`.
- T3/OMP/OpenCode all pointed at `:1234`.

So the desktop clients had no server, and the laptop gateway had no upstream.
The fix is to start the owner and stop the competing model server, not to add
another client config.

## Diagnostic commands

```bash
# What is listening?
ss -ltnp | grep -E ':(1234|1235|8088|39397)\b'

# Is the router healthy?
curl -fsS http://127.0.0.1:1234/health
curl -fsS http://127.0.0.1:1234/v1/models | jq -r '.data[].id'

# Is the gateway healthy?
curl -fsS http://100.95.87.91:1235/health
curl -fsS http://100.95.87.91:1235/v1/models | jq -r '.data[].id'

# What is LM Studio doing?
~/.lmstudio/bin/lms server status
~/.lmstudio/bin/lms ps

# What are the systemd units doing?
systemctl --user status qwen-inference.service
systemctl --user status qwen-laptop-gateway.service
journalctl --user -u qwen-inference.service -n 50 --no-pager
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Connection refused` on `:1234` | Router not running | Start `qwen-inference.service` |
| `502` from `:1235` | Gateway is up, upstream dead | Start the router; gateway preflights upstream |
| T3 `opencode` shows `lmstudio/<hash>` | Built-in LM Studio provider is being used | Use `qwen-local/qwen/...`; apply the OpenCode provider |
| T3 `pi` shows old variants | OMP `models.yml` not updated | Apply the OMP component |
| OMP asks for 32K output | `maxTokens` still 32768 | Apply the OMP component (sets 4096) |
| CUDA/Vulkan OOM | LM Studio and router both loaded a model | Unload one; use one owner |
| Laptop cannot reach `:1235` | Tailscale ACL or desktop service down | Check tailnet ACL TCP 1235 and `qwen-laptop-gateway.service` |
| Router exits cleanly and stays down | Old unit used `Restart=on-failure` | The repo unit uses `Restart=always` |

## Startup order

1. Free the GPU (unload LM Studio's model or quit LM Studio).
2. `qwen-inference.service` (`:1234`).
3. `qwen-laptop-gateway.service` (`:1235`).
4. T3/OMP/OpenCode.

Do not start step 2 while LM Studio has a 27B/35B model resident unless you
intend to unload it.
