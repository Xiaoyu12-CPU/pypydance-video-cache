"""Shipped appearance snapshot compatibility, without Qt or service startup."""

import copy
import hashlib
import json
import runpy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dance_ui import theme as theme_module
from dance_ui.theme import (
    BUILTIN_THEMES, DEFAULT_PREFERENCES, THEME_FIELDS, THEME_GROUPS,
    ThemeError, ThemeStore,
)


class BundledPresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = theme_module.BUNDLED_PRESET_DIR
        self.manifest = json.loads((self.bundle / "theme.json").read_text(encoding="utf-8"))
        self.snapshot = self.manifest["theme"]
        self.theme_id = self.snapshot["id"]

    def legacy_document(self):
        themes = copy.deepcopy(list(BUILTIN_THEMES.values()))
        themes[0].update(accent="#123456", radius=8)
        custom = copy.deepcopy(BUILTIN_THEMES["dark"])
        custom.update(id="legacy_custom", name="已有自定义", builtin=False, accent="#654321")
        return {
            "version": 1, "active_theme": custom["id"], "themes": themes + [custom],
            "preferences": {"scale": 125, "density": "compact", "logs_expanded": True},
        }

    def write_document(self, root, document):
        root.mkdir(parents=True, exist_ok=True)
        path = root / "appearance.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_fresh_snapshot_background_and_preferences_survive_reopen(self):
        store = ThemeStore(self.root / "fresh")
        self.assertEqual(store.active_id, self.theme_id)
        self.assertEqual(store.active["name"], "小余12主题")
        self.assertEqual(store.active, self.snapshot)
        self.assertEqual(store.preferences, self.manifest["preferences"])
        self.assertEqual({theme["id"] for theme in store.themes}, {"light", "dark", self.theme_id})
        image = store.background_path()
        self.assertEqual(image, self.bundle / "background.png")
        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), self.manifest["background_sha256"])
        self.assertEqual(
            self.manifest["background_sha256"],
            "fb33dc6ea90ed2124b8ec8af2a55d17a13c714e03d6460ffd3a37b4bfa088786",
        )
        self.assertFalse(Path(store.active["background_image"]).is_absolute())
        self.assertNotIn("\\", store.active["background_image"])
        self.assertEqual(list(store.background_dir.iterdir()), [])
        reopened = ThemeStore(store.root_dir)
        self.assertEqual(reopened.active, store.active)
        self.assertEqual(reopened.preferences, store.preferences)
        self.assertEqual(reopened.background_path(), image)
        self.assertFalse(reopened.can_undo)
        self.assertEqual(reopened.warnings, [])

    def test_existing_theme_values_selection_and_preferences_are_preserved(self):
        root = self.root / "existing"
        raw = self.legacy_document()
        path = self.write_document(root, raw)
        original_bytes = path.read_bytes()
        store = ThemeStore(root)
        self.assertEqual(store.active_id, raw["active_theme"])
        self.assertEqual(store.preferences, raw["preferences"])
        by_id = {theme["id"]: theme for theme in store.themes}
        for original in raw["themes"]:
            self.assertEqual(by_id[original["id"]], original)
        self.assertEqual(by_id[self.theme_id], self.snapshot)
        self.assertEqual(path.read_bytes(), original_bytes)
        store.save()
        reopened = ThemeStore(root)
        self.assertEqual(reopened.active, store.active)
        self.assertEqual(reopened.preferences, raw["preferences"])
        self.assertEqual(sum(theme["id"] == self.theme_id for theme in reopened.themes), 1)

    def test_sparse_legacy_document_uses_original_fallbacks(self):
        cases = (
            {"version": 1, "themes": []},
            {"themes": [], "active_theme": "missing", "preferences": {}},
            {"version": 1, "themes": [], "active_theme": {}, "preferences": []},
        )
        for index, raw in enumerate(cases):
            with self.subTest(case=index):
                root = self.root / ("sparse-" + str(index))
                path = self.write_document(root, raw)
                original_bytes = path.read_bytes()
                store = ThemeStore(root)
                self.assertEqual(store.active_id, "light")
                self.assertEqual(store.active, BUILTIN_THEMES["light"])
                self.assertEqual(store.preferences, DEFAULT_PREFERENCES)
                self.assertIn(self.theme_id, {theme["id"] for theme in store.themes})
                self.assertEqual(path.read_bytes(), original_bytes)

    def test_existing_same_id_is_not_duplicated_or_overwritten(self):
        raw = self.legacy_document()
        edited = copy.deepcopy(self.snapshot)
        edited.update(name="我的后续调整", builtin=False, accent="#102030", image_opacity=0.21)
        raw["themes"].append(edited)
        raw["active_theme"] = self.theme_id
        root = self.root / "same-id"
        self.write_document(root, raw)
        store = ThemeStore(root)
        self.assertEqual(store.active_id, self.theme_id)
        self.assertEqual(store.active["name"], edited["name"])
        for key in THEME_FIELDS:
            self.assertEqual(store.active[key], edited[key], key)
        self.assertTrue(store.active["builtin"])
        self.assertEqual(store.preferences, raw["preferences"])
        store.change({"accent": "#ABCDEF"})
        reopened = ThemeStore(root)
        self.assertEqual(reopened.active["accent"], "#ABCDEF")
        self.assertEqual(reopened.active["image_opacity"], 0.21)
        self.assertEqual(reopened.active["name"], edited["name"])
        self.assertEqual(sum(theme["id"] == self.theme_id for theme in reopened.themes), 1)

    def test_shipped_preset_is_protected_but_copy_can_be_deleted_and_undone(self):
        store = ThemeStore(self.root / "protected")
        before = store.path.read_bytes()
        with self.assertRaises(ThemeError):
            store.delete(self.theme_id)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertFalse(store.can_undo)
        copied = store.create(copy_current=True)
        self.assertFalse(store.active["builtin"])
        self.assertEqual(store.active["background_image"], self.snapshot["background_image"])
        self.assertEqual(store.background_path(), self.bundle / "background.png")
        store.delete(copied)
        self.assertNotIn(copied, {theme["id"] for theme in store.themes})
        self.assertTrue(store.undo())
        self.assertEqual(store.active_id, copied)
        self.assertEqual(store.background_path(), self.bundle / "background.png")

    def test_reset_uses_shipped_snapshot_and_undo_keeps_preferences(self):
        store = ThemeStore(self.root / "reset")
        preferences = {"scale": 150, "density": "comfortable", "logs_expanded": True}
        store.change_preferences(preferences)
        store.rename(self.theme_id, "仍然保留我的名称")
        store.change({"accent": "#102030", "nav_opacity": 0.8, "background_image": ""})
        edited = store.active
        self.assertTrue(store.reset_group("colors"))
        for key in THEME_GROUPS["colors"]:
            self.assertEqual(store.active[key], self.snapshot[key], key)
        self.assertEqual(store.active["nav_opacity"], 0.8)
        self.assertEqual(store.active["background_image"], "")
        self.assertTrue(store.undo())
        self.assertEqual(store.active, edited)
        self.assertTrue(store.reset_current())
        for key in THEME_FIELDS:
            self.assertEqual(store.active[key], self.snapshot[key], key)
        self.assertEqual(store.active["name"], edited["name"])
        self.assertEqual(store.preferences, preferences)
        self.assertEqual(store.background_path(), self.bundle / "background.png")
        self.assertTrue(store.undo())
        self.assertEqual(store.active, edited)
        self.assertEqual(store.preferences, preferences)

    def test_user_background_takes_precedence_over_read_only_bundle(self):
        store = ThemeStore(self.root / "user-image")
        bundled = self.bundle / "background.png"
        original_bundle = bundled.read_bytes()
        reference = store.import_background(bundled)
        self.assertEqual(reference, self.snapshot["background_image"])
        writable = store.root_dir / reference
        self.assertEqual(store.background_path(), writable)
        self.assertEqual(ThemeStore(store.root_dir).background_path(), writable)
        self.assertEqual(writable.read_bytes(), original_bundle)
        self.assertEqual(bundled.read_bytes(), original_bundle)
        writable.unlink()
        self.assertEqual(store.background_path(), bundled)

    def test_missing_or_invalid_bundle_warns_and_preserves_existing_document(self):
        cases = ("missing", "bad-json", "bad-background", "wrong-hash", "bad-preferences")
        for case in cases:
            with self.subTest(case=case):
                root = self.root / ("old-" + case)
                raw = self.legacy_document()
                path = self.write_document(root, raw)
                original_bytes = path.read_bytes()
                bundle = self.root / ("bundle-" + case)
                if case != "missing":
                    shutil.copytree(self.bundle, bundle)
                    manifest = copy.deepcopy(self.manifest)
                    if case == "bad-json":
                        (bundle / "theme.json").write_text("{broken", encoding="utf-8")
                    elif case == "bad-background":
                        (bundle / "background.png").write_bytes(b"not an image")
                    elif case == "wrong-hash":
                        manifest["background_sha256"] = "0" * 64
                    elif case == "bad-preferences":
                        manifest["preferences"] = []
                    if case not in ("bad-json", "bad-background"):
                        (bundle / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
                with patch.object(theme_module, "BUNDLED_PRESET_DIR", bundle):
                    store = ThemeStore(root)
                    self.assertEqual(store.active_id, raw["active_theme"])
                    self.assertEqual(store.preferences, raw["preferences"])
                    self.assertEqual(store.themes, raw["themes"])
                    self.assertTrue(store.warnings)
                    self.assertEqual(path.read_bytes(), original_bytes)
                    fallback = ThemeStore(self.root / ("fresh-" + case))
                    self.assertEqual(fallback.active_id, "light")
                    self.assertEqual(fallback.preferences, DEFAULT_PREFERENCES)
                    self.assertTrue(fallback.warnings)
                recovered = ThemeStore(root)
                self.assertIn(self.theme_id, {theme["id"] for theme in recovered.themes})
                self.assertEqual(recovered.active_id, raw["active_theme"])
                self.assertEqual(path.read_bytes(), original_bytes)

    def test_failed_user_save_keeps_shipped_snapshot_memory_and_resources_intact(self):
        store = ThemeStore(self.root / "failed-save")
        original_document = store.path.read_bytes()
        original_background = (self.bundle / "background.png").read_bytes()
        with patch("dance_ui.theme.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.change({"accent": "#102030"})
        self.assertEqual(store.active, self.snapshot)
        self.assertEqual(store.path.read_bytes(), original_document)
        self.assertEqual((self.bundle / "background.png").read_bytes(), original_background)
        self.assertFalse(store.can_undo)
        self.assertEqual(list(store.root_dir.glob(".appearance-*.tmp")), [])

    def test_frozen_style_module_path_finds_shipped_resources_without_writing_them(self):
        internal = self.root / "PyPyDanceCache" / "_internal"
        package = internal / "dance_ui"
        bundled = package / "presets" / "xiaoyu12"
        shutil.copytree(self.bundle, bundled)
        module_path = package / "theme.py"
        shutil.copyfile(Path(theme_module.__file__), module_path)
        resources_before = {path.name: path.read_bytes() for path in bundled.iterdir()}
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(internal), create=True):
            namespace = runpy.run_path(str(module_path))
            self.assertEqual(namespace["BUNDLED_PRESET_DIR"], bundled)
            store = namespace["ThemeStore"](self.root / "windows-user-data")
            self.assertEqual(store.active, self.snapshot)
            self.assertEqual(store.background_path(), bundled / "background.png")
            store.change({"accent": "#ABCDEF"})
            reopened = namespace["ThemeStore"](store.root_dir)
            self.assertEqual(reopened.active["accent"], "#ABCDEF")
            self.assertEqual(reopened.background_path(), bundled / "background.png")
        self.assertEqual({path.name: path.read_bytes() for path in bundled.iterdir()}, resources_before)
        self.assertFalse((package / "appearance.json").exists())


if __name__ == "__main__":
    unittest.main()
