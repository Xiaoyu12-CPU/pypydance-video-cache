"""Validate the exact built-in video shipped with the stable application."""
from __future__ import annotations

import hashlib
from pathlib import Path


COOLDOWN_RELATIVE_PATH = "assets/default_cooldown.mp4"
COOLDOWN_ARCHIVE_PATH = "PyPyDanceCache/_internal/" + COOLDOWN_RELATIVE_PATH
COOLDOWN_MAX_BYTES = 5 * 1024 * 1024
# The approved user clip is bundled byte for byte, without recompression.
COOLDOWN_SIZE_BYTES = 5_073_920
COOLDOWN_SHA256 = "453650779b315339a1c68b29a4b486d9fe0c85b49b3c8e0a5af94ff770d7b135"


def bundled_cooldown_path() -> Path:
    """PyInstaller places package modules and assets under the same _internal root."""
    return Path(__file__).resolve().parents[1] / COOLDOWN_RELATIVE_PATH


def validate_cooldown_bytes(contents: bytes) -> dict:
    """Reject missing, substituted, oversized, or non-MP4 release resources."""
    if not 12 <= len(contents) <= COOLDOWN_MAX_BYTES:
        raise ValueError("The bundled cooldown video must be nonempty and at most 5 MiB (5,242,880 bytes).")
    box_size = int.from_bytes(contents[:4], "big")
    if contents[4:8] != b"ftyp" or not 12 <= box_size <= len(contents):
        raise ValueError("The bundled cooldown video has no valid ISO MP4 ftyp signature.")
    digest = hashlib.sha256(contents).hexdigest()
    if len(contents) != COOLDOWN_SIZE_BYTES or digest != COOLDOWN_SHA256:
        raise ValueError("The bundled cooldown video differs from the approved source resource.")
    return {
        "relative_path": COOLDOWN_RELATIVE_PATH,
        "size_bytes": len(contents),
        "sha256": digest,
    }


def describe_cooldown(path: Path | None = None) -> dict:
    return validate_cooldown_bytes((path or bundled_cooldown_path()).read_bytes())
