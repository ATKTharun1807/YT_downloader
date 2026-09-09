"""
validator.py — URL and filesystem path security validation.

Protects against:
- SSRF (only whitelisted YouTube and Instagram domains allowed)
- Path traversal (../ and absolute paths outside allowed root)
- Protocol injection (javascript:, file:, etc.)
- Malformed or excessively long URLs
- Instagram URL cleaning (stripping tracking parameters like igsh, utm_*)
"""

import os
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# ---------------------------------------------------------------------------
# Whitelisted domains (SSRF guard)
# ---------------------------------------------------------------------------
ALLOWED_YOUTUBE_HOSTS = {
    "www.youtube.com",
    "youtube.com",
    "m.youtube.com",
    "youtu.be",
    "music.youtube.com",
}

ALLOWED_INSTAGRAM_HOSTS = {
    "www.instagram.com",
    "instagram.com",
    "instagr.am",
    "m.instagram.com",
}

MAX_URL_LENGTH = 2048

# Regex for Instagram paths: /p/ID, /reel/ID, /reels/ID, /tv/ID, /share/ID
_INSTAGRAM_PATH_RE = re.compile(r"^/(p|reel|reels|tv|share)(?:/([a-zA-Z0-9_-]+))?/?$", re.IGNORECASE)


def is_youtube_url(url: str) -> bool:
    """Check if the given URL belongs to a YouTube host."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc.lower().split(":")[0]
        return host in ALLOWED_YOUTUBE_HOSTS
    except Exception:
        return False


def is_instagram_url(url: str) -> bool:
    """Check if the given URL belongs to an Instagram host."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc.lower().split(":")[0]
        return host in ALLOWED_INSTAGRAM_HOSTS
    except Exception:
        return False


def get_media_platform(url: str) -> str:
    """Return 'youtube', 'instagram', or 'unsupported'."""
    if is_youtube_url(url):
        return "youtube"
    if is_instagram_url(url):
        return "instagram"
    return "unsupported"


def validate_youtube_url(url: str) -> tuple[bool, str]:
    """
    Validate that `url` is a safe, supported YouTube URL.

    Returns:
        (True, cleaned_url)  on success
        (False, error_message) on failure
    """
    if not url or not isinstance(url, str):
        return False, "No URL provided."

    url = url.strip()

    if len(url) > MAX_URL_LENGTH:
        return False, "URL is too long."

    lower = url.lower()
    for bad_proto in ("javascript:", "file:", "data:", "vbscript:", "ftp://"):
        if lower.startswith(bad_proto):
            return False, "Unsupported or unsafe URL protocol."

    if not (lower.startswith("http://") or lower.startswith("https://")):
        url = "https://" + url
        lower = url.lower()

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL."

    if parsed.scheme not in ("http", "https"):
        return False, "Only HTTP/HTTPS URLs are supported."

    host = parsed.netloc.lower().split(":")[0]
    if host not in ALLOWED_YOUTUBE_HOSTS:
        return False, f"Only YouTube URLs are supported. Got: {host!r}"

    path = parsed.path or "/"
    query = parsed.query or ""

    is_watch = path == "/watch" and "v=" in query
    is_short = host == "youtu.be" and len(path) > 1
    is_playlist = "list=" in query
    is_shorts = "/shorts/" in path
    is_embed = "/embed/" in path

    if not (is_watch or is_short or is_playlist or is_shorts or is_embed):
        return False, "URL does not point to a supported YouTube video or playlist."

    safe_url = urlunparse(("https", parsed.netloc, parsed.path, parsed.params, parsed.query, ""))
    return True, safe_url


def validate_instagram_url(url: str) -> tuple[bool, str, dict]:
    """
    Validate that `url` is a safe, supported Instagram URL (Reel, Post, TV, Share).

    Returns:
        (True, cleaned_url, meta_dict) on success
        (False, error_message, {}) on failure
    """
    if not url or not isinstance(url, str):
        return False, "No URL provided.", {}

    url = url.strip()

    if len(url) > MAX_URL_LENGTH:
        return False, "URL is too long.", {}

    lower = url.lower()
    for bad_proto in ("javascript:", "file:", "data:", "vbscript:", "ftp://"):
        if lower.startswith(bad_proto):
            return False, "Unsupported or unsafe URL protocol.", {}

    if not (lower.startswith("http://") or lower.startswith("https://")):
        url = "https://" + url
        lower = url.lower()

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL.", {}

    if parsed.scheme not in ("http", "https"):
        return False, "Only HTTP/HTTPS URLs are supported.", {}

    host = parsed.netloc.lower().split(":")[0]
    if host not in ALLOWED_INSTAGRAM_HOSTS:
        return False, f"Only Instagram URLs are supported. Got: {host!r}", {}

    path = parsed.path.rstrip("/")
    if not path:
        return False, "Please provide a link to a specific Instagram Reel, Post, or Video.", {}

    match = _INSTAGRAM_PATH_RE.match(path)
    if not match:
        return False, "URL does not point to a supported Instagram Reel or Post (e.g. /reel/..., /p/...).", {}

    media_type_str = match.group(1).lower()
    shortcode = match.group(2) or ""

    media_type = "reel" if media_type_str in ("reel", "reels") else "post"

    # Standardize hostname to www.instagram.com and strip tracking query parameters
    cleaned_path = f"/{media_type_str}/{shortcode}" if shortcode else f"/{media_type_str}/"
    clean_url = f"https://www.instagram.com{cleaned_path}"

    meta = {
        "platform": "instagram",
        "media_type": media_type,
        "shortcode": shortcode,
    }

    return True, clean_url, meta


