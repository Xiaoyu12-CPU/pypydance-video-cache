"""Release safety checks using tiny local fixtures and no GitHub operations."""

import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from tools import publish_windows_preview as publisher


class OpenVRPackagingTests(unittest.TestCase):
    def run_hook(self, package_dir, windows):
        """Exercise the hook without importing OpenVR or requiring PyInstaller."""
        hook_modules = {
            name: ModuleType(name)
            for name in ("PyInstaller", "PyInstaller.compat", "PyInstaller.utils", "PyInstaller.utils.hooks")
        }
        hook_modules["PyInstaller.compat"].is_win = windows
        package_paths = Mock(return_value=(str(package_dir.parent), str(package_dir)))
        hook_modules["PyInstaller.utils.hooks"].get_package_paths = package_paths
        hook = Path(__file__).resolve().parents[1] / "tools" / "pyinstaller_hooks" / "hook-openvr.py"
        with patch.dict(sys.modules, hook_modules):
            result = runpy.run_path(str(hook))
        return result, package_paths

    def test_hook_collects_only_the_windows_x64_library(self):
        with tempfile.TemporaryDirectory(prefix="pypydance-openvr-hook-") as directory:
            package_dir = Path(directory) / "openvr"
            package_dir.mkdir()
            for name in ("libopenvr_api_64.dll", "libopenvr_api_32.dll", "libopenvr_api_64.so", "libopenvr_api_32.dylib"):
                (package_dir / name).write_bytes(b"synthetic library")
            result, package_paths = self.run_hook(package_dir, windows=True)
            self.assertEqual(result["binaries"], [(str(package_dir / "libopenvr_api_64.dll"), "openvr")])
            package_paths.assert_called_once_with("openvr")

    def test_hook_rejects_missing_windows_library_and_ignores_other_platforms(self):
        with tempfile.TemporaryDirectory(prefix="pypydance-openvr-hook-") as directory:
            package_dir = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "Windows x64 OpenVR library is missing"):
                self.run_hook(package_dir, windows=True)
            result, package_paths = self.run_hook(package_dir, windows=False)
            self.assertEqual(result["binaries"], [])
            package_paths.assert_not_called()

    def test_frozen_windows_smoke_loads_openvr_without_initializing_steamvr(self):
        from dance_ui.smoke import _check_openvr_library

        openvr = SimpleNamespace(__version__="2.12.1401", init=Mock(), VRApplications=Mock())
        report = {"checks": []}
        with patch.object(sys, "platform", "win32"), patch.object(sys, "frozen", True, create=True), \
                patch.dict(sys.modules, {"openvr": openvr}):
            _check_openvr_library(report)
        self.assertEqual(report["openvr_version"], "2.12.1401")
        self.assertEqual(report["checks"], ["openvr_library_loaded_without_initialization"])
        openvr.init.assert_not_called()
        openvr.VRApplications.assert_not_called()

    def test_smoke_requires_openvr_only_in_frozen_windows(self):
        from dance_ui.smoke import _check_openvr_library

        with patch.dict(sys.modules, {"openvr": None}):
            for platform, frozen in (("darwin", False), ("darwin", True), ("win32", False)):
                with self.subTest(platform=platform, frozen=frozen), \
                        patch.object(sys, "platform", platform), patch.object(sys, "frozen", frozen, create=True):
                    report = {"checks": []}
                    _check_openvr_library(report)
                    self.assertEqual(report, {"checks": []})
            with patch.object(sys, "platform", "win32"), patch.object(sys, "frozen", True, create=True):
                with self.assertRaises(ModuleNotFoundError):
                    _check_openvr_library({"checks": []})


