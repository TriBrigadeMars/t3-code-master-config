#!/usr/bin/env python3
"""Launch the isolated, local-only Qwen OMP baseline.

The generated ~/.config/t3-master/qwen.env file supplies the endpoint, model
aliases, OMP binary, and agent directory. No cloud fallback is attempted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(
    os.path.expanduser(os.environ.get("OMP_QWEN_ENV", "~/.config/t3-master/qwen.env"))
)
VARIANTS = {
    "q4": "OMP_QWEN_MODEL_Q4",
    "unsloth": "OMP_QWEN_MODEL_UNSLOTH",
    "orcarouter": "OMP_QWEN_MODEL_ORCAROUTER",
}
CONTROLLED = {
    "--profile", "--config", "--model", "--provider", "--models", "--smol",
    "--slow", "--plan", "--system-prompt", "--system-prompt-template",
    "--append-system-prompt", "--tools", "--extension", "-e", "--hook",
    "--plugin-dir", "--prewalk", "--plan-yolo", "--advisor",
}


def expand(value: str) -> str:
    text = os.path.expanduser(value)
    text = re.sub(
        r"%([^%]+)%",
        lambda match: os.environ.get(match.group(1), match.group(0)),
        text,
    )
    return os.path.expandvars(text)


def load_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise SystemExit(f"missing {ENV_FILE}; run: ./bin/t3cfg apply --profile <name> --only omp")
    values: dict[str, str] = {}
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def inventory(env: dict[str, str]) -> dict[str, dict[str, object]]:
    base = env["OMP_QWEN_BASE_URL"].rstrip("/")
    result: dict[str, dict[str, object]] = {}
    for name, key in VARIANTS.items():
        result[name] = {
            "model": env.get(key, ""),
            "endpoint": base,
            "advertised": False,
        }
    try:
        request = urllib.request.Request(base + "/models", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
        served = {item.get("id"): item for item in payload.get("data", []) if isinstance(item, dict)}
        for row in result.values():
            model = served.get(str(row["model"]))
            row["advertised"] = model is not None
            if model:
                row["status"] = model.get("status", {})
    except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
        for row in result.values():
            row["error"] = str(error)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone local Qwen OMP. Remaining arguments go to OMP.",
        epilog='Examples: omp-qwen --goal "Finish the plan"; omp-qwen --variant unsloth; omp-qwen --check',
    )
    parser.add_argument("--variant", choices=VARIANTS, default="q4")
    parser.add_argument("--check", action="store_true", help="Inspect all local aliases without inference")
    options, remaining = parser.parse_known_args()

    env = load_env()
    rows = inventory(env)
    if options.check:
        print(json.dumps(rows, indent=2))
        return 0 if all(row.get("advertised") for row in rows.values()) else 1

    for argument in remaining:
        if argument.split("=", 1)[0] in CONTROLLED:
            parser.error(argument + " is controlled by this baseline; select --variant or use ordinary omp")

    row = rows[options.variant]
    if not row.get("advertised"):
        print(
            "Selected local model is unavailable at "
            + env["OMP_QWEN_BASE_URL"]
            + ". No cloud fallback will be attempted.",
            file=sys.stderr,
        )
        return 1

    agent_dir = Path(expand(env["OMP_QWEN_AGENT_DIR"]))
    omp = Path(expand(env["OMP_QWEN_OMP_BIN"]))
    system = agent_dir / "SYSTEM.md"
    config = agent_dir / "config.yml"
    overlay = agent_dir / f"variant-{options.variant}.yml"
    for required in (omp, system, config, overlay):
        if not required.exists():
            print(f"missing {required}; re-run the OMP apply step", file=sys.stderr)
            return 1

    command = [
        str(omp),
        "--profile", "qwen",
        "--config", str(config),
        "--config", str(overlay),
        "--system-prompt", str(system),
        "--tools", env.get("OMP_QWEN_TOOLS", "read,bash,edit,write,glob,grep,context_notes,new_context,goal"),
        "--no-extensions", "--no-skills", "--no-lsp", "--no-title", "--no-prewalk",
        "--model", "lm-studio/" + str(row["model"]),
        *remaining,
    ]
    child_env = os.environ.copy()
    for name in list(child_env):
        if name.startswith("T3_") or name in {
            "OMP_PROFILE", "PI_PROFILE", "PI_CODING_AGENT_DIR",
            "PI_SMOL_MODEL", "PI_SLOW_MODEL", "PI_PLAN_MODEL",
        }:
            child_env.pop(name, None)
    os.execve(str(omp), command, child_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
