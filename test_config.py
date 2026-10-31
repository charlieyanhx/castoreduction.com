"""
Tests for config/ — Phase 4 of cycle32.

Verifies:
  - Default profile loads from disk
  - get(path, default) returns nested values via dotted path
  - get_all() returns full profile dict
  - Profile switch via PIPELINE_PROFILE env var works
  - Missing key returns default
  - reload_config clears cache
"""
from __future__ import annotations
import os
import unittest


class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        # Force default profile
        os.environ.pop("PIPELINE_PROFILE", None)
        from config import reload_config
        reload_config()

    def test_default_profile_loads(self):
        from config import get_all, profile_name
        self.assertEqual(profile_name(), "default")
        all_cfg = get_all()
        self.assertIn("differentiators", all_cfg)

    def test_dotted_path_lookup(self):
        from config import get
        self.assertEqual(get("differentiators.per_dimension_max_tokens"), 1500)
        self.assertEqual(get("max_diff.panel_size"), 30)
        self.assertEqual(get("psm.panel_size"), 40)

    def test_default_returned_for_missing(self):
        from config import get
        self.assertEqual(get("nonexistent.path", default=42), 42)
        self.assertIsNone(get("not.here"))

    def test_quick_profile_switch(self):
        from config import reload_config, get, profile_name
        os.environ["PIPELINE_PROFILE"] = "quick"
        reload_config()
        try:
            self.assertEqual(profile_name(), "quick")
            self.assertEqual(get("max_diff.panel_size"), 20)  # vs 30 in default
            self.assertEqual(get("narration.default_mode"), "template")
        finally:
            os.environ.pop("PIPELINE_PROFILE")
            reload_config()

    def test_available_profiles(self):
        from config import available_profiles
        names = available_profiles()
        self.assertIn("default", names)
        self.assertIn("quick", names)

    def test_unknown_profile_falls_back_to_empty(self):
        from config import reload_config, get
        os.environ["PIPELINE_PROFILE"] = "nonexistent_profile"
        reload_config()
        try:
            # Returns default (None) — empty config
            self.assertIsNone(get("max_diff.panel_size"))
            self.assertEqual(get("max_diff.panel_size", default=99), 99)
        finally:
            os.environ.pop("PIPELINE_PROFILE")
            reload_config()


if __name__ == "__main__":
    unittest.main()
