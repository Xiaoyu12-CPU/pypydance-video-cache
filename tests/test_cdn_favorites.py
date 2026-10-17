import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dance_ui.cdn_favorites import CDNFavorites


class CDNFavoritesTests(unittest.TestCase):
    def test_restart_deduplication_and_removal(self):
        with tempfile.TemporaryDirectory() as root:
            store = CDNFavorites(root)
            store.save(["1.1.1.1", "8.8.8.8", "1.1.1.1"])
            self.assertEqual(CDNFavorites(root).items, ["1.1.1.1", "8.8.8.8"])
            store.save(["8.8.8.8"])
            self.assertEqual(CDNFavorites(root).items, ["8.8.8.8"])

    def test_failed_save_preserves_file_and_memory(self):
        with tempfile.TemporaryDirectory() as root:
            store = CDNFavorites(root)
            store.save(["1.1.1.1"])
            for ip in ("127.0.0.1", "invalid", "::1"):
                with self.assertRaises(ValueError):
                    store.save([ip])
            with patch("dance_ui.cdn_favorites.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    store.save(["8.8.8.8"])
            self.assertEqual(store.items, ["1.1.1.1"])
            self.assertEqual(CDNFavorites(root).items, ["1.1.1.1"])

    def test_corrupt_file_is_reported_and_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "cdn-favorites.json"
            path.write_text("broken", encoding="utf-8")
            store = CDNFavorites(root)
            self.assertTrue(store.warning)
            self.assertEqual(path.read_text(), "broken")
