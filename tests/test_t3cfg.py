import json
import re
import sys
import tempfile
import unittest
from unittest import mock
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
        self.assertIn(("pi", "lm-studio/14bfe4b42be4a925d98816db830155f476c605e7"), favorites)
        self.assertIn(("pi", "lm-studio/58ebd123013160600229eda180b5b17f3fb7af9d"), favorites)
        self.assertIn(("opencode", "qwen-local/qwen/qwen3.8-27b"), favorites)
        removed = {(rule["provider"], rule.get("model")) for rule in patch["favoritesRemove"]}
        self.assertIn(("pi", "lm-studio/qwen/qwen3.8-27b-unsloth"), removed)
        self.assertIn(("pi", "lm-studio/qwen/qwen3.8-27b-uncensored"), removed)
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


class CursorGuardTests(unittest.TestCase):
    """Regression tests for the 2026-11-02 mars-laptop 'Model unavailable on
    Plus' outage: the live cursor favorites had no `options` at all."""

    @staticmethod
    def _outage_settings() -> dict:
        return t3cfg.load_json(
            Path(__file__).parent / "fixtures/outage-laptop/settings.json"
        )

    def test_guard_flags_optionless_favorites(self):
        cursor, _ = t3cfg.load_catalogs()
        defects = t3cfg.cursor_plus_defects(self._outage_settings(), cursor)
        joined = "\n".join(defects)
        self.assertIn("composer-2.5", joined)
        self.assertIn("grok-4.7", joined)
        self.assertIn("Model unavailable on Plus", joined)

    def test_guard_flags_hidden_grok47(self):
        cursor, _ = t3cfg.load_catalogs()
        settings = {"favorites": [], "providerModelPreferences": {"cursor": {"hiddenModels": ["grok-4.7"]}}}
        defects = t3cfg.cursor_plus_defects(settings, cursor)
        self.assertTrue(any("hiddenModels" in defect for defect in defects))

    def test_guard_passes_canonical_state(self):
        cursor, _ = t3cfg.load_catalogs()
        settings = t3cfg.load_json(
            Path(__file__).parent / "fixtures/canonical-cursor/settings.json"
        )
        self.assertEqual(t3cfg.cursor_plus_defects(settings, cursor), [])

    def test_merge_repairs_the_outage_state(self):
        """The applier must be able to fix the laptop's broken state on rerun."""
        profile = t3cfg.load_profile("laptop")
        patch = t3cfg.build_t3_patch(profile)
        merged, _ = t3cfg.merge_t3_settings(self._outage_settings(), patch)
        cursor, _ = t3cfg.load_catalogs()
        self.assertEqual(
            t3cfg.cursor_plus_defects(merged, cursor),
            [],
            "merge must leave a Cursor Plus-clean state",
        )

    def test_apply_refuses_to_write_broken_cursor_state(self):
        """Defense-in-depth: a corrupted catalog must never reach disk."""
        profile = t3cfg.load_profile("laptop")
        patch = t3cfg.build_t3_patch(profile)
        broken_cursor = t3cfg.copy.deepcopy(t3cfg.load_catalogs()[0])
        broken_cursor["models"]["composer-2.5"]["options"] = [{"id": "fast", "value": "true"}]
        with tempfile.TemporaryDirectory() as temporary:
            settings_path = Path(temporary) / "settings.json"
            settings_path.write_text(json.dumps(self._outage_settings()), encoding="utf-8")
            with mock.patch.object(
                t3cfg, "load_catalogs", return_value=(broken_cursor, t3cfg.load_catalogs()[1])
            ):
                with self.assertRaises(SystemExit):
                    t3cfg.apply_t3(profile, patch, dry_run=False, force=True, override=str(settings_path))
            # Nothing was written: the file must still hold the outage state.
            self.assertEqual(t3cfg.load_json(settings_path), self._outage_settings())


