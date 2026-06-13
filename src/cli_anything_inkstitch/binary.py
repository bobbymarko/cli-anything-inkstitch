"""Inkstitch binary discovery + invocation."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from cli_anything_inkstitch.errors import BinaryError

SEARCH_PATHS = {
    "darwin": [
        "/Applications/Ink Stitch.app/Contents/MacOS/inkstitch",
        str(Path.home() / "Applications/Ink Stitch.app/Contents/MacOS/inkstitch"),
    ],
    "linux": [
        "/opt/inkstitch/bin/inkstitch",
        "/usr/local/bin/inkstitch",
    ],
    "win32": [
        r"C:\Program Files\Ink Stitch\inkstitch.exe",
        r"C:\Program Files (x86)\Ink Stitch\inkstitch.exe",
    ],
}


def discover(explicit: str | None = None, project_session: dict | None = None) -> str | None:
    if explicit:
        return explicit
    env = os.environ.get("INKSTITCH_BINARY")
    if env:
        return env
    if project_session and project_session.get("inkstitch_binary"):
        return project_session["inkstitch_binary"]
    on_path = shutil.which("inkstitch") or shutil.which("inkstitch.exe")
    if on_path:
        return on_path
    system = platform.system().lower()
    key = "darwin" if system == "darwin" else ("win32" if system.startswith("win") else "linux")
    for candidate in SEARCH_PATHS.get(key, []):
        if Path(candidate).exists():
            return candidate
    return None


def detect_binary_version(binary: str | None) -> str | None:
    """Best-effort Ink/Stitch version from the VERSION file shipped with the binary.

    Candidate locations mirror inkstitch's own get_bundled_dir() resolution:
    macOS app bundles put VERSION in Contents/Resources (binary is in
    Contents/MacOS); Linux/Windows onedir builds put it next to or one level
    above the executable (pyinstaller 6 uses an _internal data dir).
    Returns the normalized version string (no leading 'v') or None.
    """
    if not binary:
        return None
    b = Path(binary)
    if not b.exists():
        return None
    candidates = [
        b.parent / "VERSION",
        b.parent / "_internal" / "VERSION",
        b.parent.parent / "VERSION",
        b.parent.parent / "Resources" / "VERSION",
    ]
    for c in candidates:
        try:
            if not c.is_file():
                continue
            first_line = c.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            if first_line and first_line[0].strip():
                return first_line[0].strip().lstrip("v")
        except OSError:
            continue
    return None


def installed_version(explicit: str | None = None, project_session: dict | None = None) -> str | None:
    """Version of the Ink/Stitch binary that discovery would pick, or None."""
    return detect_binary_version(discover(explicit, project_session))


def require(explicit: str | None = None, project_session: dict | None = None) -> str:
    found = discover(explicit, project_session)
    if not found:
        raise BinaryError(
            "(discovery)",
            127,
            "Ink/Stitch binary not found. Pass --inkstitch-binary, set INKSTITCH_BINARY, "
            "or install from https://inkstitch.org/docs/install/",
        )
    return found


def run_extension(
    binary: str,
    extension: str,
    svg_path: str,
    args: dict | None = None,
    ids: list[str] | None = None,
    capture_stdout: bool = False,
    timeout: float = 300,
) -> bytes | None:
    cmd = [binary, f"--extension={extension}"]
    for k, v in (args or {}).items():
        cmd.append(f"--{k}={v}")
    for ident in ids or []:
        cmd.append(f"--id={ident}")
    cmd.append(svg_path)
    env = {**os.environ, "INKSTITCH_OFFLINE_SCRIPT": "true"}
    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise BinaryError(extension, 124, f"timeout after {timeout}s") from e
    except FileNotFoundError as e:
        raise BinaryError(extension, 127, f"binary not executable: {binary}") from e
    if result.returncode != 0:
        raise BinaryError(extension, result.returncode, result.stderr.decode("utf-8", "replace"))
    return result.stdout if capture_stdout else None
