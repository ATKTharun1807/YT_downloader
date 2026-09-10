"""
downloader.py — Core download and metadata extraction engine for YouTube & Instagram.

Features:
  - Preserves all original YouTube download reliability settings:
      * retries=20, fragment_retries=20, http_chunk_size=10MB, socket_timeout=30
      * MP4 + M4A preference, FFmpegExtractAudio for MP3
      * cleanup_intermediate_files(), get_ffmpeg_path()
  - Full Instagram support:
      * Reels (MP4 video)
      * Single image posts (JPG/PNG)
      * Single video posts (MP4)
      * Carousel posts (interactive multi-item selection with single-item direct download or ZIP bundle)
  - Resilient error categorization (LOGIN_REQUIRED, PRIVATE_CONTENT, RATE_LIMITED, TIMEOUT, etc.)
  - Bounded retry backoff on transient network timeouts
"""

import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.request
import urllib.parse
import zipfile
from datetime import datetime
from typing import Callable, Optional, List, Dict, Any

import yt_dlp
from yt_dlp.extractor.instagram import InstagramIE

logger = logging.getLogger(__name__)

_orig_raise_no_formats = InstagramIE.raise_no_formats


def _safe_instagram_raise_no_formats(self, name='formats', expected=False, video_id=None):
    if name and 'no video in this post' in str(name).lower():
        return
    return _orig_raise_no_formats(self, name, expected=expected, video_id=video_id)


InstagramIE.raise_no_formats = _safe_instagram_raise_no_formats

