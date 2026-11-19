"""FFmpeg discovery/probing and font discovery (Thai-capable fonts)."""
from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.errors import AgentError

# Thai-capable fonts, in order of preference. Docker image installs Noto Sans Thai / TLWG.
_REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
    "/usr/share/fonts/truetype/tlwg/Loma.ttf",
    "C:/Windows/Fonts/LeelawUI.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "/System/Library/Fonts/Supplemental/Thonburi.ttc",
]
_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf",
    "/usr/share/fonts/truetype/tlwg/Garuda-Bold.ttf",
    "/usr/share/fonts/truetype/tlwg/Loma-Bold.ttf",
    "C:/Windows/Fonts/LeelaUIb.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
]


def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if Path(p).is_file():
            return p
    return None


@lru_cache
def find_fonts() -> tuple[str, str]:
    """Returns (regular, bold) TTF paths with Thai coverage, or raises FONT_NOT_FOUND."""
    s = get_settings()
    regular = s.font_regular_path if s.font_regular_path and Path(s.font_regular_path).is_file() else None
    regular = regular or _first_existing(_REGULAR_CANDIDATES)
    if not regular:
        raise AgentError(
            "FONT_NOT_FOUND",
            "No Thai-capable TrueType font found. Install Noto Sans Thai or set FONT_REGULAR_PATH.",
        )
    bold = s.font_bold_path if s.font_bold_path and Path(s.font_bold_path).is_file() else None
    bold = bold or _first_existing(_BOLD_CANDIDATES) or regular
    return regular, bold


def _font_dirs() -> list[Path]:
    import os

    dirs = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/kanit"), Path("/usr/share/fonts/truetype/noto"),
            Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
    if local := os.environ.get("LOCALAPPDATA"):
        dirs.insert(0, Path(local) / "Microsoft/Windows/Fonts")
    return dirs


# Heavy display faces for posters (Kanit and Noto Sans Thai are free, OFL-licensed Thai fonts).
_DISPLAY_NAMES = ["Kanit-BlackItalic.ttf", "Kanit-ExtraBoldItalic.ttf", "NotoSansThai-Black.ttf", "ChakraPetch-BoldItalic.ttf"]
_STRONG_NAMES = ["Kanit-BoldItalic.ttf", "Kanit-SemiBoldItalic.ttf", "NotoSansThai-Bold.ttf", "ChakraPetch-SemiBoldItalic.ttf"]


def find_display_fonts() -> tuple[str, str]:
    """(display, strong) fonts for poster headlines; falls back to the regular bold Thai font."""
    s = get_settings()
    _, bold = find_fonts()

    def pick(override: str | None, names: list[str]) -> str:
        if override and Path(override).is_file():
            return override
        for name in names:
            for d in _font_dirs():
                if (d / name).is_file():
                    return str(d / name)
        return bold

    return pick(s.font_display_path, _DISPLAY_NAMES), pick(None, _STRONG_NAMES)


def find_kanit(weight: str = "Bold", italic: bool = False) -> str:
    """One upright or italic Kanit weight (e.g. "SemiBold", "Black") for the tournament poster; falls back
    to the poster display/strong fonts when Kanit is not installed."""
    name = f"Kanit-{weight}{'Italic' if italic else ''}.ttf"
    for d in _font_dirs():
        if (d / name).is_file():
            return str(d / name)
    display, strong = find_display_fonts()
    return display if weight in ("Black", "ExtraBold") else strong


@lru_cache
def find_ffmpeg() -> str:
    s = get_settings()
    if s.ffmpeg_path and Path(s.ffmpeg_path).is_file():
        return s.ffmpeg_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise AgentError("FFMPEG_NOT_FOUND", f"FFmpeg is not installed and no bundled binary is available: {exc}") from exc


def run_ffmpeg(args: list[str], timeout: int | None = None) -> str:
    cmd = [find_ffmpeg(), "-hide_banner", "-nostdin", *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout or get_settings().ffmpeg_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentError("FFMPEG_TIMEOUT", "FFmpeg timed out", retryable=True) from exc
    if proc.returncode != 0:
        raise AgentError("FFMPEG_FAILED", f"FFmpeg exited with {proc.returncode}: {proc.stderr[-800:]}")
    return proc.stderr


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*?(\d{2,5})x(\d{2,5})")
_AUDIO = re.compile(r"Stream #\d+:\d+.*?: Audio: (\w+)")


def probe_media(path: Path) -> dict:
    """Probe with `ffmpeg -i` (works with the bundled binary, which ships no ffprobe)."""
    proc = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    out = proc.stderr
    info: dict = {"duration_seconds": None, "video_codec": None, "audio_codec": None, "width": None, "height": None}
    if m := _DURATION.search(out):
        info["duration_seconds"] = round(int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]), 2)
    if m := _VIDEO.search(out):
        info.update(video_codec=m[1], width=int(m[2]), height=int(m[3]))
    if m := _AUDIO.search(out):
        info["audio_codec"] = m[1]
    return info
