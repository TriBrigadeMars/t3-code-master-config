#!/usr/bin/env python3
"""t3cfg — render, apply, and verify the T3 Code master configuration.

Stdlib only. The repository never stores provider keys, tokens, or session data.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles"
CATALOG = ROOT / "catalog"
TEMPLATES = ROOT / "templates"
BUILD = ROOT / "build"
SNAPSHOTS = ROOT / "snapshots"

TOKEN_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|tp-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,})"
)
SECRET_KEY_RE = re.compile(r"(api[-_]?key|token|secret|password|authorization|credential)", re.I)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def expand_path(value: str, home: Path | None = None) -> Path:
    if value is None:
        raise ValueError("path is None")
    if home is not None and value.startswith("~"):
        value = str(home) + value[1:]
    value = os.path.expanduser(value)
    value = re.sub(
        r"%([^%]+)%",
        lambda match: os.environ.get(match.group(1), match.group(0)),
        value,
    )
    value = os.path.expandvars(value)
    return Path(value)


def profile_names() -> list[str]:
    return sorted(path.stem for path in PROFILES.glob("*.json"))


def load_profile(name: str) -> dict:
    path = PROFILES / f"{name}.json"
    if not path.is_file():
        raise SystemExit(f"unknown profile {name!r}; choose from: {', '.join(profile_names())}")
    return load_json(path)


def load_catalogs() -> tuple[dict, dict]:
    return load_json(CATALOG / "cursor-plus.json"), load_json(CATALOG / "qwen-local.json")


def write_text(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%dT%H%M%S")
    target = path.with_name(path.name + f".bak-t3cfg-{stamp}")
    shutil.copy2(path, target)
    return target


def copy_with_backup(src: Path, dst: Path, mode: int | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and filecmp.cmp(src, dst, shallow=False):
        if mode is not None:
            dst.chmod(mode)
        return
    backup(dst)
    shutil.copy2(src, dst)
    if mode is not None:
        dst.chmod(mode)


def t3_running() -> bool:
    if os.name == "nt":
        try:
            output = subprocess.run(
                ["tasklist"], capture_output=True, text=True, timeout=10
            ).stdout.lower()
            return "t3code" in output or "t3 code" in output
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        result = subprocess.run(
            ["pgrep", "-x", "t3code"], capture_output=True, text=True, timeout=5
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def service_active(name: str) -> str:
    if os.name == "nt":
        return "unknown"
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def http_json(url: str, timeout: float = 5.0):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def unified_diff(before: str, after: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{label} (current)",
            tofile=f"{label} (desired)",
        )
    )


# ---------------------------------------------------------------------------
# Generation


def build_t3_patch(profile: dict, home: Path | None = None) -> dict:
    cursor, qwen = load_catalogs()
    favorites = []
    for model in profile["cursor"]["favorites"]:
        spec = cursor["models"][model]
        favorites.append(
            {
                "provider": "cursor",
                "model": model,
                "options": copy.deepcopy(spec["options"]),
            }
        )

    if "pi" in profile["t3"]["local_providers"]:
        for model in qwen["models"]:
            favorites.append(
                {
                    "provider": "pi",
                    "model": f"{qwen['providers']['omp']}/{model['id']}",
                }
            )

    if "opencode" in profile["t3"]["local_providers"]:
        for model in qwen["models"]:
            if not model.get("opencode_favorite"):
                continue
            favorites.append(
                {
                    "provider": "opencode",
                    "model": f"{profile['t3']['opencode_provider']}/{model['id']}",
                }
            )

    patch = {
        "favorites": favorites,
        "favoritesRemove": [
            {"provider": "opencode", "modelPrefix": "lmstudio/"},
        ],
        "providerModelPreferences": {
            "cursor": {
                "removeHidden": ["grok-4.7"],
                "addHidden": profile["cursor"].get("keep_hidden", []),
            },
            "pi": {
                "removeHidden": [
                    f"{qwen['providers']['omp']}/{model['id']}" for model in qwen["models"]
                ]
            },
            "opencode": {
                "removeHidden": [
                    f"{profile['t3']['opencode_provider']}/{model['id']}"
                    for model in qwen["models"]
                ]
            },
        },
        "providerInstances": {},
    }

    if profile["t3"].get("pi_binary"):
        patch["providerInstances"]["pi"] = {
            "config": {
                "binaryPath": str(expand_path(profile["t3"]["pi_binary"], home))
            }
        }

    if profile["t3"].get("set_default"):
        patch["defaultModelSelection"] = copy.deepcopy(profile["t3"]["default_model"])

    return patch


def build_omp_models(profile: dict) -> str:
    _, qwen = load_catalogs()
    endpoint = profile["qwen"]["endpoint"]
    context = profile["qwen"]["context_window"]
    output = profile["qwen"]["max_output_tokens"]
    effort = profile["qwen"]["default_effort"]
    lines = [
        "providers:",
        "  lm-studio:",
        f"    baseUrl: {endpoint}",
        "    api: openai-completions",
        "    auth: none",
        "    discovery:",
        "      type: openai-models-list",
        "    models:",
    ]
    for model in qwen["models"]:
        lines += [
            f"    - id: {model['id']}",
            f"      name: {model['name']}",
            f"      baseUrl: {endpoint}",
            "      api: openai-completions",
            "      reasoning: true",
            "      input:",
            "      - text",
            "      - image",
            "      imageInputDecoder: stb",
            f"      contextWindow: {context}",
            f"      maxTokens: {output}",
            "      cost:",
            "        input: 0",
            "        output: 0",
            "        cacheRead: 0",
            "        cacheWrite: 0",
            "      thinking:",
            "        mode: effort",
            "        efforts:",
            "        - low",
            "        - medium",
            "        - xhigh",
            f"        defaultLevel: {effort}",
            "        requiresEffort: false",
            "      compat:",
            "        thinkingFormat: qwen-chat-template",
            "        qwenTemplateReasoningEffort: true",
            "        reasoningEffortMap:",
            "          minimal: low",
            "          low: low",
            "          medium: medium",
            "          high: xhigh",
            "          xhigh: xhigh",
            "          max: xhigh",
            "      tokenizer: qwen3",
        ]
    return "\n".join(lines) + "\n"


def build_omp_config(profile: dict) -> str:
    _, qwen = load_catalogs()
    template = Template((TEMPLATES / "omp/config.yml.tmpl").read_text(encoding="utf-8"))
    return template.substitute(
        DEFAULT_MODEL=f"{qwen['providers']['omp']}/{qwen['canonical']}",
        DEFAULT_EFFORT=profile["qwen"]["default_effort"],
    )


def variant_models() -> dict[str, str]:
    _, qwen = load_catalogs()
    return {model["variant"]: model["id"] for model in qwen["models"]}


def build_variant(variant: str, model_id: str, provider: str) -> str:
    selector = f"{provider}/{model_id}"
    return (
        "modelRoles:\n"
        f"  default: {selector}\n"
        f"  smol: {selector}\n"
        f"  slow: {selector}\n"
        f"  plan: {selector}\n"
        f"  vision: {selector}\n"
        f"  advisor: {selector}\n"
    )


def build_qwen_env(profile: dict) -> str:
    _, qwen = load_catalogs()
    variants = variant_models()
    lines = [
        "# Generated by t3cfg. No secrets.",
        f"OMP_QWEN_BASE_URL={profile['qwen']['endpoint']}",
        f"OMP_QWEN_AGENT_DIR={profile['paths']['omp_profile']}",
        f"OMP_QWEN_OMP_BIN={profile['t3']['pi_binary']}",
        f"OMP_QWEN_CONTEXT_WINDOW={profile['qwen']['context_window']}",
        f"OMP_QWEN_MAX_OUTPUT_TOKENS={profile['qwen']['max_output_tokens']}",
        "OMP_QWEN_TOOLS=read,bash,edit,write,glob,grep,context_notes,new_context,goal",
        f"OMP_QWEN_MODEL_Q4={variants['q4']}",
        f"OMP_QWEN_MODEL_UNSLOTH={variants['unsloth']}",
        f"OMP_QWEN_MODEL_ORCAROUTER={variants['orcarouter']}",
        "",
    ]
    return "\n".join(lines)


def build_opencode_patch(profile: dict) -> dict:
    _, qwen = load_catalogs()
    provider_id = profile["t3"]["opencode_provider"]
    models = {}
    for model in qwen["models"]:
        models[model["id"]] = {
            "name": model["name"],
            "limit": {
                "context": profile["qwen"]["context_window"],
                "output": profile["qwen"]["max_output_tokens"],
            },
            "capabilities": {
                "tools": True,
                "input": ["text", "image"],
                "output": ["text"],
            },
        }
    return {
        "providers": {
            provider_id: {
                "name": "Local Qwen (tuned router)",
                "package": "@opencode/ai/providers/openai-compatible",
                "settings": {
                    "baseURL": profile["qwen"]["endpoint"],
                    "headerTimeout": 900000,
                    "chunkTimeout": 1800000,
                    "timeout": 3600000,
                },
                "models": models,
            }
        }
    }


def build_qwen_files(profile: dict) -> dict[str, str]:
    if profile["qwen"]["role"] != "server":
        return {}
    _, qwen = load_catalogs()
    server = profile["qwen"]["server"]
    server_json = {
        "id": "qwen3.8-family-router",
        "binary": str(expand_path(server["binary"])),
        "port": server["port"],
        "kv": "f16",
        "spec": "draft-dflash",
        "draft": server["draft"],
        "draft_model": str(expand_path(server["draft_model"])),
        "mmvq": False,
        "pmin": server["pmin"],
        "ubatch": server["ubatch"],
        "parallel": server["parallel"],
        "profile": "performance",
        "models_preset": str(expand_path(server["models_preset"])),
        "models_max": server["models_max"],
    }
    preset_lines = [
        "; Generated by t3cfg from catalog/qwen-local.json.",
        "; Shared router flags live in serve.py and server.json.",
        "",
    ]
    for model in qwen["models"]:
        preset_lines += [
            f"[{model['id']}]",
            f"model = {expand_path(model['weights'])}",
            f"mmproj = {expand_path(model['mmproj'])}",
            "",
        ]
    gateway = (TEMPLATES / "qwen/tailnet-gateway.py").read_text(encoding="utf-8")
    gateway_cfg = profile["qwen"]["gateway"]
    peers = ",".join(gateway_cfg["allowed_peers"])
    inference_unit = Template(
        (TEMPLATES / "systemd/qwen-inference.service.tmpl").read_text(encoding="utf-8")
    ).substitute(QWEN_PORT=server["port"])
    gateway_unit = Template(
        (TEMPLATES / "systemd/qwen-laptop-gateway.service.tmpl").read_text(encoding="utf-8")
    ).substitute(
        LISTEN_HOST=gateway_cfg["listen_host"],
        LISTEN_PORT=gateway_cfg["listen_port"],
        QWEN_PORT=server["port"],
        ALLOWED_PEERS=peers,
    )
    gateway_env = "\n".join(
        [
            "# Generated by t3cfg. Consumed by the gateway unit.",
            f"QWEN_GATEWAY_LISTEN_HOST={gateway_cfg['listen_host']}",
            f"QWEN_GATEWAY_LISTEN_PORT={gateway_cfg['listen_port']}",
            "QWEN_GATEWAY_UPSTREAM_HOST=127.0.0.1",
            f"QWEN_GATEWAY_UPSTREAM_PORT={server['port']}",
            f"QWEN_GATEWAY_ALLOWED_PEERS={peers}",
            "",
        ]
    )
    return {
        "qwen/server.json": dump_json(server_json),
        "qwen/models-preset.ini": "\n".join(preset_lines),
        "qwen/tailnet-gateway.py": gateway,
        "qwen/qwen-gateway.env": gateway_env,
        "systemd/qwen-inference.service": inference_unit,
        "systemd/qwen-laptop-gateway.service": gateway_unit,
    }


def render_profile(profile: dict, out_dir: Path | None = None) -> Path:
    out = Path(out_dir) if out_dir else BUILD / profile["id"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    write_text(out / "t3/settings.patch.json", dump_json(build_t3_patch(profile)))
    write_text(out / "omp/models.yml", build_omp_models(profile))
    write_text(out / "omp/default/models.yml", build_omp_models(profile))
    write_text(out / "omp/qwen/models.yml", build_omp_models(profile))
    write_text(out / "omp/qwen/config.yml", build_omp_config(profile))
    shutil.copy2(TEMPLATES / "omp/SYSTEM.md", out / "omp/qwen/SYSTEM.md")
    _, qwen = load_catalogs()
    for model in qwen["models"]:
        write_text(
            out / f"omp/qwen/variant-{model['variant']}.yml",
            build_variant(model["variant"], model["id"], qwen["providers"]["omp"]),
        )
    write_text(out / "omp/qwen/qwen.env", build_qwen_env(profile))
    write_text(out / "opencode/provider.patch.json", dump_json(build_opencode_patch(profile)))

    for relative, content in build_qwen_files(profile).items():
        write_text(out / relative, content)

    files = sorted(str(path.relative_to(out)) for path in out.rglob("*") if path.is_file())
    write_text(
        out / "manifest.json",
        dump_json(
            {
                "profile": profile["id"],
                "hostname": profile["hostname"],
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": files,
            }
        ),
    )
    return out


# ---------------------------------------------------------------------------
# Merging


def deep_merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def merge_t3_settings(settings: dict, patch: dict) -> tuple[dict, list[str]]:
    result = copy.deepcopy(settings)
    changes: list[str] = []
    favorites = result.setdefault("favorites", [])

    for rule in patch.get("favoritesRemove", []):
        provider = rule.get("provider")
        prefix = rule.get("modelPrefix")
        exact = rule.get("model")
        kept = []
        for favorite in favorites:
            matches = favorite.get("provider") == provider and (
                (prefix is not None and str(favorite.get("model", "")).startswith(prefix))
                or (exact is not None and favorite.get("model") == exact)
            )
            if matches:
                changes.append(f"remove favorite {provider}/{favorite.get('model')}")
            else:
                kept.append(favorite)
        favorites[:] = kept

    for favorite in patch.get("favorites", []):
        key = (favorite.get("provider"), favorite.get("model"))
        index = next(
            (
                i
                for i, existing in enumerate(favorites)
                if (existing.get("provider"), existing.get("model")) == key
            ),
            None,
        )
        if index is None:
            favorites.append(copy.deepcopy(favorite))
            changes.append(f"add favorite {favorite['provider']}/{favorite['model']}")
        else:
            if favorites[index] != favorite:
                changes.append(f"update favorite {favorite['provider']}/{favorite['model']}")
            favorites[index] = copy.deepcopy(favorite)

    prefs = result.setdefault("providerModelPreferences", {})
    for provider, rules in patch.get("providerModelPreferences", {}).items():
        entry = prefs.setdefault(provider, {})
        hidden = entry.setdefault("hiddenModels", [])
        for model in rules.get("removeHidden", []):
            if model in hidden:
                hidden.remove(model)
                changes.append(f"unhide {provider}/{model}")
        for model in rules.get("addHidden", []):
            if model not in hidden:
                hidden.append(model)
                changes.append(f"hide {provider}/{model}")

    instances = result.setdefault("providerInstances", {})
    deep_merge(instances, patch.get("providerInstances", {}))

    if "defaultModelSelection" in patch:
        if result.get("defaultModelSelection") != patch["defaultModelSelection"]:
            changes.append("set defaultModelSelection")
        result["defaultModelSelection"] = copy.deepcopy(patch["defaultModelSelection"])

    result["favorites"] = favorites
    return result, changes


def merge_opencode_config(config: dict, patch: dict) -> dict:
    result = copy.deepcopy(config)
    providers = result.setdefault("providers", {})
    for provider_id, block in patch.get("providers", {}).items():
        providers[provider_id] = copy.deepcopy(block)
    return result


CURSOR_PLUS_REMEDIATION = (
    "Fix with: quit T3, then ./bin/t3cfg apply --profile <profile> --only t3; "
    "verify with ./bin/t3cfg guard --profile <profile>"
)


def cursor_plus_defects(settings: dict, cursor: dict) -> list[str]:
    """Detect every shape that makes Cursor Plus reject a model.

    This is the 2026-11-02 mars-laptop outage class: a cursor favorite stored
    without its fixed option values (or with wrong ones), or grok-4.7 left
    hidden. Read-only; safe to run while T3 is open.
    """
    defects: list[str] = []
    for item in settings.get("favorites", []):
        if item.get("provider") != "cursor":
            continue
        model = item.get("model")
        spec = cursor["models"].get(model)
        if spec is None:
            continue
        expected = spec["options"]
        actual = item.get("options")
        if actual != expected:
            defects.append(
                f"cursor favorite {model} has options {actual!r}, "
                f"expected {expected!r} (missing/wrong fixed options cause "
                "'Model unavailable on Plus')"
            )
    hidden = (
        settings.get("providerModelPreferences", {})
        .get("cursor", {})
        .get("hiddenModels", [])
    )
    if "grok-4.7" in hidden:
        defects.append(
            "grok-4.7 is in providerModelPreferences.cursor.hiddenModels; "
            "it cannot be used while hidden"
        )
    return defects


def omp_lmstudio_block(profile: dict) -> str:
    generated = build_omp_models(profile).splitlines()
    start = next(i for i, line in enumerate(generated) if line.strip() == "lm-studio:")
    return "\n".join(generated[start:]) + "\n"


def merge_omp_models(existing: str, block: str) -> str:
    lines = existing.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^  lm-studio:\s*$", line):
            start = index
            break
    new_block = block.rstrip("\n").splitlines()

    if start is not None:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if not line.strip():
                continue
            if re.match(r"^  [A-Za-z0-9_.-]+:", line) or re.match(r"^[^\s#]", line):
                end = index
                break
        lines[start:end] = new_block
        return "\n".join(lines) + "\n"

    provider_index = next(
        (i for i, line in enumerate(lines) if re.match(r"^providers:\s*$", line)),
        None,
    )
    if provider_index is None:
        return "providers:\n" + block.rstrip("\n") + "\n" + ("\n".join(lines) + "\n" if lines else "")
    lines[provider_index + 1 : provider_index + 1] = new_block
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Apply


def find_t3_settings(profile: dict, override: str | None = None) -> Path:
    if override:
        path = expand_path(override)
        return path if path.name == "settings.json" else path / "settings.json"
    return expand_path(profile["paths"]["t3_userdata"]) / "settings.json"


def apply_t3(profile: dict, patch: dict, dry_run: bool, force: bool, override: str | None) -> None:
    path = find_t3_settings(profile, override)
    if not path.is_file():
        raise SystemExit(
            f"T3 settings not found at {path}. Run T3 once, or pass --t3-userdata <dir>."
        )
    if not dry_run and t3_running() and not force:
        raise SystemExit(
            "T3 is running. Quit T3 before applying settings, or pass --force. "
            "T3 may overwrite settings.json while it is open."
        )
    current = load_json(path)
    merged, changes = merge_t3_settings(current, patch)
    merged_defects = cursor_plus_defects(merged, load_catalogs()[0])
    if merged_defects:
        detail = "\n  - ".join(merged_defects)
        if dry_run:
            print(f"[dry-run] WARNING: merged state would still break Cursor Plus:\n  - {detail}")
        else:
            raise SystemExit(
                "refusing to write T3 settings: the merged state would still "
                f"trigger 'Model unavailable on Plus':\n  - {detail}\n"
                f"Catalog/applier invariant violated; {CURSOR_PLUS_REMEDIATION}"
            )
    before = dump_json(current)
    after = dump_json(merged)
    if before != after:
        print(unified_diff(before, after, str(path)))
        for change in changes:
            print(f"  - {change}")
    else:
        print(f"T3 settings already match {profile['id']}.")
    if dry_run:
        print(f"[dry-run] would write {path}")
        return
    backup_path = backup(path)
    temporary = path.with_name(path.name + ".t3cfg.tmp")
    mode = path.stat().st_mode & 0o777
    temporary.write_text(after, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)
    print(f"wrote {path}" + (f" (backup: {backup_path.name})" if backup_path else ""))


def apply_omp(profile: dict, dry_run: bool) -> None:
    rendered = render_profile(profile)
    agent_models = expand_path(profile["paths"]["omp_agent"]) / "models.yml"
    profile_dir = expand_path(profile["paths"]["omp_profile"])
    state_dir = expand_path(profile["paths"]["state"])
    bin_dir = expand_path(profile["paths"].get("bin", "~/.local/bin"))
    block = omp_lmstudio_block(profile)

    if agent_models.is_file():
        existing = agent_models.read_text(encoding="utf-8")
        merged = merge_omp_models(existing, block)
    else:
        merged = "providers:\n" + block

    actions = [
        (agent_models, merged, None),
        (profile_dir / "config.yml", (rendered / "omp/qwen/config.yml").read_text(encoding="utf-8"), None),
        (profile_dir / "models.yml", (rendered / "omp/qwen/models.yml").read_text(encoding="utf-8"), None),
        (profile_dir / "SYSTEM.md", (rendered / "omp/qwen/SYSTEM.md").read_text(encoding="utf-8"), None),
        (state_dir / "qwen.env", (rendered / "omp/qwen/qwen.env").read_text(encoding="utf-8"), None),
        (bin_dir / "omp-qwen", (TEMPLATES / "omp/omp-qwen.py").read_text(encoding="utf-8"), 0o755),
    ]
    for variant in variant_models():
        actions.append(
            (
                profile_dir / f"variant-{variant}.yml",
                (rendered / f"omp/qwen/variant-{variant}.yml").read_text(encoding="utf-8"),
                None,
            )
        )

    for path, content, mode in actions:
        if dry_run:
            print(f"[dry-run] would write {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == content:
            if mode is not None:
                path.chmod(mode)
            print(f"unchanged {path}")
            continue
        backup(path)
        path.write_text(content, encoding="utf-8")
        if mode is not None:
            path.chmod(mode)
        print(f"wrote {path}")


def apply_opencode(profile: dict, patch: dict, dry_run: bool) -> None:
    path = expand_path(profile["paths"]["opencode"])
    if not path.is_file():
        raise SystemExit(f"OpenCode config not found at {path}.")
    current = load_json(path)
    merged = merge_opencode_config(current, patch)
    before = dump_json(current)
    after = dump_json(merged)
    if before != after:
        print(unified_diff(before, after, str(path)))
    else:
        print("OpenCode provider already matches.")
    if dry_run:
        print(f"[dry-run] would write {path}")
        return
    backup_path = backup(path)
    mode = path.stat().st_mode & 0o777
    temporary = path.with_name(path.name + ".t3cfg.tmp")
    temporary.write_text(after, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)
    print(f"wrote {path}" + (f" (backup: {backup_path.name})" if backup_path else ""))
    print("Restart T3/OpenCode so the provider list is reloaded.")


def apply_qwen(profile: dict, dry_run: bool) -> None:
    if profile["qwen"]["role"] != "server":
        print("Qwen router files are only generated for qwen.role=server; skipping.")
        return
    rendered = render_profile(profile)
    qwen_config = expand_path(profile["paths"]["qwen_config"])
    qwen_share = expand_path(profile["paths"]["qwen_share"])
    systemd = Path.home() / ".config/systemd/user"
    state = expand_path(profile["paths"]["state"])
    actions = [
        (qwen_config / "server.json", rendered / "qwen/server.json", None),
        (qwen_config / "models-preset.ini", rendered / "qwen/models-preset.ini", None),
        (qwen_share / "tailnet-gateway.py", rendered / "qwen/tailnet-gateway.py", 0o755),
        (systemd / "qwen-inference.service", rendered / "systemd/qwen-inference.service", None),
        (systemd / "qwen-laptop-gateway.service", rendered / "systemd/qwen-laptop-gateway.service", None),
        (state / "qwen-gateway.env", rendered / "qwen/qwen-gateway.env", None),
    ]
    for target, source, mode in actions:
        if dry_run:
            print(f"[dry-run] would write {target}")
            continue
        copy_with_backup(source, target, mode)
        print(f"wrote {target}")
    if not dry_run:
        print(
            "Next: systemctl --user daemon-reload && "
            "systemctl --user enable --now qwen-inference.service qwen-laptop-gateway.service"
        )


# ---------------------------------------------------------------------------
# Verify


def favorite(settings: dict, provider: str, model: str) -> dict | None:
    for item in settings.get("favorites", []):
        if item.get("provider") == provider and item.get("model") == model:
            return item
    return None


def doctor(profile: dict, as_json: bool) -> int:
    cursor, qwen = load_catalogs()
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str = "") -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    settings_path = find_t3_settings(profile)
    settings = None
    if not settings_path.is_file():
        add("T3 settings file", "FAIL", f"missing {settings_path}")
    else:
        try:
            settings = load_json(settings_path)
            add("T3 settings file", "PASS", str(settings_path))
        except (OSError, ValueError) as error:
            add("T3 settings file", "FAIL", str(error))

    if settings:
        for model in profile["cursor"]["favorites"]:
            item = favorite(settings, "cursor", model)
            expected = cursor["models"][model]["options"]
            if item is None:
                add(f"Cursor {model}", "FAIL", "favorite missing")
            elif item.get("options") != expected:
                add(f"Cursor {model}", "FAIL", f"options are {item.get('options')!r}")
            else:
                add(f"Cursor {model}", "PASS", "fixed Plus options present")

        hidden = (
            settings.get("providerModelPreferences", {})
            .get("cursor", {})
            .get("hiddenModels", [])
        )
        if "grok-4.7" in hidden:
            add("Cursor Grok 4.7 visible", "FAIL", "still in hiddenModels")
        else:
            add("Cursor Grok 4.7 visible", "PASS")

        for model in qwen["models"]:
            selector = f"{qwen['providers']['omp']}/{model['id']}"
            add(
                f"T3 pi {model['variant']}",
                "PASS" if favorite(settings, "pi", selector) else "WARN",
                selector,
            )
        opencode_model = (
            f"{profile['t3']['opencode_provider']}/{qwen['canonical']}"
        )
        add(
            f"T3 opencode {qwen['canonical']}",
            "PASS" if favorite(settings, "opencode", opencode_model) else "WARN",
            opencode_model,
        )

        configured_binary = (
            settings.get("providerInstances", {})
            .get("pi", {})
            .get("config", {})
            .get("binaryPath")
        )
        expected_binary = (
            str(expand_path(profile["t3"]["pi_binary"]))
            if profile["t3"].get("pi_binary")
            else None
        )
        if configured_binary != expected_binary:
            add("OMP binary path", "WARN", f"{configured_binary!r} != {expected_binary!r}")
        elif expected_binary and expand_path(expected_binary).exists():
            add("OMP binary path", "PASS", expected_binary)
        else:
            add("OMP binary path", "WARN", f"not found: {expected_binary}")

        if profile["t3"].get("set_default"):
            expected_default = profile["t3"]["default_model"]
            if settings.get("defaultModelSelection") != expected_default:
                add("T3 default model", "WARN", "profile default not applied")
            else:
                add("T3 default model", "PASS")

    agent_models = expand_path(profile["paths"]["omp_agent"]) / "models.yml"
    if not agent_models.is_file():
        add("OMP models", "FAIL", f"missing {agent_models}")
    else:
        text = agent_models.read_text(encoding="utf-8")
        missing = [m["id"] for m in qwen["models"] if m["id"] not in text]
        if missing:
            add("OMP models", "FAIL", "missing " + ", ".join(missing))
        elif profile["qwen"]["endpoint"] not in text:
            add("OMP endpoint", "FAIL", profile["qwen"]["endpoint"])
        elif "maxTokens: 4096" not in text:
            add("OMP output cap", "FAIL", "expected maxTokens: 4096")
        else:
            add("OMP models", "PASS")
        add(
            "OMP discovery",
            "PASS" if "openai-models-list" in text else "WARN",
            "expects /v1/models",
        )

    profile_dir = expand_path(profile["paths"]["omp_profile"])
    for name in ["config.yml", "models.yml", "SYSTEM.md", "variant-q4.yml", "variant-unsloth.yml", "variant-orcarouter.yml"]:
        add(
            f"OMP profile {name}",
            "PASS" if (profile_dir / name).is_file() else "WARN",
            str(profile_dir / name),
        )

    opencode_path = expand_path(profile["paths"]["opencode"])
    if not opencode_path.is_file():
        add("OpenCode config", "FAIL", f"missing {opencode_path}")
    else:
        try:
            config = load_json(opencode_path)
            provider_id = profile["t3"]["opencode_provider"]
            block = config.get("providers", {}).get(provider_id)
            if block is None:
                add("OpenCode qwen provider", "FAIL", f"missing providers.{provider_id}")
            else:
                base = block.get("settings", {}).get("baseURL", "")
                if base.rstrip("/") != profile["qwen"]["endpoint"].rstrip("/"):
                    add("OpenCode qwen endpoint", "FAIL", f"{base!r}")
                else:
                    add("OpenCode qwen endpoint", "PASS", base)
                models = block.get("models", {})
                missing = [m["id"] for m in qwen["models"] if m["id"] not in models]
                add(
                    "OpenCode qwen models",
                    "FAIL" if missing else "PASS",
                    "missing " + ", ".join(missing) if missing else "",
                )
        except (OSError, ValueError) as error:
            add("OpenCode config", "FAIL", str(error))

    models_url = profile["qwen"]["endpoint"].rstrip("/") + "/models"
    try:
        payload = http_json(models_url)
        served = {item.get("id") for item in payload.get("data", []) if isinstance(item, dict)}
        missing = [m["id"] for m in qwen["models"] if m["id"] not in served]
        if missing:
            add("Local /v1/models", "FAIL", "missing " + ", ".join(missing))
        else:
            add("Local /v1/models", "PASS", ", ".join(sorted(served)))
    except (OSError, ValueError, urllib.error.URLError) as error:
        add("Local /v1/models", "FAIL", f"{models_url}: {error}")

    health_url = profile["qwen"].get("health")
    if health_url:
        try:
            with urllib.request.urlopen(health_url, timeout=5) as response:
                add("Local health", "PASS" if response.status < 400 else "WARN", f"HTTP {response.status}")
        except (OSError, urllib.error.URLError) as error:
            add("Local health", "WARN", str(error))

    if profile["qwen"]["role"] == "server":
        for unit in ["qwen-inference.service", "qwen-laptop-gateway.service"]:
            state = service_active(unit)
            add(
                f"systemd {unit}",
                "PASS" if state == "active" else ("WARN" if state == "unknown" else "FAIL"),
                state,
            )

    provider_logs = expand_path(profile["paths"]["t3_userdata"]) / "logs/provider"
    recent_plus_error = False
    if provider_logs.is_dir():
        cutoff = time.time() - 14 * 86400
        for log in provider_logs.glob("*.log"):
            try:
                if log.stat().st_mtime < cutoff:
                    continue
                text = log.read_text(encoding="utf-8", errors="ignore")[-2_000_000:]
            except OSError:
                continue
            if "Model unavailable on Plus" in text:
                recent_plus_error = True
                break
    add(
        "Recent Cursor Plus errors",
        "WARN" if recent_plus_error else "PASS",
        "found in last 14 days; old logs may predate the fix" if recent_plus_error else "",
    )

    if as_json:
        print(json.dumps({"profile": profile["id"], "checks": checks}, indent=2))
    else:
        for check in checks:
            line = f"{check['status']:<4}  {check['name']}"
            if check["detail"]:
                line += f" — {check['detail']}"
            print(line)
        failures = sum(1 for check in checks if check["status"] == "FAIL")
        warnings = sum(1 for check in checks if check["status"] == "WARN")
        print(f"\n{len(checks)} checks: {failures} FAIL, {warnings} WARN")
    return 1 if any(check["status"] == "FAIL" for check in checks) else 0


# ---------------------------------------------------------------------------
# Plan / snapshot


# ---------------------------------------------------------------------------
# Guard (read-only, safe to run while T3 is open)


def guard(profile: dict, override: str | None, as_json: bool) -> int:
    cursor, _ = load_catalogs()
    path = find_t3_settings(profile, override)
    if not path.is_file():
        defects = [f"T3 settings file missing: {path}"]
    else:
        try:
            defects = cursor_plus_defects(load_json(path), cursor)
        except (OSError, ValueError) as error:
            defects = [f"cannot read {path}: {error}"]

    if as_json:
        print(json.dumps({"profile": profile["id"], "settings": str(path), "defects": defects}, indent=2))
    elif defects:
        for defect in defects:
            print(f"GUARD FAIL — {defect}")
        print(f"\n{CURSOR_PLUS_REMEDIATION}")
    else:
        print("GUARD PASS — Cursor Plus favorites carry their fixed options and grok-4.7 is visible.")
    return 1 if defects else 0


def plan(profile: dict) -> None:
    cursor, qwen = load_catalogs()
    settings_path = find_t3_settings(profile)
    print(f"Profile: {profile['id']} ({profile['hostname']})")
    print(f"Local endpoint: {profile['qwen']['endpoint']}")
    print(f"T3 settings: {settings_path}")
    if settings_path.is_file():
        settings = load_json(settings_path)
        for model in profile["cursor"]["favorites"]:
            item = favorite(settings, "cursor", model)
            current = item.get("options") if item else None
            desired = cursor["models"][model]["options"]
            state = "OK" if current == desired else "CHANGE"
            print(f"  [{state}] cursor/{model}: {current!r} -> {desired!r}")
        hidden = (
            settings.get("providerModelPreferences", {})
            .get("cursor", {})
            .get("hiddenModels", [])
        )
        print(f"  cursor hiddenModels: {hidden!r}")
        print(
            "  local favorites: "
            + repr(
                [
                    f"{item['provider']}/{item['model']}"
                    for item in settings.get("favorites", [])
                    if item.get("provider") in {"pi", "opencode"}
                ]
            )
        )
    else:
        print("  T3 settings not found; no current values to compare.")

    agent_models = expand_path(profile["paths"]["omp_agent"]) / "models.yml"
    if agent_models.is_file():
        text = agent_models.read_text(encoding="utf-8")
        print(f"  OMP models: {agent_models}")
        for model in qwen["models"]:
            print(f"    [{ 'OK' if model['id'] in text else 'CHANGE' }] {model['id']}")
        print(f"    [{ 'OK' if profile['qwen']['endpoint'] in text else 'CHANGE' }] endpoint {profile['qwen']['endpoint']}")
        print(f"    [{ 'OK' if 'maxTokens: 4096' in text else 'CHANGE' }] maxTokens 4096")
    else:
        print(f"  OMP models not found at {agent_models}")

    opencode_path = expand_path(profile["paths"]["opencode"])
    if opencode_path.is_file():
        config = load_json(opencode_path)
        provider_id = profile["t3"]["opencode_provider"]
        block = config.get("providers", {}).get(provider_id)
        print(f"  OpenCode providers.{provider_id}: {'present' if block else 'MISSING'}")
        if block:
            print(f"    baseURL: {block.get('settings', {}).get('baseURL')!r}")
    else:
        print(f"  OpenCode config not found at {opencode_path}")

    print("\nApply with:")
    print(f"  ./bin/t3cfg apply --profile {profile['id']} --only all")
    print("  (quit T3 first; use --dry-run to preview)")


def redact(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)):
                result[key] = "<redacted>"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and TOKEN_RE.search(value):
        return "<redacted>"
    return value


def redact_yaml(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*(apiKey|token|secret|password|authorization):", line, re.I):
            key = line.split(":", 1)[0]
            lines.append(f"{key}: <redacted>")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def snapshot(profile: dict, out_dir: str | None) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = Path(out_dir) if out_dir else SNAPSHOTS / f"{profile['id']}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    settings_path = find_t3_settings(profile)
    if settings_path.is_file():
        write_text(out / "t3-settings.redacted.json", dump_json(redact(load_json(settings_path))))
    agent_models = expand_path(profile["paths"]["omp_agent"]) / "models.yml"
    if agent_models.is_file():
        write_text(out / "omp-models.redacted.yml", redact_yaml(agent_models.read_text(encoding="utf-8")))
    opencode_path = expand_path(profile["paths"]["opencode"])
    if opencode_path.is_file():
        write_text(out / "opencode.redacted.json", dump_json(redact(load_json(opencode_path))))
    write_text(
        out / "README.txt",
        f"Profile: {profile['id']}\nHost: {profile['hostname']}\n"
        f"Created: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        "Secrets are redacted. Do not commit this directory.\n",
    )
    return out


# ---------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="t3cfg",
        description="Render, apply, and verify the T3 Code master configuration.",
    )
    parser.add_argument("--version", action="version", version="t3cfg 1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List machine profiles")
    list_parser.set_defaults(func=lambda args: 0)

    for name, help_text in [
        ("render", "Render generated files without touching live configs"),
        ("plan", "Show current vs desired values without writing"),
        ("apply", "Apply selected components with backups"),
        ("guard", "Read-only check that Cursor Plus favorites keep their fixed options"),
        ("doctor", "Verify files and the live endpoint"),
        ("snapshot", "Capture redacted local configs"),
    ]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--profile", required=True)
        if name == "render":
            command.add_argument("--out", default=None)
        if name == "apply":
            command.add_argument("--only", default="all")
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--force", action="store_true")
        if name in {"doctor", "guard"}:
            command.add_argument("--json", action="store_true")
        if name in {"apply", "guard"}:
            command.add_argument("--t3-userdata", default=None)
        if name == "snapshot":
            command.add_argument("--out", default=None)

    args = parser.parse_args(argv)

    if args.command == "list":
        for name in profile_names():
            profile = load_profile(name)
            print(f"{name:<8} {profile['label']} ({profile['status']})")
        return 0

    profile = load_profile(args.profile)
    cursor, qwen = load_catalogs()

    if args.command == "render":
        out = render_profile(profile, Path(args.out) if args.out else None)
        print(f"rendered {profile['id']} to {out}")
        return 0

    if args.command == "plan":
        plan(profile)
        return 0

    if args.command == "guard":
        return guard(profile, args.t3_userdata, args.json)

    if args.command == "doctor":
        return doctor(profile, args.json)

    if args.command == "snapshot":
        out = snapshot(profile, args.out)
        print(f"snapshot written to {out}")
        return 0

    if args.command == "apply":
        selected = {item.strip() for item in args.only.split(",") if item.strip()}
        if "all" in selected:
            selected = {"t3", "omp", "opencode", "qwen"}
        unknown = selected - {"t3", "omp", "opencode", "qwen"}
        if unknown:
            raise SystemExit(f"unknown components: {', '.join(sorted(unknown))}")
        patch = build_t3_patch(profile)
        opencode_patch = build_opencode_patch(profile)
        if "t3" in selected:
            apply_t3(profile, patch, args.dry_run, args.force, args.t3_userdata)
        if "omp" in selected:
            apply_omp(profile, args.dry_run)
        if "opencode" in selected:
            apply_opencode(profile, opencode_patch, args.dry_run)
        if "qwen" in selected:
            apply_qwen(profile, args.dry_run)
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
