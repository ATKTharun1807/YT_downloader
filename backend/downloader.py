"""
downloader.py — Core yt-dlp download logic refactored from youtube.py.

All original download reliability settings are preserved:
  - retries=20, fragment_retries=20
  - http_chunk_size=10MB
  - socket_timeout=30
  - windowsfilenames=True
  - MP4 + M4A format preference
  - FFmpegExtractAudio for MP3
  - cleanup_intermediate_files()
  - get_ffmpeg_path() via PATH or imageio-ffmpeg

This module contains NO input() or print() calls.
All communication is via return values and callbacks.
"""

import os
import re
import shutil
import logging
from typing import Callable, Optional

import yt_dlp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FFmpeg detection (preserved from youtube.py)
# ---------------------------------------------------------------------------

def get_ffmpeg_path() -> Optional[str]:
    """Find FFmpeg in system PATH or via imageio-ffmpeg."""
    path = shutil.which("ffmpeg")
    if not path:
        try:
            import imageio_ffmpeg  # type: ignore
            path = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            path = None
    return path


# ---------------------------------------------------------------------------
# Directory utilities (preserved from youtube.py)
# ---------------------------------------------------------------------------

def is_writable_dir(directory: str) -> bool:
    """Check if a directory can be created and written to."""
    try:
        os.makedirs(directory, exist_ok=True)
        test_file = os.path.join(directory, f".test_write_{os.getpid()}.tmp")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except Exception:
        return False


