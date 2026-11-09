# Local inference: one engine, three clients

LM Studio Bionic on the desktop is the only local model engine. Every machine's
OMP, OpenCode and T3 point at Bionic's OpenAI-compatible API. Nothing else on
any machine loads local models for these clients.

The custom `llama.cpp` router (`qwen-inference.service`) and its Tailscale
gateway (`qwen-laptop-gateway.service`) were removed on 2026-11-08. They ran a
second engine on the same 24 GB GPU, which pushed Bionic's models out of VRAM.

## Roles

| Component | What it is |
|---|---|
| **LM Studio Bionic** (desktop) | The model engine and the only server |
| **Oh My Pi (OMP)** | Agent client |
| **OpenCode** | Agent client |
| **T3 Code Nightly** | Orchestrator and client |

## Endpoint

Every machine uses the same URL:

```
http://100.95.87.91:8088/v1
```

Bionic's server binds to the desktop's Tailscale address only (Bionic
**Developer → Server settings**: port `8088`, network interface `100.95.87.91`,
just-in-time loading on). It does not listen on `127.0.0.1`, so desktop clients
use the Tailscale address too. Any device on the tailnet can reach it; there is
no per-peer allow-list.

| Machine | Profile | Endpoint |
|---|---|---|
| `mars-desktop-cachy` | `desktop` | `http://100.95.87.91:8088/v1` |
| `mars-laptop` | `laptop` | `http://100.95.87.91:8088/v1` |
| `amc-5rcsfw3` (AMC work laptop) | `work` | `http://100.95.87.91:8088/v1` |

## Models

The ids are the ones Bionic's `/v1/models` reports. Two of them are snapshot
hashes, because that is how Bionic names files it found in the Hugging Face cache.

| Variant | Bionic model id | File | Vision |
|---|---|---|---|
| `q4` (default) | `qwen/qwen3.8-27b` | LM Studio Community Q4_K_M | yes |
| `swift` | `14bfe4b42be4a925d98816db830155f476c605e7` | Swift 1.5 Qwen3.8 27B Q4_K_S | yes |
| `orcarouter` | `58ebd123013160600229eda180b5b17f3fb7af9d` | Qwen3.8 27B Uncensored OrcaRouter Q4_K_M | no |

Clients use a `32768` context window and a `4096` output cap. Bionic loads a
model with its own saved default context (`qwen/qwen3.8-27b` loaded at `40000`
on 2026-11-08), so 32,768 leaves headroom. Raise `context_window` in the
profiles only after checking the loaded context with `lms ps`.

## GPU sharing

Bionic manages the GPU for everything:

- A request for a model that is not loaded loads it on demand.
- Loading an on-demand model unloads the previous on-demand model
  (`unloadPreviousJITModelOnLoad`).
- An idle on-demand model unloads after Bionic's TTL (one hour).
- A request for a model that is already loaded, for example one you opened in a
  Bionic chat, reuses that instance.

Other apps that load models on their own, such as Unsloth Studio, are outside
Bionic's control and can still exhaust VRAM. Unload them before long local runs.

## OpenCode and OMP details

- OpenCode uses the `qwen-local` provider (`@opencode/ai/providers/openai-compatible`)
  with the Bionic URL. Its built-in `lmstudio` provider assumes `127.0.0.1:1234`
  and is not used; T3 favorites starting with `lmstudio/` are removed on apply.
- OMP's `lm-studio` provider uses `discovery.type: openai-models-list`, which
  reads Bionic's `/v1/models`.

## Diagnostic commands

```bash
# Is Bionic serving, and which models does it advertise?
curl -fsS http://100.95.87.91:8088/v1/models | jq -r '.data[].id'

# What is loaded, with which context, and when does it unload?
lms ps
curl -fsS http://100.95.87.91:8088/api/v1/models | jq '.models[] | select(.loaded_instances != []) | {key, loaded_instances}'

# Which of this machine's variants does Bionic advertise?
omp-qwen --check

# Full check of this machine's files plus the endpoint
./bin/t3cfg doctor --profile <desktop|laptop|work>
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Connection refused` on `:8088` | Bionic is not running, or Tailscale is down on the desktop | Start Bionic (it autostarts at login); check `tailscale status` |
| First request takes minutes | Bionic is loading a 17 GB model | Wait; later requests are fast until the TTL unloads it |
| `omp-qwen --check` shows `advertised: false` | Model id changed in Bionic | Update `catalog/qwen-local.json` from `/v1/models` |
| T3 shows `lm-studio/qwen/qwen3.8-27b-unsloth` or `-uncensored` | Old favorites from the removed router | Quit and reopen T3 so the launch apply removes them |
| VRAM spill or very slow prompts | Another app (e.g. Unsloth Studio) holds a model | Unload it in that app |
| Context-length error from a client | Bionic loaded the model with less context than the client assumes | Check `lms ps`; lower `context_window` or raise Bionic's default |