class OmpTests(unittest.TestCase):
    def test_models_have_canonical_ids_and_limits(self):
        profile = t3cfg.load_profile("laptop")
        text = t3cfg.build_omp_models(profile)
        self.assertIn("http://100.95.87.91:8088/v1", text)
        self.assertIn("qwen/qwen3.8-27b", text)
        self.assertIn("14bfe4b42be4a925d98816db830155f476c605e7", text)
        self.assertIn("58ebd123013160600229eda180b5b17f3fb7af9d", text)
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
        self.assertIn("OMP_QWEN_MODEL_ORCAROUTER=58ebd123013160600229eda180b5b17f3fb7af9d", env)
        self.assertIn("OMP_QWEN_MODEL_SWIFT=14bfe4b42be4a925d98816db830155f476c605e7", env)
        self.assertNotIn("OMP_QWEN_MODEL_UNSLOTH", env)
        self.assertIn("OMP_QWEN_BASE_URL=http://100.95.87.91:8088/v1", env)

    def test_text_only_model_gets_no_image_input(self):
        profile = t3cfg.load_profile("desktop")
        text = t3cfg.build_omp_models(profile)
        orca = text.split("- id: 58ebd123013160600229eda180b5b17f3fb7af9d")[1].split("- id:")[0]
        self.assertNotIn("- image", orca)
        q4 = text.split("- id: qwen/qwen3.8-27b")[1].split("- id:")[0]
        self.assertIn("- image", q4)


class OpenCodeTests(unittest.TestCase):
    def test_provider_shape(self):
        profile = t3cfg.load_profile("laptop")
        patch = t3cfg.build_opencode_patch(profile)
        block = patch["providers"]["qwen-local"]
        self.assertEqual(block["settings"]["baseURL"], "http://100.95.87.91:8088/v1")
        self.assertEqual(
            block["package"], "@opencode/ai/providers/openai-compatible"
        )
        self.assertEqual(
            set(block["models"]),
            {
                "qwen/qwen3.8-27b",
                "14bfe4b42be4a925d98816db830155f476c605e7",
                "58ebd123013160600229eda180b5b17f3fb7af9d",
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


class IdempotentApplyTests(unittest.TestCase):
    """The sync timer applies every 15 minutes; a no-op must leave no backup behind."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.profile = json.loads(json.dumps(t3cfg.load_profile("laptop")))

    def backups(self):
        return [p.name for p in self.dir.iterdir() if ".bak-t3cfg-" in p.name]

    def test_unchanged_opencode_config_is_not_backed_up_or_rewritten(self):
        path = self.dir / "opencode.json"
        self.profile["paths"]["opencode"] = str(path)
        patch = t3cfg.build_opencode_patch(self.profile)
        path.write_text(t3cfg.dump_json(t3cfg.merge_opencode_config({}, patch)), encoding="utf-8")
        with mock.patch("sys.stdout"):
            t3cfg.apply_opencode(self.profile, patch, dry_run=False)
        self.assertEqual(self.backups(), [])

    def test_unchanged_t3_settings_are_not_backed_up_or_rewritten(self):
        patch = t3cfg.build_t3_patch(self.profile)
        canonical = t3cfg.load_json(ROOT / "tests/fixtures/canonical-cursor/settings.json")
        merged, _ = t3cfg.merge_t3_settings(canonical, patch)
        (self.dir / "settings.json").write_text(t3cfg.dump_json(merged), encoding="utf-8")
        with mock.patch.object(t3cfg, "t3_running", return_value=False), mock.patch("sys.stdout"):
            t3cfg.apply_t3(self.profile, patch, dry_run=False, force=False, override=str(self.dir))
        self.assertEqual(self.backups(), [])

    def test_changed_opencode_config_is_still_backed_up(self):
        path = self.dir / "opencode.json"
        self.profile["paths"]["opencode"] = str(path)
        path.write_text("{}\n", encoding="utf-8")
        with mock.patch("sys.stdout"):
            t3cfg.apply_opencode(self.profile, t3cfg.build_opencode_patch(self.profile), dry_run=False)
        self.assertEqual(len(self.backups()), 1)


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
                self.assertTrue((out / "omp/qwen/variant-swift.yml").is_file())
                # No machine renders model-server or gateway files: Bionic is the only engine.
                self.assertFalse((out / "qwen").exists())
                self.assertFalse((out / "systemd").exists())

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
            "state",
            "bin",
        }
        for name in t3cfg.profile_names():
            profile = t3cfg.load_profile(name)
            self.assertEqual(profile["id"], name)
            self.assertTrue(required.issubset(profile["paths"]), name)
            self.assertEqual(set(profile["qwen"]), {"endpoint", "context_window", "max_output_tokens", "default_effort"})

    def test_endpoints(self):
        desktop = t3cfg.load_profile("desktop")
        laptop = t3cfg.load_profile("laptop")
        work = t3cfg.load_profile("work")
        # All three machines use LM Studio Bionic on the desktop, which binds to its Tailscale address.
        for profile in (desktop, laptop, work):
            self.assertEqual(profile["qwen"]["endpoint"], "http://100.95.87.91:8088/v1")


if __name__ == "__main__":
    unittest.main()
