"""Create reviewable release assets from a tested native Windows onedir build."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path("dist/PyPyDanceCache"))
    parser.add_argument("--reports", type=Path, default=Path("build-reports"))
    parser.add_argument("--output", type=Path, default=Path("release"))
    args = parser.parse_args()
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise SystemExit("Release assets must be prepared with native 64-bit Windows Python.")

    bundle = args.bundle.resolve()
    if bundle.name != "PyPyDanceCache":
        raise SystemExit("The bundle root must be named PyPyDanceCache.")
    required = (
        "PyPyDanceCache.exe",
        "_internal/python312.dll",
        "_internal/dance_ui/presets/xiaoyu12/theme.json",
        "_internal/dance_ui/presets/xiaoyu12/background.png",
        "README.md",
        "LICENSE",
        "docs/DEVELOPMENT.md",
        "docs/WINDOWS_PREVIEW.md",
        "启动.bat",
        "打开演示.bat",
    )
    for relative in required:
        if not (bundle / relative).is_file():
            raise SystemExit(f"The Windows bundle is incomplete: missing {relative}")
    if not (bundle / "THIRD_PARTY_LICENSES").is_dir():
        raise SystemExit("The Windows bundle is missing third-party license materials.")

    tests = read_json(args.reports / "unit-tests.json")
    smoke = read_json(args.reports / "frozen-smoke.json")
    if tests.get("status") != "passed" or tests.get("tests_run", 0) < 1:
        raise SystemExit("The full isolated test suite has not passed.")
    if any(tests.get(key) for key in (
        "failures", "errors", "skipped", "expected_failures", "unexpected_successes",
    )):
        raise SystemExit("The full isolated test suite must pass without failures, errors, or skipped tests.")
    if (
        smoke.get("status") != "passed" or smoke.get("frozen") is not True
        or smoke.get("platform") != "win32" or smoke.get("qt_platform") != "windows"
    ):
        raise SystemExit("A passing frozen-demo smoke report using native Windows Qt is required.")

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    expected_commit = os.environ.get("GITHUB_SHA")
    if expected_commit and commit != expected_commit:
        raise SystemExit("The checkout commit differs from the GitHub Actions commit.")
    dependencies = (args.reports / "dependencies.txt").read_text(encoding="utf-8-sig").splitlines()
    build_info = {
        "application": "PyPyDanceCache",
        "asset": "PyPyDanceCache-windows-x64.zip",
        "git_sha": commit,
        "git_ref": os.environ.get("GITHUB_REF"),
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "build_platform": platform.platform(),
        "python": sys.version,
        "architecture": platform.architecture()[0],
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "dependencies": dependencies,
        "isolated_tests": tests,
        "frozen_demo_smoke": smoke,
        "not_verified": [
            "Windows manual UI interaction and display scaling",
            "Real service startup/shutdown, hosts restoration, and existing library compatibility",
            "VRChat video requests and real cache/download behavior",
        ],
        "distribution": "Extract and keep the complete PyPyDanceCache folder, including _internal.",
    }
    build_info_text = json.dumps(build_info, ensure_ascii=False, indent=2) + "\n"
    (bundle / "docs/build-info.json").write_text(build_info_text, encoding="utf-8")

    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / build_info["asset"]
    files = sorted(path for path in bundle.rglob("*") if path.is_file())
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in files:
            if path.is_symlink():
                raise SystemExit(f"Unexpected symbolic link in the Windows bundle: {path}")
            zipped.write(path, arcname=(Path(bundle.name) / path.relative_to(bundle)).as_posix())
    with zipfile.ZipFile(archive) as zipped:
        damaged = zipped.testzip()
        if damaged:
            raise SystemExit(f"Archive integrity check failed: {damaged}")
        for relative in required:
            if f"{bundle.name}/{relative}" not in zipped.namelist():
                raise SystemExit(f"Archive is missing {relative}")
    with archive.open("rb") as archive_file:
        digest = hashlib.file_digest(archive_file, "sha256").hexdigest()
    (args.output / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    (args.output / "build-info.json").write_text(build_info_text, encoding="utf-8")
    for name in ("unit-tests.json", "unit-tests.log", "frozen-smoke.json", "dependencies.txt"):
        shutil.copy2(args.reports / name, args.output / name)
    print(f"Created {archive} ({archive.stat().st_size:,} bytes, {len(files)} files)")
    print(f"SHA256: {digest}")
    print(f"Commit: {commit}")
    print(f"Tests: {tests['tests_run']} run; {len(tests.get('skipped', []))} skipped")
    print("Packaged demo smoke: passed; real Windows + VRChat verification remains pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