def validate_media_url(url: str) -> tuple[bool, str, dict]:
    """
    Unified validator for both YouTube and Instagram URLs.

    Returns:
        (True, cleaned_url, info_dict) on success
        (False, error_message, {}) on failure
    """
    if not url or not isinstance(url, str):
        return False, "No URL provided.", {}

    raw = url.strip()
    platform = get_media_platform(raw)

    if platform == "youtube":
        valid, result = validate_youtube_url(raw)
        if not valid:
            return False, result, {}
        return True, result, {
            "platform": "youtube",
            "is_playlist": is_playlist_url(result),
            "is_mixed": is_mixed_url(result),
        }
    elif platform == "instagram":
        return validate_instagram_url(raw)
    else:
        return False, "Unsupported URL. Please enter a valid YouTube or Instagram URL.", {}


def is_playlist_url(url: str) -> bool:
    """Return True if the URL contains a playlist parameter."""
    try:
        parsed = urlparse(url)
        return "list=" in parsed.query
    except Exception:
        return False


def is_mixed_url(url: str) -> bool:
    """Return True if URL has both a video ID and a playlist ID (watch + list)."""
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        return "v" in qs and "list" in qs
    except Exception:
        return False


def strip_playlist_from_url(url: str) -> str:
    """Remove list=, index=, start_radio= params; return clean single-video URL."""
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for key in ("list", "index", "start_radio"):
            qs.pop(key, None)
        new_query = urlencode(qs, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, ""))
    except Exception:
        return url


# ---------------------------------------------------------------------------
# Filesystem path validation
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DOWNLOADS_DIR = os.path.join(_PROJECT_ROOT, "downloads")

_DANGEROUS_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f]")  # null bytes + control chars


def get_safe_download_root() -> str:
    """Return the primary safe download root (project/downloads)."""
    os.makedirs(_DEFAULT_DOWNLOADS_DIR, exist_ok=True)
    return _DEFAULT_DOWNLOADS_DIR


def validate_download_path(requested_path: str) -> tuple[bool, str]:
    """
    Validate that `requested_path` is a safe, accessible local directory.

    Allows ANY writable local directory the user has access to.
    Blocks:
      - UNC paths (\\\\server\\share)
      - Null bytes / control characters
      - Paths that cannot be resolved to an absolute path
      - Paths that are not (or cannot be made) writable

    Returns:
        (True, resolved_absolute_path)  on success
        (False, error_message) on failure
    """
    if not requested_path or not isinstance(requested_path, str):
        return False, "No download path provided."

    path = requested_path.strip().strip('"').strip("'")

    if not path:
        return False, "Empty path provided."

    if _DANGEROUS_PATH_CHARS.search(path):
        return False, "Path contains invalid characters."

    if path.startswith("\\\\") or path.startswith("//"):
        return False, "UNC network paths are not allowed."

    path = os.path.expanduser(path)

    try:
        resolved = os.path.realpath(os.path.abspath(path))
    except Exception:
        return False, "Could not resolve path."

    drive, tail = os.path.splitdrive(resolved)
    if not tail or tail in (os.sep, "/"):
        return False, "Cannot download directly to a drive root."

    try:
        os.makedirs(resolved, exist_ok=True)
    except PermissionError:
        return False, "Permission denied: cannot create this directory."
    except Exception as e:
        return False, f"Cannot create directory: {e}"

    if not os.path.isdir(resolved):
        return False, "Path is not a directory."

    try:
        test_file = os.path.join(resolved, f".yt_write_test_{os.getpid()}.tmp")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
    except Exception:
        return False, "This folder is not writable. Please choose another folder."

    return True, resolved


def validate_local_file_path(file_path: str) -> tuple[bool, str]:
    """
    Validate that `file_path` exists as a file and its directory passes
    validate_download_path(). Used for open-file / open-folder endpoints.
    """
    if not file_path or not isinstance(file_path, str):
        return False, "No file path provided."

    if _DANGEROUS_PATH_CHARS.search(file_path):
        return False, "Path contains invalid characters."

    try:
        resolved = os.path.realpath(os.path.abspath(file_path))
    except Exception:
        return False, "Invalid path."

    directory = os.path.dirname(resolved)
    return validate_download_path(directory)


def sanitize_filename_component(name: str) -> str:
    """Remove characters that are illegal in Windows filenames."""
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.rstrip(". ")
    return name or "download"
