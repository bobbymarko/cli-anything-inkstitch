"""Inkscape binary discovery and SVG → PNG rasterization.

Used by `preview generate --raster` to convert stitch-plan SVGs into images
the LLM can visually consume. Inkscape 1.0+ CLI flags are required (we test
against 1.4).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from cli_anything_inkstitch.errors import BinaryError

SEARCH_PATHS = {
    "darwin": [
        "/Applications/Inkscape.app/Contents/MacOS/inkscape",
        str(Path.home() / "Applications/Inkscape.app/Contents/MacOS/inkscape"),
    ],
    "linux": [
        "/usr/bin/inkscape",
        "/usr/local/bin/inkscape",
        "/snap/bin/inkscape",
    ],
    "win32": [
        r"C:\Program Files\Inkscape\bin\inkscape.exe",
        r"C:\Program Files (x86)\Inkscape\bin\inkscape.exe",
    ],
}


def discover() -> str | None:
    """Locate the inkscape binary. Returns None if not found.

    Resolution order:
      1. INKSCAPE_BINARY environment variable
      2. `inkscape` on PATH
      3. Platform-specific install paths
    """
    env = os.environ.get("INKSCAPE_BINARY")
    if env and Path(env).exists():
        return env
    on_path = shutil.which("inkscape") or shutil.which("inkscape.exe")
    if on_path:
        return on_path
    system = platform.system().lower()
    key = "darwin" if system == "darwin" else ("win32" if system.startswith("win") else "linux")
    for candidate in SEARCH_PATHS.get(key, []):
        if Path(candidate).exists():
            return candidate
    return None


def rasterize(svg_path: str, png_path: str, dpi: int = 150,
              timeout: float = 120) -> int:
    """Convert an SVG file to PNG via Inkscape. Returns the PNG file size.

    Raises BinaryError if Inkscape isn't found or rasterization fails.
    """
    binary = discover()
    if not binary:
        raise BinaryError(
            "inkscape", 127,
            "Inkscape not found. Install Inkscape 1.0+ or set INKSCAPE_BINARY. "
            "Required for --raster output."
        )
    Path(png_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        binary,
        svg_path,
        "--export-type=png",
        f"--export-filename={png_path}",
        f"--export-dpi={dpi}",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise BinaryError("inkscape", 124,
                          f"rasterize timeout after {timeout}s") from e
    if result.returncode != 0:
        raise BinaryError(
            "inkscape", result.returncode,
            f"Inkscape failed to rasterize: {result.stderr.decode('utf-8', 'replace')[:500]}"
        )
    if not Path(png_path).exists():
        raise BinaryError(
            "inkscape", 1,
            f"Inkscape exited 0 but PNG was not written: {png_path}"
        )
    return Path(png_path).stat().st_size
