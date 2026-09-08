"""
validator.py — URL and filesystem path security validation.

Protects against:
- SSRF (only whitelisted YouTube domains allowed)
- Path traversal (../ and absolute paths outside allowed root)
- Protocol injection (javascript:, file:, etc.)
- Malformed or excessively long URLs
"""

import os
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# ---------------------------------------------------------------------------
# Whitelisted YouTube domains (SSRF guard)
# ---------------------------------------------------------------------------
ALLOWED_YOUTUBE_HOSTS = {
    "www.youtube.com",
    "youtube.com",
    "m.youtube.com",
    "youtu.be",
    "music.youtube.com",
}

MAX_URL_LENGTH = 2048


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

    # Block obviously dangerous protocols before parsing
    lower = url.lower()
    for bad_proto in ("javascript:", "file:", "data:", "vbscript:", "ftp://"):
        if lower.startswith(bad_proto):
            return False, "Unsupported or unsafe URL protocol."

    # Ensure it starts with http(s)
    if not (lower.startswith("http://") or lower.startswith("https://")):
        # Try prepending https://
        url = "https://" + url
        lower = url.lower()

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL."

    if parsed.scheme not in ("http", "https"):
        return False, "Only HTTP/HTTPS URLs are supported."

    host = parsed.netloc.lower().split(":")[0]  # strip port if present
    if host not in ALLOWED_YOUTUBE_HOSTS:
        return False, f"Only YouTube URLs are supported. Got: {host!r}"

    # Must have a meaningful path or query
    path = parsed.path or "/"
    query = parsed.query or ""

    is_watch = path == "/watch" and "v=" in query
    is_short = host == "youtu.be" and len(path) > 1
    is_playlist = "list=" in query
    is_shorts = "/shorts/" in path
    is_embed = "/embed/" in path

    if not (is_watch or is_short or is_playlist or is_shorts or is_embed):
        return False, "URL does not point to a supported YouTube video or playlist."

    # Force HTTPS
    safe_url = urlunparse(("https", parsed.netloc, parsed.path, parsed.params, parsed.query, ""))
    return True, safe_url


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

# Characters that must never appear in a filesystem path argument
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

    # Block control characters / null bytes
    if _DANGEROUS_PATH_CHARS.search(path):
        return False, "Path contains invalid characters."

    # Block UNC paths (\\\\server\\share or //server/share)
    if path.startswith("\\\\") or path.startswith("//"):
        return False, "UNC network paths are not allowed."

    # Expand ~ to home directory
    path = os.path.expanduser(path)

    # Resolve to absolute path, collapsing any .. traversal
    try:
        resolved = os.path.realpath(os.path.abspath(path))
    except Exception:
        return False, "Could not resolve path."

    # Must not be a root drive by itself (e.g. C:\\ or D:\\)
    drive, tail = os.path.splitdrive(resolved)
    if not tail or tail in (os.sep, "/"):
        return False, "Cannot download directly to a drive root."

    # Attempt to create the directory if it doesn't exist
    try:
        os.makedirs(resolved, exist_ok=True)
    except PermissionError:
        return False, "Permission denied: cannot create this directory."
    except Exception as e:
        return False, f"Cannot create directory: {e}"

    # Verify it is actually a directory
    if not os.path.isdir(resolved):
        return False, "Path is not a directory."

    # Write-access test
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
    # Remove null bytes and control characters
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    # Remove Windows-illegal chars
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    # Trim trailing dots/spaces (Windows quirk)
    name = name.rstrip(". ")
    return name or "download"