def get_default_download_dir() -> str:
    """Return a safe, writable download directory."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fallback = os.path.join(project_root, "downloads")
    os.makedirs(fallback, exist_ok=True)
    return fallback


# ---------------------------------------------------------------------------
# File cleanup (preserved from youtube.py)
# ---------------------------------------------------------------------------

def cleanup_intermediate_files(directory: str) -> None:
    """Remove .webm, .temp.mp4, format fragment files left by yt-dlp."""
    try:
        for fname in os.listdir(directory):
            full_path = os.path.join(directory, fname)
            if fname.endswith(".temp.mp4"):
                target = full_path.replace(".temp.mp4", ".mp4")
                if not os.path.exists(target):
                    try:
                        os.rename(full_path, target)
                        continue
                    except Exception:
                        pass
                else:
                    try:
                        os.remove(full_path)
                    except Exception:
                        pass
            elif fname.endswith(".part"):
                continue  # active download, leave it
            elif re.search(r"\.f\d+\.", fname):
                try:
                    os.remove(full_path)
                except Exception:
                    pass
            elif fname.endswith(".webm") and not fname.endswith(".final.webm"):
                try:
                    os.remove(full_path)
                except Exception:
                    pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Resolution labels (preserved from youtube.py)
# ---------------------------------------------------------------------------

RESOLUTION_LABELS: dict[int, str] = {
    2160: "4K Ultra HD (2160p)",
    1440: "2K Quad HD (1440p)",
    1080: "Full HD (1080p)",
    720: "HD (720p)",
    480: "SD (480p)",
    360: "360p",
    240: "240p",
    144: "144p",
}


def get_resolution_label(height: int) -> str:
    return RESOLUTION_LABELS.get(height, f"{height}p")


# ---------------------------------------------------------------------------
# Base yt-dlp options (preserved from youtube.py)
# ---------------------------------------------------------------------------

def _build_base_opts(noplaylist: bool = True) -> dict:
    """Build base yt-dlp options with all reliability settings."""
    opts = {
        "noplaylist": noplaylist,
        "http_chunk_size": 10485760,   # 10MB chunking
        "retries": 20,
        "fragment_retries": 20,
        "file_access_retries": 5,
        "socket_timeout": 30,
        "buffersize": 1024 * 1024,     # 1MB buffer
    }

    # Node.js for PO token / JavaScript challenges
    node_path = shutil.which("node")
    if node_path:
        opts["js_runtimes"] = {"node": {"path": node_path}}
        opts["remote_components"] = ["ejs:github"]

    ffmpeg_path = get_ffmpeg_path()
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path

    return opts


# ---------------------------------------------------------------------------
# Video info extraction
# ---------------------------------------------------------------------------

def get_video_info(url: str, noplaylist: bool = True) -> dict:
    """
    Fetch video/playlist metadata without downloading.

    Returns a structured dict suitable for JSON serialization.
    Raises RuntimeError on failure.
    """
    opts = {
        **_build_base_opts(noplaylist=noplaylist),
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "private" in msg.lower():
            raise RuntimeError("This video is private and cannot be downloaded.")
        if "unavailable" in msg.lower() or "removed" in msg.lower():
            raise RuntimeError("This video is unavailable or has been removed.")
        raise RuntimeError(f"Failed to fetch video info: {msg}")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch video info: {e}")

    return _parse_info(raw, noplaylist)


def _parse_info(raw: dict, noplaylist: bool) -> dict:
    """Parse raw yt-dlp info into clean structured dict."""
    is_playlist = raw.get("_type") == "playlist"

    if is_playlist and noplaylist:
        entries = list(raw.get("entries", []) or [])
        entries = [e for e in entries if e]
        if entries:
            raw = entries[0]
            is_playlist = False

    if is_playlist:
        entries = list(raw.get("entries", []) or [])
        entries = [e for e in entries if e]
        return {
            "is_playlist": True,
            "playlist_id": raw.get("id", ""),
            "playlist_title": raw.get("title", "Unknown Playlist"),
            "playlist_count": raw.get("playlist_count") or len(entries),
            "uploader": raw.get("uploader") or raw.get("channel", ""),
            "thumbnail": _best_thumbnail(raw),
            "entries": [_parse_single_entry(e) for e in entries[:5]],  # preview first 5
        }

    return _parse_single_entry(raw)


def _parse_single_entry(info: dict) -> dict:
    """Extract clean metadata from a single video info dict."""
    formats = info.get("formats", [])

    # Available video heights (deduplicated, sorted desc)
    heights = sorted(
        list({
            f["height"]
            for f in formats
            if f.get("height") and f.get("vcodec") != "none"
        }),
        reverse=True,
    )

    quality_options = []
    quality_options.append({
        "value": "best",
        "label": "Best Available",
        "description": "Highest resolution MP4",
    })
    for h in heights:
        quality_options.append({
            "value": str(h),
            "label": get_resolution_label(h),
            "description": f"MP4 video",
        })
    quality_options.append({
        "value": "audio",
        "label": "Audio Only — MP3 192kbps",
        "description": "Extract audio as MP3",
    })

    # Duration formatting
    duration_sec = info.get("duration") or 0
    duration_str = _format_duration(duration_sec)

    return {
        "is_playlist": False,
        "id": info.get("id", ""),
        "title": info.get("title", "Unknown Title"),
        "thumbnail": _best_thumbnail(info),
        "channel": info.get("uploader") or info.get("channel", ""),
        "duration": duration_sec,
        "duration_str": duration_str,
        "view_count": info.get("view_count"),
        "upload_date": info.get("upload_date", ""),
        "quality_options": quality_options,
        "available_heights": heights,
    }


def _best_thumbnail(info: dict) -> str:
    """Return the best available thumbnail URL."""
    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        # Prefer hq thumbnails
        for t in reversed(thumbnails):
            url = t.get("url", "")
            if url and url.startswith("http"):
                return url
    return info.get("thumbnail", "")


def _format_duration(seconds: int) -> str:
    if not seconds:
        return "Unknown"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Download options builder
# ---------------------------------------------------------------------------

def build_download_options(
    url: str,
    quality: str,
    save_dir: str,
    audio_only: bool,
    noplaylist: bool,
    progress_callback: Optional[Callable[[dict], None]] = None,
    postprocess_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """
    Build yt-dlp options dict for actual download.
    Preserves all format strings from youtube.py exactly.
    """
    outtmpl = os.path.join(save_dir, "%(title)s.%(ext)s")

    hooks = []
    if progress_callback:
        hooks.append(progress_callback)

    pp_hooks = []
    if postprocess_callback:
        pp_hooks.append(postprocess_callback)

    options = {
        **_build_base_opts(noplaylist=noplaylist),
        "outtmpl": outtmpl,
        "windowsfilenames": True,
        "progress_hooks": hooks,
        "postprocessor_hooks": pp_hooks,
    }

    if audio_only:
        options["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        # Exact format strings from youtube.py — preserved
        if quality and quality not in ("best", "audio"):
            try:
                h = int(quality)
                options["format"] = (
                    f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={h}]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={h}]+bestaudio/"
                    f"best[height<={h}][ext=mp4]/best[height<={h}]/best"
                )
            except ValueError:
                options["format"] = (
                    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                    "bestvideo+bestaudio[ext=m4a]/"
                    "bestvideo+bestaudio/"
                    "best[ext=mp4]/best"
                )
        else:
            options["format"] = (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio/"
                "best[ext=mp4]/best"
            )
        options["merge_output_format"] = "mp4"

    return options


# ---------------------------------------------------------------------------
# Actual download executor (called from background thread in jobs.py)
# ---------------------------------------------------------------------------

def execute_download(
    url: str,
    quality: str,
    save_dir: str,
    audio_only: bool,
    noplaylist: bool,
    on_progress: Callable[[dict], None],
    on_stage: Callable[[str], None],
) -> dict:
    """
    Execute the download. Designed to run in a background thread.

    `on_progress(d)` is called with yt-dlp progress hook dicts.
    `on_stage(stage_name)` is called at each workflow stage.

    Returns dict with result info on success.
    Raises RuntimeError on failure.
    """
    downloaded_files: list[str] = []
    is_audio = audio_only or quality == "audio"

    def progress_hook(d: dict) -> None:
        on_progress(d)
        if d.get("status") == "finished":
            fname = d.get("filename")
            if fname and fname not in downloaded_files:
                downloaded_files.append(fname)

    def pp_hook(d: dict) -> None:
        if d.get("status") == "started":
            pp_name = d.get("postprocessor", "")
            if "Merge" in pp_name or "FFmpegMerger" in pp_name:
                on_stage("merging")
            elif "ExtractAudio" in pp_name:
                on_stage("extracting_audio")

    on_stage("downloading")

    options = build_download_options(
        url=url,
        quality=quality,
        save_dir=save_dir,
        audio_only=is_audio,
        noplaylist=noplaylist,
        progress_callback=progress_hook,
        postprocess_callback=pp_hook,
    )

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        logger.error("yt-dlp DownloadError: %s", msg)
        if "private" in msg.lower():
            raise RuntimeError("This video is private and cannot be downloaded.")
        if "unavailable" in msg.lower():
            raise RuntimeError("This video is unavailable.")
        if "ffmpeg" in msg.lower():
            raise RuntimeError("FFmpeg is required but was not found. Please install FFmpeg.")
        raise RuntimeError("Download failed. Please check the URL and try again.")
    except Exception as e:
        logger.error("Download exception: %s", e, exc_info=True)
        raise RuntimeError("Download failed. Please try again.")

    on_stage("cleaning")
    cleanup_intermediate_files(save_dir)

    # Determine final filename
    final_file = _resolve_final_filename(downloaded_files, save_dir, is_audio)

    on_stage("completed")

    return {
        "file": final_file,
        "filename": os.path.basename(final_file) if final_file else None,
        "directory": save_dir,
    }


def _resolve_final_filename(downloaded_files: list[str], save_dir: str, is_audio: bool) -> str:
    """Clean up intermediate filename suffixes to find the real final file."""
    for f in downloaded_files:
        base = os.path.basename(f)
        # Strip intermediate suffixes
        clean = base.replace(".temp.mp4", ".mp4")
        clean = re.sub(r"\.f\d+", "", clean)
        if is_audio:
            clean = re.sub(r"\.(m4a|webm|opus|part)$", ".mp3", clean)
        else:
            clean = re.sub(r"\.part$", "", clean)
        candidate = os.path.join(save_dir, clean)
        if os.path.exists(candidate):
            return candidate
        if os.path.exists(f):
            return f
    return ""
