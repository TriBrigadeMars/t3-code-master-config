import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import t3cfg  # noqa: E402


class CursorCatalogTests(unittest.TestCase):
    def test_cursor_plus_fixed_options(self):
        cursor, _ = t3cfg.load_catalogs()
        self.assertEqual(
            cursor["models"]["composer-2.5"]["options"],
            [{"id": "fastMode", "value": "false"}],
        )
        self.assertEqual(
            cursor["models"]["grok-4.6"]["options"],
            [
                {"id": "effort", "value": "medium"},
                {"id": "fastMode", "value": "false"},
            ],
        )
        self.assertEqual(
            cursor["models"]["grok-4.7"]["options"],
            [
                {"id": "contextWindow", "value": "256k"},
                {"id": "fastMode", "value": "false"},
                {"id": "reasoning_effort", "value": "medium"},
            ],
        )

    def test_grok_option_ids_are_not_interchangeable(self):
        cursor, _ = t3cfg.load_catalogs()
        grok_46 = {item["id"] for item in cursor["models"]["grok-4.6"]["options"]}
        grok_47 = {item["id"] for item in cursor["models"]["grok-4.7"]["options"]}
        self.assertIn("effort", grok_46)
        self.assertNotIn("reasoning_effort", grok_46)
        self.assertIn("reasoning_effort", grok_47)
        self.assertNotIn("effort", grok_47)


class T3PatchTests(unittest.TestCase):
    def test_all_profiles_get_fixed_cursor_options(self):
        cursor, _ = t3cfg.load_catalogs()
        for name in t3cfg.profile_names():
            profile = t3cfg.load_profile(name)
            patch = t3cfg.build_t3_patch(profile)
            favorites = {
                (item["provider"], item["model"]): item
                for item in patch["favorites"]
            }
            for model in profile["cursor"]["favorites"]:
                self.assertEqual(
                    favorites[("cursor", model)]["options"],
                    cursor["models"][model]["options"],
                    f"{name}: {model}",
                )
            self.assertNotIn("grok-4.7", patch["providerModelPreferences"]["cursor"]["addHidden"])
            self.assertIn("grok-4.5", patch["providerModelPreferences"]["cursor"]["addHidden"])

    def test_local_favorites_and_binary_path(self):
        profile = t3cfg.load_profile("desktop")
        patch = t3cfg.build_t3_patch(profile)
        favorites = {(item["provider"], item["model"]) for item in patch["favorites"]}
        self.assertIn(("pi", "lm-studio/qwen/qwen3.8-27b"), favorites)
        self.assertIn(("pi", "lm-studio/qwen/qwen3.8-27b-unsloth"), favorites)
        self.assertIn(("pi", "lm-studio/qwen/qwen3.8-27b-uncensored"), favorites)
        self.assertIn(("opencode", "qwen-local/qwen/qwen3.8-27b"), favorites)
        binary = patch["providerInstances"]["pi"]["config"]["binaryPath"]
        self.assertFalse(binary.startswith("~"), binary)
        self.assertTrue(binary.endswith(".bun/bin/omp"), binary)

    def test_merge_preserves_unrelated_and_removes_stale(self):
        profile = t3cfg.load_profile("desktop")
        patch = t3cfg.build_t3_patch(profile)
        settings = {
            "favorites": [
                {"provider": "codex", "model": "gpt-6-luna"},
                {"provider": "cursor", "model": "composer-2.5"},
                {"provider": "opencode", "model": "lmstudio/58ebd123"},
            ],
            "providerModelPreferences": {
                "cursor": {"hiddenModels": ["grok-4.5", "grok-4.7"]},
                "pi": {"hiddenModels": ["lm-studio/qwen/qwen3.8-27b"]},
                "opencode": {"hiddenModels": ["qwen-local/qwen/qwen3.8-27b"]},
            },
            "providerInstances": {},
        }
        merged, changes = t3cfg.merge_t3_settings(settings, patch)
        favorites = {
            (item["provider"], item["model"]): item for item in merged["favorites"]
        }
        self.assertIn(("codex", "gpt-6-luna"), favorites)
        self.assertIn(("cursor", "composer-2.5"), favorites)
        self.assertEqual(
            favorites[("cursor", "composer-2.5")]["options"],
            [{"id": "fastMode", "value": "false"}],
        )
        self.assertNotIn(("opencode", "lmstudio/58ebd123"), favorites)
        hidden = merged["providerModelPreferences"]["cursor"]["hiddenModels"]
        self.assertIn("grok-4.5", hidden)
        self.assertNotIn("grok-4.7", hidden)
        self.assertNotIn(
            "lm-studio/qwen/qwen3.8-27b",
            merged["providerModelPreferences"]["pi"]["hiddenModels"],
        )
        self.assertTrue(changes)