class ReleaseAssetTests(unittest.TestCase):
    SHA = "a" * 40

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pypydance-release-test-")
        self.addCleanup(temporary.cleanup)
        self.assets = Path(temporary.name)
        self.block_process = patch.object(
            publisher.subprocess, "check_output",
            side_effect=AssertionError("Release tests must not execute git, gh, or any subprocess."),
        )
        self.block_process.start()
        self.addCleanup(self.block_process.stop)
        self.info = {
            "git_sha": self.SHA,
            "isolated_tests": {
                "status": "passed", "platform": "win32", "tests_run": 3, "passed": 3,
                "failures": [], "errors": [], "skipped": [],
                "expected_failures": [], "unexpected_successes": [],
            },
            "frozen_demo_smoke": {
                "status": "passed", "frozen": True, "platform": "win32",
                "qt_platform": "windows", "application_version": publisher.TAG.removeprefix("v"),
            },
        }

    def write_assets(self, info=None):
        info = copy.deepcopy(self.info if info is None else info)
        text = json.dumps(info, ensure_ascii=False)
        archive = self.assets / "PyPyDanceCache-windows-x64.zip"
        with ZipFile(archive, "w") as zipped:
            for name in ("PyPyDanceCache.exe", "_internal/python312.dll", "打开演示.bat"):
                zipped.writestr("PyPyDanceCache/" + name, b"tiny synthetic fixture")
            zipped.writestr("PyPyDanceCache/docs/build-info.json", text)
        (self.assets / "build-info.json").write_text(text, encoding="utf-8")
        (self.assets / (archive.name + ".sha256")).write_text(
            publisher.digest(archive) + "  " + archive.name + "\n", encoding="ascii",
        )
        return info

    def remote_assets(self, files):
        return {
            "assets": [
                {"name": file.name, "state": "uploaded", "size": file.stat().st_size,
                 "digest": "sha256:" + publisher.digest(file)}
                for file in files
            ],
        }

    def test_accepts_complete_windows_artifact_and_matching_uploaded_assets(self):
        expected = self.write_assets()
        info, files = publisher.verify_assets(self.assets, self.SHA)
        self.assertEqual(info, expected)
        self.assertEqual(len(files), 3)
        self.assertLess(files[0].stat().st_size, 4096)
        publisher.verify_uploaded(self.remote_assets(files), files)

    def test_rejects_wrong_commit_incomplete_tests_and_non_native_smoke(self):
        cases = [
            ("git_sha", None, "b" * 40),
            ("isolated_tests", "skipped", [{"test": "GUI", "reason": "Qt missing"}]),
            ("isolated_tests", "expected_failures", ["GUI"]),
            ("isolated_tests", "unexpected_successes", ["GUI"]),
            ("isolated_tests", "platform", "darwin"),
            ("isolated_tests", "passed", 2),
            ("frozen_demo_smoke", "qt_platform", "offscreen"),
            ("frozen_demo_smoke", "frozen", False),
            ("frozen_demo_smoke", "platform", "darwin"),
            ("frozen_demo_smoke", "application_version", "2.0.0-preview.999"),
        ]
        for section, key, value in cases:
            with self.subTest(section=section, field=key):
                info = copy.deepcopy(self.info)
                if key is None:
                    info[section] = value
                else:
                    info[section][key] = value
                self.write_assets(info)
                with self.assertRaises(ValueError):
                    publisher.verify_assets(self.assets, self.SHA)

    def test_rejects_remote_assets_with_wrong_hash_size_state_or_missing_file(self):
        self.write_assets()
        _, files = publisher.verify_assets(self.assets, self.SHA)
        for key, value in (("digest", "sha256:" + "0" * 64), ("size", 0), ("state", "starter")):
            with self.subTest(field=key):
                release = self.remote_assets(files)
                release["assets"][0][key] = value
                with self.assertRaisesRegex(ValueError, "remote release asset"):
                    publisher.verify_uploaded(release, files)
        with self.assertRaisesRegex(ValueError, "remote release asset"):
            publisher.verify_uploaded({"assets": []}, files)

    def test_resolves_only_the_exact_tag_and_follows_annotated_tags(self):
        reference = "refs/tags/" + publisher.TAG
        nearby = {"ref": reference + "-other", "object": {"type": "commit", "sha": "b" * 40}}
        direct = {"ref": reference, "object": {"type": "commit", "sha": self.SHA}}
        with patch.object(publisher, "api", return_value=[nearby]) as api:
            self.assertIsNone(publisher.tag_commit())
            api.assert_called_once_with("git/matching-refs/tags/" + publisher.TAG)
        with patch.object(publisher, "api", return_value=[nearby, direct]):
            self.assertEqual(publisher.tag_commit(), self.SHA)
        annotated = {"ref": reference, "object": {"type": "tag", "sha": "c" * 40}}
        with patch.object(publisher, "api", side_effect=[
            [annotated], {"object": {"type": "commit", "sha": self.SHA}},
        ]) as api:
            self.assertEqual(publisher.tag_commit(), self.SHA)
            self.assertEqual(api.call_args_list[-1].args, ("git/tags/" + "c" * 40,))
        invalid = {"ref": reference, "object": {"type": "blob", "sha": "d" * 40}}
        with patch.object(publisher, "api", return_value=[invalid]):
            with self.assertRaisesRegex(ValueError, "does not resolve to a commit"):
                publisher.tag_commit()

    def test_rejects_an_unauthorized_repository_before_reading_or_publishing(self):
        with patch.object(sys, "argv", ["publish_windows_preview.py"]), patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "oreochromis/pypydance-video-cache"}, clear=True,
        ), patch.object(publisher, "verify_assets") as verify, patch.object(publisher, "gh") as gh:
            with self.assertRaisesRegex(SystemExit, "authorized Fork"):
                publisher.main()
            verify.assert_not_called()
            gh.assert_not_called()

    def test_refuses_to_replace_an_existing_preview_tag_at_another_commit(self):
        self.write_assets()
        environment = {
            "GITHUB_REPOSITORY": publisher.REPOSITORY, "GITHUB_SHA": self.SHA,
            "GITHUB_ACTIONS": "true", "GH_TOKEN": "synthetic-test-token",
        }
        arguments = ["publish_windows_preview.py", "--assets", str(self.assets)]
        with patch.object(sys, "argv", arguments), patch.dict(os.environ, environment, clear=True), \
                patch.object(publisher, "tag_commit", return_value="b" * 40), \
                patch.object(publisher, "api") as api, patch.object(publisher, "gh") as gh, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "will not be overwritten"):
                publisher.main()
            api.assert_not_called()
            gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
