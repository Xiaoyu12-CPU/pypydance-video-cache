"""Publish verified stable assets from an explicitly requested main-branch build.

The historical script name remains for workflow and tooling compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from zipfile import ZipFile


REPOSITORY = "Xiaoyu12-CPU/pypydance-video-cache"
TAG = "v2.0.0"
TITLE = "PyPyDance Cache 2.0.0（Windows 正式版）"
PUBLISH_MARKER = "[publish-release]"
REQUIRED_SMOKE_CHECKS = {
    "demo_mode", "builtin_light_theme", "pillow_generated_background",
    "embedded_svg_icons", "four_page_navigation_and_rendering",
    "openvr_library_loaded_without_initialization",
}


def gh(*arguments: str) -> str:
    return subprocess.check_output(["gh", *arguments, "--repo", REPOSITORY], text=True)


def api(path: str) -> dict | list:
    # gh api uses --hostname rather than the release commands' --repo option.
    result = subprocess.check_output(["gh", "api", "repos/" + REPOSITORY + "/" + path], text=True)
    return json.loads(result)


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify_assets(assets: Path, expected_sha: str) -> tuple[dict, list[Path]]:
    archive = assets / "PyPyDanceCache-windows-x64.zip"
    checksum = assets / (archive.name + ".sha256")
    info_path = assets / "build-info.json"
    if digest(archive) != checksum.read_text(encoding="ascii").split()[0]:
        raise ValueError("The release ZIP does not match its checksum.")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if info["git_sha"] != expected_sha:
        raise ValueError("The artifact was not built from this workflow's exact commit.")
    tests, smoke = info["isolated_tests"], info["frozen_demo_smoke"]
    if (tests["status"] != "passed" or tests["platform"] != "win32"
            or tests["tests_run"] < 1 or tests["passed"] != tests["tests_run"]
            or any(tests[key] for key in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))):
        raise ValueError("A complete Windows test run without failures or skips is required.")
    if (smoke["status"] != "passed" or smoke["frozen"] is not True
            or smoke["platform"] != "win32" or smoke["qt_platform"] != "windows"
            or "v" + smoke["application_version"] != TAG
            or smoke.get("theme_id") != "light"
            or not REQUIRED_SMOKE_CHECKS.issubset(smoke.get("checks", []))):
        raise ValueError("The frozen Windows executable has not passed the expected stable checks.")
    with ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("The release ZIP is damaged.")
        members = zipped.namelist()
        if any("presets" in tuple(part.casefold() for part in Path(name.replace("\\", "/")).parts)
               for name in members):
            raise ValueError("Stable release assets must not include bundled preset resources.")
        for path in ("PyPyDanceCache.exe", "_internal/python312.dll", "docs/build-info.json", "打开演示.bat"):
            if "PyPyDanceCache/" + path not in members:
                raise ValueError("The release ZIP is incomplete: " + path)
        if json.loads(zipped.read("PyPyDanceCache/docs/build-info.json")) != info:
            raise ValueError("The bundled and attached build reports differ.")
    return info, [archive, checksum, info_path]


def verify_uploaded(release: dict, files: list[Path]) -> None:
    uploaded = {asset["name"]: asset for asset in release["assets"]}
    for file in files:
        asset = uploaded.get(file.name)
        if (asset is None or asset["state"] != "uploaded" or asset["size"] != file.stat().st_size
                or asset.get("digest") != "sha256:" + digest(file)):
            raise ValueError("The remote release asset is missing or does not match: " + file.name)


def tag_commit() -> str | None:
    refs = api("git/matching-refs/tags/" + TAG)
    exact = [ref for ref in refs if ref["ref"] == "refs/tags/" + TAG]
    if not exact:
        return None
    target = exact[0]["object"]
    for _ in range(4):
        if target["type"] == "commit":
            return target["sha"]
        if target["type"] != "tag":
            break
        target = api("git/tags/" + target["sha"])["object"]
    raise ValueError("The release tag does not resolve to a commit.")


def notes(info: dict, checksum: str) -> str:
    smoke, tests = info["frozen_demo_smoke"], info["isolated_tests"]
    run_url = f"https://github.com/{REPOSITORY}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return f"""PyPyDance Cache 2.0.0 Windows x64 正式版。用户已在 Windows 上运行 VRChat 并反馈程序能够正常工作。

## 本版功能

- Qt for Python 桌面界面，提供运行、存储、设置、外观四页。
- 测速后可将 CDN 保存至本机“常用 CDN 收藏列表”，支持选择使用与删除。
- 设置页提供“跟随 SteamVR 启动”选项；需要先运行 SteamVR，再应用启动设置。
- 新配置默认使用内置浅蓝主题。本包不附带“小余12主题”及其背景，减少下载体积；外观页仍支持自定义主题和图片，已有本地外观配置保留。

## 下载与启动