class OmpTests(unittest.TestCase):
    def test_models_have_canonical_ids_and_limits(self):
        profile = t3cfg.load_profile("laptop")
        text = t3cfg.build_omp_models(profile)
        self.assertIn("http://100.95.87.91:1235/v1", text)
        self.assertIn("qwen/qwen3.8-27b", text)
        self.assertIn("qwen/qwen3.8-27b-unsloth", text)
        self.assertIn("qwen/qwen3.8-27b-uncensored", text)
        self.assertIn("maxTokens: 4096", text)
        self.assertIn("contextWindow: 32768", text)
        self.assertIn("defaultLevel: medium", text)
        self.assertIn("openai-models-list", text)

    def test_merge_replaces_only_lm_studio(self):
        profile = t3cfg.load_profile("desktop")
        existing = (
            "providers:\n"
            "  lm-studio:\n"
            "    old: true\n"
            "  copilot:\n"
            "    apiKey: should-stay\n"
        )
        merged = t3cfg.merge_omp_models(existing, t3cfg.omp_lmstudio_block(profile))
        self.assertEqual(merged.count("  lm-studio:"), 1)
        self.assertNotIn("old: true", merged)
        self.assertIn("copilot:", merged)
        self.assertIn("should-stay", merged)
        self.assertIn("qwen/qwen3.8-27b", merged)
        self.assertIn("maxTokens: 4096", merged)

    def test_env_uses_orcarouter_key(self):
        profile = t3cfg.load_profile("desktop")
        env = t3cfg.build_qwen_env(profile)
        self.assertIn("OMP_QWEN_MODEL_ORCAROUTER=qwen/qwen3.8-27b-uncensored", env)
        self.assertNotIn("OMP_QWEN_MODEL_UNCENSORED", env)


class OpenCodeTests(unittest.TestCase):
    def test_provider_shape(self):
        profile = t3cfg.load_profile("laptop")
        patch = t3cfg.build_opencode_patch(profile)
        block = patch["providers"]["qwen-local"]
        self.assertEqual(block["settings"]["baseURL"], "http://100.95.87.91:1235/v1")
        self.assertEqual(
            block["package"], "@opencode/ai/providers/openai-compatible"
        )
        self.assertEqual(
            set(block["models"]),
            {
                "qwen/qwen3.8-27b",
                "qwen/qwen3.8-27b-unsloth",
                "qwen/qwen3.8-27b-uncensored",
            },
        )
        self.assertEqual(block["models"]["qwen/qwen3.8-27b"]["limit"]["output"], 4096)

    def test_merge_preserves_other_providers(self):
        profile = t3cfg.load_profile("desktop")
        patch = t3cfg.build_opencode_patch(profile)
        config = {
            "mcp": {"servers": {}},
            "providers": {
                "cliproxy": {"settings": {"baseURL": "http://127.0.0.1:8317/v1"}}
            },
        }
        merged = t3cfg.merge_opencode_config(config, patch)
        self.assertIn("cliproxy", merged["providers"])
        self.assertIn("qwen-local", merged["providers"])
        self.assertEqual(merged["mcp"], {"servers": {}})


class RenderTests(unittest.TestCase):
    def test_all_profiles_render(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in t3cfg.profile_names():
                profile = t3cfg.load_profile(name)
                out = root / name
                t3cfg.render_profile(profile, out)
                self.assertTrue((out / "manifest.json").is_file())
                self.assertTrue((out / "t3/settings.patch.json").is_file())
                self.assertTrue((out / "omp/default/models.yml").is_file())
                self.assertTrue((out / "omp/qwen/models.yml").is_file())
                self.assertTrue((out / "opencode/provider.patch.json").is_file())
                self.assertTrue((out / "omp/qwen/variant-orcarouter.yml").is_file())
                self.assertFalse((out / "omp/qwen/variant-uncensored.yml").exists())
                if profile["qwen"]["role"] == "server":
                    self.assertTrue((out / "qwen/server.json").is_file())
                    self.assertTrue((out / "systemd/qwen-inference.service").is_file())
                else:
                    self.assertFalse((out / "qwen").exists())

    def test_no_tokens_in_generated_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in t3cfg.profile_names():
                profile = t3cfg.load_profile(name)
                out = root / name
                t3cfg.render_profile(profile, out)
                for path in out.rglob("*"):
                    if not path.is_file():
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    self.assertIsNone(t3cfg.TOKEN_RE.search(text), f"token-like value in {path}")

    def test_no_tokens_in_repo(self):
        excluded = {".git", "build", "snapshots", "__pycache__"}
        for path in ROOT.rglob("*"):
            if not path.is_file() or any(part in excluded for part in path.parts):
                continue
            if path.suffix in {".pyc"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertIsNone(t3cfg.TOKEN_RE.search(text), f"token-like value in {path}")


class ProfileTests(unittest.TestCase):
    def test_required_paths(self):
        required = {
            "t3_userdata",
            "omp_agent",
            "omp_profile",
            "opencode",
            "qwen_config",
            "qwen_share",
            "state",
            "bin",
        }
        for name in t3cfg.profile_names():
            profile = t3cfg.load_profile(name)
            self.assertEqual(profile["id"], name)
            self.assertTrue(required.issubset(profile["paths"]), name)
            self.assertIn(profile["qwen"]["role"], {"server", "client"})

    def test_endpoints(self):
        desktop = t3cfg.load_profile("desktop")
        laptop = t3cfg.load_profile("laptop")
        server = t3cfg.load_profile("server")
        self.assertEqual(desktop["qwen"]["endpoint"], "http://127.0.0.1:1234/v1")
        self.assertEqual(laptop["qwen"]["endpoint"], "http://100.95.87.91:1235/v1")
        self.assertEqual(server["qwen"]["endpoint"], "http://100.95.87.91:1235/v1")


if __name__ == "__main__":
    unittest.main()