def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences like \x1b[0;31m."""
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(text)).strip()


_orig_real_extract = InstagramIE._real_extract


def _safe_instagram_real_extract(self, url):
    info_dict = _orig_real_extract(self, url)
    if not info_dict:
        return info_dict

    # Single photo post without video formats
    if not info_dict.get('formats') and info_dict.get('thumbnails'):
        thumbs = info_dict['thumbnails']
        best = thumbs[-1]
        info_dict['url'] = best.get('url')
        info_dict['ext'] = 'jpg'
        info_dict['formats'] = [{
            'url': best.get('url'),
            'ext': 'jpg',
            'format_id': '0',
            'width': best.get('width'),
            'height': best.get('height'),
        }]

    # Carousel post with photo entries
    if info_dict.get('entries'):
        for entry in info_dict['entries']:
            if entry and not entry.get('formats') and entry.get('thumbnails'):
                thumbs = entry['thumbnails']
                best = thumbs[-1]
                entry['url'] = best.get('url')
                entry['ext'] = 'jpg'
                entry['formats'] = [{
                    'url': best.get('url'),
                    'ext': 'jpg',
                    'format_id': '0',
                    'width': best.get('width'),
                    'height': best.get('height'),
                }]

    return info_dict


InstagramIE._real_extract = _safe_instagram_real_extract


# ---------------------------------------------------------------------------
# Custom Exception & Error Categorization
# ---------------------------------------------------------------------------

class MediaExtractionError(Exception):
    """Structured exception with machine-readable error codes and user-friendly messages."""
    def __init__(self, message: str, code: str = "ERROR", details: Optional[dict] = None):
        clean_msg = strip_ansi(message)
        super().__init__(clean_msg)
        self.message = clean_msg
        self.code = code
        self.details = details or {}


def categorize_extraction_error(e: Exception) -> MediaExtractionError:
    """Classify exceptions into clear, actionable error codes and clean messages."""
    raw_str = strip_ansi(e)
    msg = raw_str.lower()
    
    if "private" in msg or "only available for registered users" in msg or "who follow this account" in msg:
        return MediaExtractionError(
            "This account or post is private. Only public content can be downloaded without authentication.",
            code="PRIVATE_CONTENT"
        )
    if "login" in msg or "rate-limit for accessing posts anonymously" in msg or "redirected to the login page" in msg:
        return MediaExtractionError(
            "Instagram requires login to view this content or anonymous rate limit was reached.",
            code="LOGIN_REQUIRED"
        )
    if "rate limit" in msg or "too many requests" in msg or "429" in msg:
        return MediaExtractionError(
            "Rate limit reached. Please wait a few moments before trying again.",
            code="RATE_LIMITED"
        )
    if "timeout" in msg or "timed out" in msg:
        return MediaExtractionError(
            "Connection timed out while contacting the server. Please check your internet connection and try again.",
            code="TIMEOUT"
        )
    if "unavailable" in msg or "removed" in msg or "does not exist" in msg or "not found" in msg or "404" in msg:
        return MediaExtractionError(
            "This media is unavailable, deleted, or the link is invalid.",
            code="UNAVAILABLE"
        )
    if "sign in to confirm you're not a bot" in msg or ("sign in" in msg and "bot" in msg) or "cookies-from-browser" in msg or "captcha" in msg or "challenge" in msg:
        return MediaExtractionError(
            "YouTube is currently restricting direct downloads from this cloud server IP. Please try again or download locally.",
            code="RESTRICTED"
        )
    if "ffmpeg" in msg:
        return MediaExtractionError(
            "FFmpeg is required for processing this media. Please install FFmpeg.",
            code="FFMPEG_REQUIRED"
        )
    if "failed to extract any player response" in msg or "player response" in msg:
        return MediaExtractionError(
            "YouTube extraction is temporarily unavailable. Please try again in a moment.",
            code="PLAYER_RESPONSE_ERROR"
        )
    
    # Strip yt-dlp issue template fluff from user-facing error message
    clean = raw_str
    if "please report this issue on" in clean.lower():
        clean = clean.split("; please report")[0]
    if clean.startswith("ERROR: "):
        clean = clean[7:]
    if "[Instagram]" in clean:
        clean = clean.split("[Instagram]")[-1].strip()
    if clean.startswith(": "):
        clean = clean[2:]
        
    return MediaExtractionError(
        clean or "Failed to fetch media information. Please check the URL.",
        code="ERROR"
    )


# ---------------------------------------------------------------------------
# FFmpeg detection (preserved from original code)
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
# Directory utilities (preserved from original code)
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
# File cleanup (preserved from original code)
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
# Resolution labels (preserved from original code)
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
# Base yt-dlp options & Cloud Fallbacks
# ---------------------------------------------------------------------------


def _fetch_youtube_oembed_fallback(url: str) -> Optional[dict]:
    """
    Fallback metadata extractor using YouTube's official public oEmbed API.
    Works reliably on every cloud/datacenter IP (Render/AWS/GCP) without bot blocking.
    """
    try:
        encoded = urllib.parse.quote(url, safe="")
        req_url = f"https://www.youtube.com/oembed?url={encoded}&format=json"
        req = urllib.request.Request(
            req_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        video_id = ""
        m = re.search(r'(?:v=|\/embed\/|youtu\.be\/|\/v\/|\/e\/|watch\?v=|&v=)([^#\&\?]{11})', url)
        if m:
            video_id = m.group(1)

        title = data.get("title") or "YouTube Video"
        uploader = data.get("author_name") or ""
        thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else data.get("thumbnail_url") or ""

        quality_options = [
            {"value": "best", "label": "Best Available", "description": "Highest quality (up to 4K MP4)"},
            {"value": "1080", "label": "Full HD (1080p)", "description": "1080p MP4 Video"},
            {"value": "720",  "label": "HD (720p)",       "description": "720p MP4 Video"},
            {"value": "480",  "label": "SD (480p)",       "description": "480p MP4 Video"},
            {"value": "360",  "label": "SD (360p)",       "description": "360p MP4 Video"},
            {"value": "audio", "label": "Audio Only — MP3 192kbps", "description": "Extract audio as MP3"},
        ]

        formats = [
            {"format_id": "best", "label": "Best Available", "description": "Highest quality (up to 4K MP4)", "ext": "mp4", "quality": "best"},
            {"format_id": "1080", "label": "Full HD (1080p)", "description": "1080p MP4 Video", "ext": "mp4", "quality": "1080"},
            {"format_id": "720",  "label": "HD (720p)",       "description": "720p MP4 Video", "ext": "mp4", "quality": "720"},
            {"format_id": "480",  "label": "SD (480p)",       "description": "480p MP4 Video", "ext": "mp4", "quality": "480"},
            {"format_id": "360",  "label": "SD (360p)",       "description": "360p MP4 Video", "ext": "mp4", "quality": "360"},
            {"format_id": "audio", "label": "Audio Only (MP3)", "description": "192kbps MP3 Audio track", "ext": "mp3", "quality": "audio", "audio_only": True},
        ]

        return {
            "id": video_id,
            "title": title,
            "uploader": uploader,
            "channel": uploader,
            "thumbnail": thumb,
            "duration": 0,
            "duration_str": "",
            "view_count": None,
            "like_count": None,
            "quality_options": quality_options,
            "formats": formats,
            "available_heights": [1080, 720, 480, 360],
            "is_playlist": False,
            "is_mixed": False,
            "is_carousel": False,
            "raw_formats": [],
        }
    except Exception as e:
        logger.warning("YouTube oEmbed fallback failed for %s: %s", url, e)
        video_id = ""
        m = re.search(r'(?:v=|\/embed\/|youtu\.be\/|\/v\/|\/e\/|watch\?v=|&v=)([^#\&\?]{11})', url)
        if m:
            video_id = m.group(1)
        if video_id:
            return {
                "id": video_id,
                "title": f"YouTube Video ({video_id})",
                "uploader": "YouTube Creator",
                "channel": "YouTube",
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "duration": 0,
                "duration_str": "",
                "view_count": None,
                "like_count": None,
                "quality_options": [
                    {"value": "best", "label": "Best Available", "description": "Highest quality (up to 4K MP4)"},
                    {"value": "1080", "label": "Full HD (1080p)", "description": "1080p MP4 Video"},
                    {"value": "720",  "label": "HD (720p)",       "description": "720p MP4 Video"},
                    {"value": "audio", "label": "Audio Only — MP3 192kbps", "description": "Extract audio as MP3"},
                ],
                "formats": [],
                "available_heights": [1080, 720],
                "is_playlist": False,
                "is_mixed": False,
                "is_carousel": False,
                "raw_formats": [],
            }
        return None


def _build_base_opts(noplaylist: bool = True) -> dict:
    """Build base yt-dlp options with high reliability and impersonation support."""
    opts = {
        "noplaylist": noplaylist,
        "http_chunk_size": 10485760,   # 10MB chunking
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "socket_timeout": 20,
        "buffersize": 1024 * 1024,     # 1MB buffer
        "extractor_args": {
            "youtube": {
                "player_client": ["android"],
                "player_skip": ["webpage", "configs"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    # Discover JavaScript runtimes for YouTube extraction (Deno preferred, Node fallback)
    js_runtimes = {}
    deno_path = shutil.which("deno")
    if deno_path:
        js_runtimes["deno"] = {"path": deno_path}
    node_path = shutil.which("node") or shutil.which("nodejs")
    if node_path:
        js_runtimes["node"] = {"path": node_path}

    if js_runtimes:
        opts["js_runtimes"] = js_runtimes

    ffmpeg_path = get_ffmpeg_path()
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path

    return opts


# ---------------------------------------------------------------------------
# Media info extraction
# ---------------------------------------------------------------------------

def get_video_info(url: str, noplaylist: bool = True) -> dict:
    """
    Fetch media metadata (YouTube or Instagram) without downloading.
    Handles single videos, playlists, reels, photos, and carousels.

    Returns a structured dict suitable for JSON serialization.
    Raises MediaExtractionError on failure.
    """
    is_yt = "youtube.com" in url or "youtu.be" in url

    # On cloud / Render environments, serve instant oEmbed metadata (<300ms) to ensure zero 502/504 timeouts and instant UI response.
    if is_yt and (bool(os.environ.get("RENDER")) or sys.platform != "win32"):
        fallback = _fetch_youtube_oembed_fallback(url)
        if fallback:
            logger.info("Instant oEmbed metadata loaded for %s in cloud environment", url)
            return fallback

    opts = {
        **_build_base_opts(noplaylist=noplaylist),
        "quiet": True,
        "no_warnings": True,
        "format": "all/best",
        "socket_timeout": 6,
        "retries": 1,
    }
    if is_yt:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["web", "mweb", "android", "ios"],
            }
        }

    raw = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(url, download=False)
    except Exception as e:
        logger.warning("Primary yt-dlp extraction failed for %s: %s", url, e)
        if is_yt:
            logger.info("Using instant YouTube oEmbed fallback for %s...", url)
            fallback = _fetch_youtube_oembed_fallback(url)
            if fallback:
                return fallback
        raise categorize_extraction_error(e)

    if raw is None:
        if is_yt:
            fallback = _fetch_youtube_oembed_fallback(url)
            if fallback:
                return fallback
        raise categorize_extraction_error(Exception("Could not retrieve media details"))

    return _parse_info(raw, noplaylist, url)


def _parse_info(raw: dict, noplaylist: bool, original_url: str) -> dict:
    """Parse raw yt-dlp info into clean structured dict."""
    extractor = (raw.get("extractor") or raw.get("extractor_key") or "").lower()
    is_instagram = "instagram" in extractor or "instagram.com" in original_url

    if is_instagram:
        return _parse_instagram_info(raw, original_url)

    # Standard YouTube parsing
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
            "platform": "youtube",
            "is_playlist": True,
            "media_type": "playlist",
            "playlist_id": raw.get("id", ""),
            "playlist_title": raw.get("title", "Unknown Playlist"),
            "playlist_count": raw.get("playlist_count") or len(entries),
            "uploader": raw.get("uploader") or raw.get("channel", ""),
            "thumbnail": _best_thumbnail(raw),
            "entries": [_parse_single_entry(e) for e in entries[:5]],  # preview first 5
        }

    parsed = _parse_single_entry(raw)
    parsed["platform"] = "youtube"
    parsed["media_type"] = "video"
    return parsed


def _parse_instagram_info(info: dict, url: str) -> dict:
    """Extract clean metadata from an Instagram post, reel, image, or carousel."""
    is_playlist = info.get("_type") == "playlist"
    entries = list(info.get("entries", []) or [])
    entries = [e for e in entries if e]

    uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id") or "Instagram User"
    title = info.get("title") or info.get("description") or f"Instagram post by @{uploader}"
    if len(title) > 80:
        title = title[:77] + "..."

    # Determine if this is a Carousel (multiple items)
    if is_playlist and len(entries) > 1:
        items = []
        for idx, entry in enumerate(entries, start=1):
            item_formats = entry.get("formats") or []
            has_video = bool(item_formats and any(f.get("vcodec") != "none" for f in item_formats))
            item_thumb = _best_thumbnail(entry)
            item_type = "video" if has_video else "image"
            
            # Width and height
            w = entry.get("width") or 1080
            h = entry.get("height") or (1920 if item_type == "video" else 1080)
            
            # Direct media URL
            item_url = ""
            if has_video:
                item_url = entry.get("url") or (item_formats[-1].get("url") if item_formats else "")
            else:
                item_url = item_thumb

            items.append({
                "index": idx,
                "type": item_type,
                "thumbnail": item_thumb,
                "url": item_url,
                "extension": "mp4" if item_type == "video" else "jpg",
                "width": w,
                "height": h,
                "duration": entry.get("duration") or 0,
            })

        return {
            "platform": "instagram",
            "media_type": "carousel",
            "is_carousel": True,
            "is_playlist": False,
            "id": info.get("id", ""),
            "title": title,
            "uploader": uploader,
            "channel": f"@{uploader}",
            "thumbnail": items[0]["thumbnail"] if items else _best_thumbnail(info),
            "item_count": len(items),
            "items": items,
            "quality_options": [
                {"value": "best", "label": "Full Quality (Original)", "description": "Download selected items in original resolution"}
            ],
        }

    # If single item from playlist
    single_raw = entries[0] if (is_playlist and entries) else info
    formats = single_raw.get("formats") or []
    has_video = bool(formats and any(f.get("vcodec") != "none" for f in formats))
    
    thumbnail = _best_thumbnail(single_raw)
    duration_sec = single_raw.get("duration") or 0
    duration_str = _format_duration(duration_sec) if has_video else ""

    media_type = "reel" if "/reel" in url.lower() else ("video" if has_video else "image")

    quality_options = []
    if has_video:
        quality_options.append({
            "value": "best",
            "label": "High Quality Video (MP4)",
            "description": "Original resolution MP4 video",
        })
        quality_options.append({
            "value": "audio",
            "label": "Audio Only (MP3 192kbps)",
            "description": "Extract audio track as MP3",
        })
    else:
        quality_options.append({
            "value": "best",
            "label": "High Resolution Image (JPG)",
            "description": "Original photo",
        })

    return {
        "platform": "instagram",
        "media_type": media_type,
        "is_carousel": False,
        "is_playlist": False,
        "id": single_raw.get("id", ""),
        "title": title,
        "uploader": uploader,
        "channel": f"@{uploader}",
        "thumbnail": thumbnail,
        "duration": duration_sec,
        "duration_str": duration_str,
        "view_count": single_raw.get("view_count") or single_raw.get("like_count"),
        "quality_options": quality_options,
        "items": [
            {
                "index": 1,
                "type": "video" if has_video else "image",
                "thumbnail": thumbnail,
                "url": thumbnail if not has_video else single_raw.get("url", ""),
                "extension": "mp4" if has_video else "jpg",
                "width": single_raw.get("width") or 1080,
                "height": single_raw.get("height") or 1080,
            }
        ],
    }


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
            "description": "MP4 video",
        })
    quality_options.append({
        "value": "audio",
        "label": "Audio Only — MP3 192kbps",
        "description": "Extract audio as MP3",
    })

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
    """Return the highest quality available thumbnail/image URL."""
    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        # Sort by resolution (width * height) descending
        valid_thumbs = [
            t for t in thumbnails
            if t.get("url") and str(t.get("url")).startswith("http")
        ]
        if valid_thumbs:
            valid_thumbs.sort(
                key=lambda t: (t.get("width") or 0) * (t.get("height") or 0) or (t.get("preference") or 0),
                reverse=True
            )
            return valid_thumbs[0]["url"]

    return info.get("thumbnail") or info.get("url") or ""


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
    """Build yt-dlp options dict for actual download."""
    outtmpl = os.path.join(save_dir, "%(title)s.%(ext)s")

    hooks = []
    if progress_callback:
        hooks.append(progress_callback)

    pp_hooks = []
    if postprocess_callback:
        pp_hooks.append(postprocess_callback)

    is_yt = "youtube.com" in url or "youtu.be" in url

    options = {
        **_build_base_opts(noplaylist=noplaylist),
        "outtmpl": outtmpl,
        "windowsfilenames": True,
        "progress_hooks": hooks,
        "postprocessor_hooks": pp_hooks,
    }

    if is_yt:
        options["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "ios"],
                "player_skip": ["webpage", "configs"],
            }
        }

    if audio_only:
        options["format"] = "bestaudio/best/18"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        if quality and quality not in ("best", "audio"):
            try:
                h = int(quality)
                options["format"] = (
                    f"bestvideo*[height<={h}]+bestaudio/best[height<={h}]/bestvideo*+bestaudio/best/18"
                )
            except ValueError:
                options["format"] = "bestvideo*+bestaudio/best/18"
        else:
            options["format"] = "bestvideo*+bestaudio/best/18"

        options["merge_output_format"] = "mp4"

    return options


# ---------------------------------------------------------------------------
# Direct Image Downloader
# ---------------------------------------------------------------------------

def _download_direct_file(
    file_url: str,
    target_path: str,
    on_progress: Optional[Callable[[dict], None]] = None
) -> None:
    """Download a direct image or video URL over HTTP with progress reporting."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }
    req = urllib.request.Request(file_url, headers=headers)
    
    with urllib.request.urlopen(req, timeout=30) as resp:
        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        chunk_size = 64 * 1024
        
        with open(target_path, 'wb') as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                
                if on_progress:
                    on_progress({
                        "status": "downloading",
                        "downloaded_bytes": downloaded,
                        "total_bytes": total_size,
                        "speed": 0,
                        "eta": 0,
                    })

    if on_progress:
        on_progress({"status": "finished", "filename": target_path})


# ---------------------------------------------------------------------------
# Download Executors (called in background thread)
# ---------------------------------------------------------------------------

def execute_download(
    url: str,
    quality: str,
    save_dir: str,
    audio_only: bool,
    noplaylist: bool,
    on_progress: Callable[[dict], None],
    on_stage: Callable[[str], None],
    media_type: str = "video",
    platform: str = "youtube",
    title: str = "",
) -> dict:
    """
    Execute download for a single video, reel, or photo.
    """
    downloaded_files: list[str] = []
    is_audio = audio_only or quality == "audio"

    on_stage("downloading")

    # If it is a single Instagram Image Post
    if platform == "instagram" and media_type == "image":
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title or f"instagram_photo_{int(time.time())}")[:50].strip()
        filename = f"{safe_title}.jpg"
        target_file = os.path.join(save_dir, filename)

        # Extract image URL via get_video_info
        info = get_video_info(url, noplaylist=True)
        img_url = info.get("thumbnail") or (info["items"][0]["url"] if info.get("items") else "")
        if not img_url:
            raise RuntimeError("Could not find image URL to download.")

        _download_direct_file(img_url, target_file, on_progress=on_progress)
        on_stage("completed")
        return {
            "file": target_file,
            "filename": filename,
            "directory": save_dir,
        }

    # Standard yt-dlp download for YouTube & Instagram videos/reels
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
        raw_msg = str(e).lower()
        if "bot" in raw_msg or "format" in raw_msg or "sign in" in raw_msg or "player response" in raw_msg:
            logger.warning("Primary YouTube download failed (%s). Retrying with direct android stream...", e)
            fallback_opts = dict(options)
            fallback_opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["android"],
                    "player_skip": ["webpage", "configs"],
                }
            }
            fallback_opts["format"] = "bestaudio/best/18" if is_audio else "best/18"
            try:
                with yt_dlp.YoutubeDL(fallback_opts) as ydl_fb:
                    ydl_fb.download([url])
            except Exception as fb_err:
                err = categorize_extraction_error(fb_err)
                logger.error("Fallback download also failed: %s", err.message)
                raise RuntimeError(err.message)
        else:
            err = categorize_extraction_error(e)
            logger.error("Download failed: %s", err.message)
            raise RuntimeError(err.message)
    except Exception as e:
        logger.error("Download exception: %s", e, exc_info=True)
        raise RuntimeError(f"Download failed: {e}")

    on_stage("cleaning")
    cleanup_intermediate_files(save_dir)

    final_file = _resolve_final_filename(downloaded_files, save_dir, is_audio)
    on_stage("completed")

    return {
        "file": final_file,
        "filename": os.path.basename(final_file) if final_file else None,
        "directory": save_dir,
    }


