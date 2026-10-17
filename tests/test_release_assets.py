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

from tools import package_windows as packager
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
            "build_platform": "Windows synthetic test fixture",
            "isolated_tests": {
                "status": "passed", "platform": "win32", "tests_run": 3, "passed": 3,
                "failures": [], "errors": [], "skipped": [],
                "expected_failures": [], "unexpected_successes": [],
            },
            "frozen_demo_smoke": {
                "status": "passed", "frozen": True, "platform": "win32",
                "qt_platform": "windows", "application_version": publisher.TAG.removeprefix("v"),
                "theme_id": "light", "checks": sorted(publisher.REQUIRED_SMOKE_CHECKS),
                "qt_version": "6.synthetic", "pillow_version": "synthetic",
            },
        }

    def write_assets(self, info=None, extra_files=None):
        info = copy.deepcopy(self.info if info is None else info)
        text = json.dumps(info, ensure_ascii=False)
        archive = self.assets / "PyPyDanceCache-windows-x64.zip"
        with ZipFile(archive, "w") as zipped:
            for name in ("PyPyDanceCache.exe", "_internal/python312.dll", "打开演示.bat"):
                zipped.writestr("PyPyDanceCache/" + name, b"tiny synthetic fixture")
            for name, contents in (extra_files or {}).items():
                zipped.writestr("PyPyDanceCache/" + name, contents)
            zipped.writestr("PyPyDanceCache/docs/build-info.json", text)
        (self.assets / "build-info.json").write_text(text, encoding="utf-8")
        (self.assets / (archive.name + ".sha256")).write_text(
            publisher.digest(archive) + "  " + archive.name + "\n", encoding="ascii",
        )
        return info

    def publication_environment(self, **overrides):
        event = self.assets / "github-event.json"
        event.write_text(json.dumps({
            "after": self.SHA, "head_commit": {"message": "Release 2.0.0 [publish-release]"},
        }), encoding="utf-8")
        environment = {
            "GITHUB_REPOSITORY": publisher.REPOSITORY, "GITHUB_SHA": self.SHA,
            "GITHUB_ACTIONS": "true", "GH_TOKEN": "synthetic-test-token",
            "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_PATH": str(event), "GITHUB_RUN_ID": "synthetic-run",
        }
        environment.update(overrides)
        return environment

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
            ("frozen_demo_smoke", "theme_id", "custom-theme"),
            ("frozen_demo_smoke", "checks", ["demo_mode"]),
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

    def test_rejects_bundled_presets_even_with_a_valid_checksum(self):
        for name in (
            "_internal/dance_ui/presets/xiaoyu12/theme.json",
            "_internal/dance_ui/PRESETS/xiaoyu12/background.png",
            "_internal\\dance_ui\\presets\\xiaoyu12\\background.png",
        ):
            with self.subTest(name=name):
                self.write_assets(extra_files={name: b"excluded preset"})
                with self.assertRaisesRegex(ValueError, "must not include bundled preset"):
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

    def test_refuses_to_replace_an_existing_release_tag_at_another_commit(self):
        self.write_assets()
        environment = self.publication_environment()
        arguments = ["publish_windows_preview.py", "--assets", str(self.assets)]
        with patch.object(sys, "argv", arguments), patch.dict(os.environ, environment, clear=True), \
                patch.object(publisher, "tag_commit", return_value="b" * 40), \
                patch.object(publisher, "api") as api, patch.object(publisher, "gh") as gh, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "will not be overwritten"):
                publisher.main()
            api.assert_not_called()
            gh.assert_not_called()

    def test_only_an_explicit_exact_main_push_can_publish(self):
        cases = (
            {"GITHUB_REF": "refs/heads/ui-redesign"},
            {"GITHUB_REF": "refs/tags/v2.0.0"},
            {"GITHUB_EVENT_NAME": "workflow_dispatch"},
            {"GITHUB_ACTIONS": "false"},
            {"GH_TOKEN": ""},
            {"GITHUB_SHA": "b" * 40},
            {"GITHUB_EVENT_PATH": str(self.assets / "missing-event.json")},
        )
        for override in cases:
            with self.subTest(override=override):
                environment = self.publication_environment(**override)
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(SystemExit):
                        publisher.verify_publication_request()
        for message in ("Release 2.0.0", "Release [publish-preview]"):
            environment = self.publication_environment()
            Path(environment["GITHUB_EVENT_PATH"]).write_text(json.dumps({
                "after": self.SHA, "head_commit": {"message": message},
            }), encoding="utf-8")
            with self.subTest(message=message), patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(SystemExit, "explicitly request"):
                    publisher.verify_publication_request()
        with patch.dict(os.environ, self.publication_environment(), clear=True):
            publisher.verify_publication_request()

    def test_publishes_verified_draft_as_a_stable_latest_release(self):
        self.write_assets()
        _, files = publisher.verify_assets(self.assets, self.SHA)
        uploaded = self.remote_assets(files) | {
            "id": 7, "tag_name": publisher.TAG, "html_url": "https://example.invalid/release",
        }
        draft = uploaded | {"draft": True, "prerelease": False}
        published = uploaded | {"draft": False, "prerelease": False}
        with patch.object(sys, "argv", ["publish_windows_preview.py", "--assets", str(self.assets)]), \
                patch.dict(os.environ, self.publication_environment(), clear=True), \
                patch.object(publisher, "tag_commit", side_effect=[None, None, self.SHA]), \
                patch.object(publisher, "api", side_effect=[[], [draft], published, published]), \
                patch.object(publisher, "gh") as gh, redirect_stdout(io.StringIO()):
            self.assertEqual(publisher.main(), 0)
        create, upload, publish = [call.args for call in gh.call_args_list]
        self.assertEqual(create[:3], ("release", "create", "v2.0.0"))
        self.assertIn("--draft=true", create)
        self.assertIn("--prerelease=false", create)
        self.assertEqual(upload[:3], ("release", "upload", "v2.0.0"))
        self.assertEqual(publish, (
            "release", "edit", "v2.0.0", "--draft=false", "--prerelease=false", "--latest=true",
        ))

    def test_does_not_publish_a_draft_if_uploaded_assets_differ(self):
        self.write_assets()
        draft = {"id": 7, "tag_name": publisher.TAG, "draft": True, "assets": []}
        with patch.object(sys, "argv", ["publish_windows_preview.py", "--assets", str(self.assets)]), \
                patch.dict(os.environ, self.publication_environment(), clear=True), \
                patch.object(publisher, "tag_commit", return_value=None), \
                patch.object(publisher, "api", side_effect=[[], [draft]]), \
                patch.object(publisher, "gh") as gh, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "remote release asset"):
                publisher.main()
        self.assertEqual(len(gh.call_args_list), 2)
        self.assertFalse(any("--draft=false" in call.args for call in gh.call_args_list))

    def test_keeps_a_draft_private_if_the_tag_changes_during_upload(self):
        self.write_assets()
        _, files = publisher.verify_assets(self.assets, self.SHA)
        draft = self.remote_assets(files) | {"tag_name": publisher.TAG, "draft": True}
        with patch.object(sys, "argv", ["publish_windows_preview.py", "--assets", str(self.assets)]), \
                patch.dict(os.environ, self.publication_environment(), clear=True), \
                patch.object(publisher, "tag_commit", side_effect=[None, "b" * 40]), \
                patch.object(publisher, "api", side_effect=[[], [draft]]), \
                patch.object(publisher, "gh") as gh, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "changed during upload"):
                publisher.main()
        self.assertEqual(len(gh.call_args_list), 2)
        self.assertFalse(any("--draft=false" in call.args for call in gh.call_args_list))

    def test_never_overwrites_an_existing_published_release(self):
        self.write_assets()
        _, files = publisher.verify_assets(self.assets, self.SHA)
        published = self.remote_assets(files) | {
            "tag_name": publisher.TAG, "draft": False, "prerelease": False,
            "html_url": "https://example.invalid/release",
        }
        with patch.object(sys, "argv", ["publish_windows_preview.py", "--assets", str(self.assets)]), \
                patch.dict(os.environ, self.publication_environment(), clear=True), \
                patch.object(publisher, "tag_commit", return_value=self.SHA), \
                patch.object(publisher, "api", side_effect=[[published], published]), \
                patch.object(publisher, "gh") as gh, redirect_stdout(io.StringIO()):
            self.assertEqual(publisher.main(), 0)
            gh.assert_not_called()

    def test_packager_excludes_presets_and_records_user_feedback_separately(self):
        bundle = self.assets / "PyPyDanceCache"
        for name in (
            "PyPyDanceCache.exe", "_internal/python312.dll", "README.md", "LICENSE",
            "docs/DEVELOPMENT.md", "docs/WINDOWS_PREVIEW.md", "启动.bat", "打开演示.bat",
        ):
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("synthetic package fixture", encoding="utf-8")
        (bundle / "THIRD_PARTY_LICENSES").mkdir()
        reports = self.assets / "reports"
        reports.mkdir()
        for name, value in (
            ("unit-tests.json", self.info["isolated_tests"]),
            ("frozen-smoke.json", self.info["frozen_demo_smoke"]),
        ):
            (reports / name).write_text(json.dumps(value), encoding="utf-8")
        for name in ("dependencies.txt", "unit-tests.log"):
            (reports / name).write_text("synthetic report", encoding="utf-8")
        output = self.assets / "packaged"
        arguments = ["package_windows.py", "--bundle", str(bundle), "--reports", str(reports),
                     "--output", str(output)]
        with patch.object(sys, "argv", arguments), patch.object(sys, "platform", "win32"), \
                patch.object(packager.platform, "architecture", return_value=("64bit", "WindowsPE")), \
                patch.object(packager.platform, "platform", return_value="Windows synthetic fixture"), \
                patch.object(packager.subprocess, "check_output", return_value=self.SHA + "\n"), \
                patch.dict(os.environ, {"GITHUB_SHA": self.SHA}, clear=True), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(packager.main(), 0)
            info, _ = publisher.verify_assets(output, self.SHA)
            self.assertEqual(info["user_reported_validation"]["environment"], "Windows running VRChat")
            self.assertEqual(info["frozen_demo_smoke"], self.info["frozen_demo_smoke"])
            self.assertTrue(info["not_verified"])
            preset = bundle / "_internal/dance_ui/presets/xiaoyu12/background.png"
            preset.parent.mkdir(parents=True)
            preset.write_bytes(b"excluded large image fixture")
            with self.assertRaisesRegex(SystemExit, "must not include bundled preset"):
                packager.main()


if __name__ == "__main__":
    unittest.main()
