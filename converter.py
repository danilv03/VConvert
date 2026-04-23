#!/usr/bin/env python3
"""
YouTube Video/Audio Converter

Install dependencies:
  pip install yt-dlp

Install FFmpeg on macOS:
  brew install ffmpeg

Example usage:
  python3 converter.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --format mp3
  python3 converter.py "https://youtu.be/dQw4w9WgXcQ" --format wav
  python3 converter.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --format m4a
  python3 converter.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --format mp4
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError
except ImportError:
    print(
        "Error: yt-dlp is not installed.\n"
        "Install it with: pip install yt-dlp",
        file=sys.stderr,
    )
    sys.exit(1)


SUPPORTED_FORMATS = {"mp3", "wav", "m4a", "mp4"}
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
PROGRESS_BAR_WIDTH = 32


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a YouTube video and convert it to audio or MP4."
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "--format",
        required=True,
        choices=sorted(SUPPORTED_FORMATS),
        help="Output format: mp3, wav, m4a, or mp4",
    )
    return parser.parse_args()


def validate_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    hostname = (parsed.hostname or "").lower()
    valid_hosts = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
    if hostname not in valid_hosts:
        return False

    if hostname.endswith("youtu.be"):
        return bool(parsed.path.strip("/"))

    if parsed.path == "/watch":
        query = parse_qs(parsed.query)
        return bool(query.get("v", [""])[0].strip())

    return parsed.path.startswith(("/shorts/", "/live/", "/embed/"))


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "FFmpeg is not installed or not in your PATH. "
            "Install it on macOS with: brew install ffmpeg"
        )


def format_bytes(num_bytes: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def sanitize_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\s.-]", "", title, flags=re.ASCII)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.replace(" ", "_")
    return cleaned[:200] or "download"


class ProgressPrinter:
    def __init__(self) -> None:
        self._last_line_length = 0

    def hook(self, status: dict[str, Any]) -> None:
        state = status.get("status")
        if state == "downloading":
            downloaded = status.get("downloaded_bytes", 0)
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            speed = status.get("speed")
            eta = status.get("eta")

            if total:
                percent = min(max(downloaded / total, 0.0), 1.0)
                filled = int(PROGRESS_BAR_WIDTH * percent)
                bar = "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)
                percent_text = f"{percent * 100:6.2f}%"
                total_text = format_bytes(total)
            else:
                bar = "#" * (PROGRESS_BAR_WIDTH // 2) + "-" * (PROGRESS_BAR_WIDTH // 2)
                percent_text = "  ??.??%"
                total_text = "unknown"

            speed_text = format_bytes(speed) + "/s" if speed else "--"
            eta_text = f"{int(eta)}s" if eta is not None else "--"
            line = (
                f"\rDownloading [{bar}] {percent_text}  "
                f"{format_bytes(downloaded)}/{total_text}  "
                f"Speed: {speed_text}  ETA: {eta_text}"
            )
            self._write(line)
        elif state == "finished":
            self._write("\rDownload complete. Processing with FFmpeg...".ljust(self._last_line_length))
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._last_line_length = 0

    def _write(self, text: str) -> None:
        self._last_line_length = max(self._last_line_length, len(text))
        sys.stdout.write(text.ljust(self._last_line_length))
        sys.stdout.flush()


def build_postprocessors(selected_format: str) -> list[dict[str, Any]]:
    if selected_format == "mp3":
        return [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ]
    if selected_format == "wav":
        return [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]
    if selected_format == "m4a":
        return [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    return [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]


def build_ydl_options(selected_format: str, progress: ProgressPrinter) -> dict[str, Any]:
    output_template = str(OUTPUT_DIR / "%(title)s [%(id)s].%(ext)s")

    options: dict[str, Any] = {
        "outtmpl": output_template,
        "paths": {"home": str(OUTPUT_DIR)},
        "restrictfilenames": True,
        "noplaylist": True,
        "progress_hooks": [progress.hook],
        "postprocessors": build_postprocessors(selected_format),
        "merge_output_format": "mp4" if selected_format == "mp4" else None,
        "quiet": True,
        "no_warnings": True,
    }

    if selected_format == "mp4":
        options["format"] = "bestvideo[height<=1080]+bestaudio/best"
    else:
        options["format"] = "bestaudio/best"

    return options


def resolve_final_path(info: dict[str, Any], selected_format: str) -> Path:
    title = sanitize_title(info.get("title", "download"))
    video_id = info.get("id", "unknown")
    final_name = f"{title} [{video_id}].{selected_format}"
    return OUTPUT_DIR / final_name


def download_and_convert(url: str, selected_format: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_ffmpeg_available()

    progress = ProgressPrinter()
    ydl_opts = build_ydl_options(selected_format, progress)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected error during download/conversion: {exc}") from exc

    final_path = resolve_final_path(info, selected_format)
    if not final_path.exists():
        suffix = f" [{info.get('id', '')}].{selected_format}"
        matches = sorted(path for path in OUTPUT_DIR.iterdir() if path.name.endswith(suffix))
        if matches:
            final_path = matches[0]

    return final_path


def main() -> int:
    args = parse_args()
    url = args.url.strip()
    selected_format = args.format.lower()

    if not validate_youtube_url(url):
        print(
            "Error: Invalid YouTube URL.\n"
            "Please provide a valid youtube.com or youtu.be video URL.",
            file=sys.stderr,
        )
        return 1

    if selected_format not in SUPPORTED_FORMATS:
        print(
            "Error: Unsupported format. Choose one of: mp3, wav, m4a, mp4.",
            file=sys.stderr,
        )
        return 1

    try:
        final_path = download_and_convert(url, selected_format)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if final_path.exists():
        print(f"Success: File saved to {final_path}")
        return 0

    print(
        "Warning: Download reported success, but the final file could not be confirmed.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