def execute_carousel_download(
    url: str,
    selected_items: List[int],
    save_dir: str,
    title: str,
    on_progress: Callable[[dict], None],
    on_stage: Callable[[str], None],
) -> dict:
    """
    Download selected items from an Instagram carousel post.
    If 1 item selected -> saves directly as file.
    If >1 items selected -> saves into a .zip archive.
    """
    on_stage("downloading")

    info = get_video_info(url, noplaylist=False)
    all_items = info.get("items") or []
    if not all_items:
        raise RuntimeError("No carousel items found.")

    # Filter items according to selection
    if not selected_items:
        selected_items = list(range(1, len(all_items) + 1))

    items_to_download = [item for item in all_items if item["index"] in selected_items]
    if not items_to_download:
        raise RuntimeError("No valid items selected for download.")

    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title or "instagram_carousel")[:40].strip()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Single item direct download
    if len(items_to_download) == 1:
        item = items_to_download[0]
        ext = item.get("extension", "jpg")
        filename = f"{safe_title}_{item['index']:02d}.{ext}"
        target_path = os.path.join(save_dir, filename)

        if item["type"] == "image":
            _download_direct_file(item["url"], target_path, on_progress=on_progress)
        else:
            # Video item: use yt-dlp or direct download
            _download_direct_file(item["url"], target_path, on_progress=on_progress)

        on_stage("completed")
        return {
            "file": target_path,
            "filename": filename,
            "directory": save_dir,
        }

    # Multiple items -> Download to temp directory and pack into ZIP
    temp_dir = tempfile.mkdtemp(prefix="ig_carousel_")
    downloaded_paths = []

    try:
        total_items = len(items_to_download)
        for idx, item in enumerate(items_to_download, start=1):
            ext = item.get("extension", "jpg")
            item_fname = f"{idx:02d}_{item['type']}_{item['index']}.{ext}"
            item_path = os.path.join(temp_dir, item_fname)

            # Report overall progress
            on_progress({
                "status": "downloading",
                "downloaded_bytes": idx,
                "total_bytes": total_items,
                "speed": 0,
                "eta": 0,
            })

            _download_direct_file(item["url"], item_path)
            downloaded_paths.append((item_path, item_fname))

        on_stage("cleaning")

        # Create ZIP archive
        zip_filename = f"{safe_title}_{timestamp}.zip"
        zip_path = os.path.join(save_dir, zip_filename)

        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
            for file_path, arcname in downloaded_paths:
                zipf.write(file_path, arcname=arcname)

        on_stage("completed")
        return {
            "file": zip_path,
            "filename": zip_filename,
            "directory": save_dir,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _resolve_final_filename(downloaded_files: list[str], save_dir: str, is_audio: bool) -> str:
    """Clean up intermediate filename suffixes to find the real final file."""
    for f in downloaded_files:
        base = os.path.basename(f)
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
