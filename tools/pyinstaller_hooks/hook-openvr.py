"""Bundle only the Windows x64 OpenVR library used by the preview executable."""
from pathlib import Path

from PyInstaller.compat import is_win
from PyInstaller.utils.hooks import get_package_paths


binaries = []
if is_win:
    _, package_dir = get_package_paths("openvr")
    library = Path(package_dir) / "libopenvr_api_64.dll"
    if not library.is_file():
        raise RuntimeError("The Windows x64 OpenVR library is missing: " + str(library))
    # pyopenvr requests the extensionless resource libopenvr_api_64. Windows
    # LoadLibrary adds .dll; preserve its package directory and real filename.
    binaries = [(str(library), "openvr")]