下载下方 **PyPyDanceCache-windows-x64.zip**，完整解压后进入 `PyPyDanceCache` 文件夹，无需安装 Python。

- 先双击 `打开演示.bat` 检查四页界面；演示不启动真实缓存服务。
- 正常使用时关闭演示，再双击 `PyPyDanceCache.exe`，按 UAC 提示启动；正式入口会自动启动缓存服务。
- 必须保留 `_internal` 等全部文件，不能只复制 exe；建议先使用账户可写的独立测试目录。
- 更改目录、YouTube 缓存开关前先停止服务，应用后再启动。正常关闭后检查 hosts 恢复，失败时保留窗口和日志。

完整步骤见包内 `docs/WINDOWS_PREVIEW.md`。程序未签名，UAC 发布者显示为未知。

## 本次实际验证

- 原生 Windows x64 构建：{info['build_platform']}。
- Windows 完整隔离测试：**{tests['tests_run']} 项通过，失败 0、错误 0、跳过 0**。
- 实际打包 exe 以 Windows Qt 平台启动演示并自动退出；内置浅蓝主题、四页导航渲染、SVG 图标、合成图片的 Pillow 背景渲染及 OpenVR DLL 加载检查均通过。演示检查不启动真实服务或 SteamVR。
- Qt / PySide6 {smoke['qt_version']}，Pillow {smoke['pillow_version']}。完整环境与报告见附件及包内 `docs/build-info.json`。
- [本次 Windows 构建与测试记录]({run_url})。

**用户人工反馈：**用户已在 Windows + VRChat 环境运行并验证程序能够正常工作；这是发布前开发版本的人工反馈，与上述本次构建自动测试分别记录。

**仍需专项验证：**SteamVR 自动拉起与 UAC 流程、退出后 hosts 恢复、不同 CDN/YouTube 缓存场景、旧配置/曲库迁移及不同 Windows 缩放设置。用户的一般运行反馈不表示这些细项均已逐项验收。

源码提交：`{info['git_sha']}`（`main`）。

ZIP SHA-256：

```text
{checksum}
```
"""


def verify_publication_request() -> None:
    """Require an explicit main push, even if this script is called separately."""
    if (os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_EVENT_NAME") != "push"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise SystemExit("Publishing requires an explicitly requested main-branch push workflow.")
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError) as error:
        raise SystemExit("The release request event cannot be verified.") from error
    if (PUBLISH_MARKER not in (event.get("head_commit") or {}).get("message", "")
            or event.get("after") != os.environ.get("GITHUB_SHA")):
        raise SystemExit("The exact main push must explicitly request " + PUBLISH_MARKER + ".")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=Path("release"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    repository = os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
    if repository != REPOSITORY:
        raise SystemExit("This publisher is restricted to the authorized Fork.")
    expected_sha = os.environ.get("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    info, files = verify_assets(args.assets, expected_sha)
    print(f"Verified Windows assets from {expected_sha}", flush=True)
    if args.check_only:
        return 0
    verify_publication_request()
    existing_tag = tag_commit()
    if existing_tag is not None and existing_tag != expected_sha:
        raise SystemExit("The release tag already points elsewhere; it will not be overwritten.")
    releases = api("releases?per_page=100")
    matching = [release for release in releases if release["tag_name"] == TAG]
    if len(matching) > 1:
        raise SystemExit("More than one matching release was returned.")
    if matching and not matching[0]["draft"]:
        verify_uploaded(matching[0], files)
        if existing_tag != expected_sha or matching[0]["prerelease"]:
            raise SystemExit("The published release tag or stable state differs from the build.")
        if api("releases/latest")["tag_name"] != TAG:
            raise SystemExit("The published stable release is not the latest release.")
        print("Already published: " + matching[0]["html_url"], flush=True)
        return 0
    notes_path = args.assets / "release-notes.md"
    notes_path.write_text(notes(info, digest(files[0])), encoding="utf-8")
    command = "edit" if matching else "create"
    gh("release", command, TAG, "--target", expected_sha, "--draft=true", "--prerelease=false",
       "--latest=false", "--title", TITLE, "--notes-file", str(notes_path))
    gh("release", "upload", TAG, *(str(file) for file in files), "--clobber")
    release = next(item for item in api("releases?per_page=100") if item["tag_name"] == TAG)
    verify_uploaded(release, files)
    ready_tag = tag_commit()
    if ready_tag is not None and ready_tag != expected_sha:
        raise SystemExit("The release tag changed during upload; the draft will not be published.")
    gh("release", "edit", TAG, "--draft=false", "--prerelease=false", "--latest=true")
    release = api("releases/" + str(release["id"]))
    verify_uploaded(release, files)
    if (release["draft"] or release["prerelease"] or tag_commit() != expected_sha
            or api("releases/latest")["tag_name"] != TAG):
        raise SystemExit("The published release state could not be confirmed.")
    print("Published: " + release["html_url"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
