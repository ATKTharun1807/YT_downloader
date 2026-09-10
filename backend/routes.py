"""
routes.py — FastAPI APIRouter with all API endpoints for YouTube & Instagram downloading.

Endpoints:
  POST   /api/analyze                 - Fetch media info (YouTube / Instagram)
  POST   /api/download                - Start download job
  GET    /api/progress/{job_id}       - SSE progress stream
  GET    /api/job/{job_id}            - Job status (polling fallback)
  GET    /api/downloads               - Download history
  DELETE /api/downloads/{entry_id}    - Delete history entry
  GET    /api/settings                - Read settings
  POST   /api/settings                - Save settings
  GET    /api/select-folder           - Open native Windows folder picker
  POST   /api/open-file/{entry_id}    - Open file in explorer
  POST   /api/open-folder/{entry_id}  - Open folder in explorer
  GET    /api/ffmpeg-status           - Check FFmpeg availability
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import traceback
from typing import AsyncGenerator

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

from backend.downloader import (
    get_ffmpeg_path,
    get_video_info,
    get_default_download_dir,
    MediaExtractionError,
    _fetch_youtube_oembed_fallback,
    _get_cookie_file,
)
from backend.jobs import job_manager, load_history, delete_history_entry
from backend.schemas import AnalyzeRequest, DownloadRequest, SettingsUpdateRequest
from backend.security import analyze_limiter, download_limiter
from backend.validator import (
    validate_media_url,
    validate_download_path,
    validate_local_file_path,
    is_mixed_url,
    strip_playlist_from_url,
    get_safe_download_root,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SETTINGS_FILE = os.path.join(_PROJECT_ROOT, "settings.json")

_DEFAULT_SETTINGS = {
    "download_dir": os.path.join(_PROJECT_ROOT, "downloads"),
    "default_quality": "best",
    "max_concurrent": 2,
    "theme": "light",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    merged = {**_DEFAULT_SETTINGS, **data}
                    return merged
        except Exception:
            pass
    return dict(_DEFAULT_SETTINGS)


def _save_settings(settings: dict) -> None:
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _safe_error(user_msg: str, log_msg: str = "", status_code: int = 400, error_code: str = "ERROR") -> JSONResponse:
    if log_msg:
        logger.warning(log_msg)
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": user_msg, "error_code": error_code}
    )


# ---------------------------------------------------------------------------
# Diagnostic & Health Check Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def api_health():
    return {"status": "ok"}


@router.get("/system-status")
async def api_system_status():
    """Diagnostic system status without exposing any secrets, env vars, or keys."""
    try:
        import yt_dlp
        yt_ver = getattr(yt_dlp.version, "__version__", "unknown")
    except Exception as e:
        yt_ver = f"error: {e}"

    try:
        import yt_dlp_ejs
        ejs_ver = getattr(yt_dlp_ejs, "__version__", getattr(yt_dlp_ejs, "version", "installed"))
    except Exception as e:
        ejs_ver = f"not installed ({e})"

    deno_path = shutil.which("deno")
    deno_ver = "not found"
    if deno_path:
        try:
            deno_ver = subprocess.check_output([deno_path, "--version"], text=True).splitlines()[0]
        except Exception as e:
            deno_ver = f"found ({e})"

    ffmpeg_path = get_ffmpeg_path()
    ffmpeg_ver = "not found"
    if ffmpeg_path:
        try:
            ffmpeg_ver = subprocess.check_output([ffmpeg_path, "-version"], text=True).splitlines()[0]
        except Exception as e:
            ffmpeg_ver = f"found ({e})"

    return {
        "status": "ok",
        "yt_dlp": yt_ver != "unknown" and not yt_ver.startswith("error"),
        "yt_dlp_ejs": "not installed" not in ejs_ver,
        "deno": bool(deno_path),
        "ffmpeg": bool(ffmpeg_path),
        "versions": {
            "yt_dlp": yt_ver,
            "yt_dlp_ejs": ejs_ver,
            "deno": deno_ver,
            "ffmpeg": ffmpeg_ver,
        }
    }


@router.get("/ffmpeg-status")
async def ffmpeg_status():
    path = get_ffmpeg_path()
    return {
        "available": path is not None,
        "path": path or "",
    }


# ---------------------------------------------------------------------------
# /api/select-folder  — Native Windows folder picker via tkinter subprocess
# ---------------------------------------------------------------------------

def _run_native_folder_picker(initial_dir: str) -> dict:
    """Helper executed in worker thread to prevent blocking async event loop."""
    picker_script = r"""
import sys, json
try:
    import tkinter as tk
    from tkinter import filedialog
    initial = sys.argv[1] if len(sys.argv) > 1 else "/"
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.focus_force()
    chosen = filedialog.askdirectory(
        parent=root,
        initialdir=initial,
        title="Select Download Folder \u2014 YT_DOWNLOADER"
    )
    root.destroy()
    print(json.dumps({"path": chosen or ""}))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", picker_script, initial_dir],
            capture_output=True,
            text=True,
            timeout=120,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Folder picker timed out.")
        return {"cancelled": True, "message": "Folder selection timed out."}
    except Exception as exc:
        logger.error("Folder picker subprocess error: %s", exc)
        return {"error": "Could not launch folder picker."}

    raw = (proc.stdout or "").strip()
    if not raw:
        return {"cancelled": True, "message": "Folder selection cancelled."}

    try:
        payload = json.loads(raw)
        return payload
    except json.JSONDecodeError:
        logger.error("Folder picker bad output: %r", raw)
        return {"error": "Folder picker returned unexpected output."}


@router.get("/select-folder")
async def select_folder():
    """Select download directory. In cloud/Linux environments, returns server storage directory."""
    settings = _load_settings()
    initial_dir = settings.get("download_dir") or get_safe_download_root()
    if not os.path.isdir(initial_dir):
        initial_dir = get_safe_download_root()

    # Cloud / Docker / Linux headless environment: immediately return valid directory without opening desktop dialog
    if sys.platform != "win32" or bool(os.environ.get("RENDER")):
        os.makedirs(initial_dir, exist_ok=True)
        return {
            "success": True,
            "directory": initial_dir,
            "is_cloud": True,
            "message": "Using server storage directory.",
        }

    result = await asyncio.to_thread(_run_native_folder_picker, initial_dir)

    if result.get("cancelled"):
        return {"success": False, "cancelled": True, "message": result.get("message", "Folder selection cancelled.")}

    if "error" in result:
        logger.warning("Folder picker unavailable (%s), falling back to default directory: %s", result["error"], initial_dir)
        return {
            "success": True,
            "directory": initial_dir,
            "fallback": True,
            "message": "Using default download directory.",
        }

    chosen_path = result.get("path", "")
    if not chosen_path:
        return {"success": False, "cancelled": True, "message": "Folder selection cancelled."}

    path_valid, path_result = validate_download_path(chosen_path)
    if not path_valid:
        logger.warning("[Directory] Picker returned invalid path %r: %s", chosen_path, path_result)
        return _safe_error(path_result, status_code=400)

    resolved = path_result
    logger.info("[Directory] Selected download folder: %s", resolved)

    settings["download_dir"] = resolved
    try:
        _save_settings(settings)
        last_dir_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".last_download_dir",
        )
        with open(last_dir_file, "w", encoding="utf-8") as f:
            f.write(resolved)
    except Exception as e:
        logger.warning("Could not persist download dir: %s", e)

    return {"success": True, "directory": resolved}


# ---------------------------------------------------------------------------
# /api/analyze
# ---------------------------------------------------------------------------

@router.post("/analyze")
@analyze_limiter.limit
async def analyze(request: Request, body: AnalyzeRequest):
    logger.info("[API] /api/analyze request received")
    raw_url = (body.url or "").strip()
    logger.info("[API] URL = %s", raw_url)
    if not raw_url:
        logger.warning("[API ERROR] Empty URL provided")
        return _safe_error("Please enter a YouTube or Instagram URL.")

    valid, clean_url, media_info = validate_media_url(raw_url)
    if not valid:
        logger.warning("[API ERROR] URL validation failed: %s", clean_url)
        return _safe_error(clean_url)

    logger.info("[API] URL validation complete")
    platform = media_info.get("platform", "youtube")
    has_playlist = media_info.get("is_playlist", False)
    is_mixed = media_info.get("is_mixed", False)
    noplaylist = not (has_playlist and not is_mixed)

    try:
        import yt_dlp
        yt_ver = getattr(yt_dlp.version, "__version__", "unknown")
    except Exception as e:
        yt_ver = f"error: {e}"

    try:
        import yt_dlp_ejs
        ejs_avail = getattr(yt_dlp_ejs, "__version__", getattr(yt_dlp_ejs, "version", "installed"))
    except Exception:
        ejs_avail = "not installed"

    deno_path = shutil.which("deno")
    deno_ver = "not found"
    if deno_path:
        try:
            deno_ver = subprocess.check_output([deno_path, "--version"], text=True).splitlines()[0]
        except Exception:
            deno_ver = "available"

    ffmpeg_path = get_ffmpeg_path()
    ffmpeg_ver = "not found"
    if ffmpeg_path:
        try:
            ffmpeg_ver = subprocess.check_output([ffmpeg_path, "-version"], text=True).splitlines()[0]
        except Exception:
            ffmpeg_ver = "available"

    logger.info("[API] yt-dlp initialization")
    logger.info("[API] yt-dlp version = %s", yt_ver)
    logger.info("[API] EJS version = %s", ejs_avail)
    logger.info("[API] Deno version = %s", deno_ver)
    logger.info("[API] FFmpeg version = %s", ffmpeg_ver)
    logger.info("[API] Starting YouTube extraction")

    try:
        # Enforce hard backend timeout of 15 seconds
        info = await asyncio.wait_for(
            asyncio.to_thread(get_video_info, clean_url, noplaylist=noplaylist),
            timeout=15.0
        )
        logger.info("[API] YouTube extraction finished")
    except (asyncio.TimeoutError, MediaExtractionError, Exception) as e:
        logger.error("[API ERROR] Extraction error for %s: %s", clean_url, e)
        logger.error("[API ERROR] full traceback:\n%s", traceback.format_exc())

        # For YouTube URLs, never let the request fail or return 500/502/504
        if platform == "youtube":
            logger.info("[API] Invoking instant fallback metadata for %s", clean_url)
            fallback = _fetch_youtube_oembed_fallback(clean_url)
            if fallback:
                logger.info("[API] Returning metadata")
                return {
                    "success": True,
                    "url": clean_url,
                    "platform": "youtube",
                    "has_playlist": False,
                    "is_mixed": False,
                    **fallback,
                }

        if isinstance(e, MediaExtractionError):
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": e.message,
                    "error_code": e.code,
                    "platform": platform,
                }
            )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Media extraction failed. Please check the URL and try again.",
                "error_code": "EXTRACTION_FAILED",
            }
        )

    logger.info("[API] Returning metadata")
    return {
        "success": True,
        "url": clean_url,
        "platform": platform,
        "has_playlist": has_playlist,
        "is_mixed": is_mixed,
        **info,
    }


# ---------------------------------------------------------------------------
# /api/download
# ---------------------------------------------------------------------------

@router.post("/download")
@download_limiter.limit
async def start_download(request: Request, body: DownloadRequest):
    raw_url = (body.url or "").strip()
    quality = (body.quality or "best").strip()
    audio_only = bool(body.audio_only) or quality == "audio"
    noplaylist = bool(body.noplaylist)

    title = str(body.title or "")[:300]
    thumbnail = str(body.thumbnail or "")[:2000]
    channel = str(body.channel or "")[:200]
    duration_str = str(body.duration_str or "")[:20]

    if not raw_url:
        return _safe_error("No URL provided.")

    valid, clean_url, media_info = validate_media_url(raw_url)
    if not valid:
        return _safe_error(clean_url)

    platform = body.platform or media_info.get("platform") or "youtube"
    media_type = body.media_type or media_info.get("media_type") or "video"
    selected_items = body.selected_items

    # Strip playlist params if single video requested
    if platform == "youtube" and noplaylist and is_mixed_url(clean_url):
        clean_url = strip_playlist_from_url(clean_url)

    # Validate/determine download directory
    settings = _load_settings()
    requested_dir = (body.download_dir or settings.get("download_dir") or "").strip()

    if requested_dir:
        path_valid, path_result = validate_download_path(requested_dir)
        if not path_valid:
            save_dir = get_safe_download_root()
            logger.warning("Requested path rejected (%s), using default.", path_result)
        else:
            save_dir = path_result
    else:
        save_dir = get_safe_download_root()

    os.makedirs(save_dir, exist_ok=True)
    logger.info("[Directory] Saving to: %s", save_dir)

    # FFmpeg check for YouTube video merging
    if platform == "youtube" and not audio_only and not get_ffmpeg_path():
        return _safe_error(
            "FFmpeg is required for merging video and audio streams. "
            "Please install FFmpeg or configure its path."
        )

    # Concurrency check
    if job_manager.active_count() >= 2:
        return _safe_error(
            "Maximum concurrent downloads reached (2). "
            "Please wait for a current download to finish.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    job = job_manager.create_job(
        url=clean_url,
        quality=quality,
        save_dir=save_dir,
        audio_only=audio_only,
        noplaylist=noplaylist,
        title=title,
        thumbnail=thumbnail,
        channel=channel,
        duration_str=duration_str,
        platform=platform,
        media_type=media_type,
        selected_items=selected_items,
    )

    return {
        "success": True,
        "job_id": job.job_id,
        "message": "Download started.",
    }


# ---------------------------------------------------------------------------
# /api/progress/{job_id}  — Server-Sent Events (SSE)
# ---------------------------------------------------------------------------

@router.get("/progress/{job_id}")
async def progress_stream(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        async def not_found() -> AsyncGenerator[str, None]:
            yield f"data: {json.dumps({'status': 'failed', 'error': 'Job not found.'})}\n\n"
        return StreamingResponse(not_found(), media_type="text/event-stream")

    async def sse_event_generator() -> AsyncGenerator[str, None]:
        while True:
            try:
                event = await asyncio.to_thread(job._event_queue.get, True, 20.0)
                if event.get("type") == "heartbeat":
                    yield ": heartbeat\n\n"
                else:
                    yield f"data: {json.dumps(event)}\n\n"

                if event.get("status") in ("completed", "failed", "cancelled"):
                    while not job._event_queue.empty():
                        try:
                            rem = job._event_queue.get_nowait()
                            yield f"data: {json.dumps(rem)}\n\n"
                        except Exception:
                            break
                    break
            except Exception:
                yield ": heartbeat\n\n"
                if job.status in ("completed", "failed", "cancelled"):
                    break

    response = StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


# ---------------------------------------------------------------------------
# /api/job/{job_id}  — Polling fallback
# ---------------------------------------------------------------------------

@router.get("/job/{job_id}")
async def job_status(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        return _safe_error("Job not found.", status_code=404)
    return {"success": True, **job.to_dict()}


# ---------------------------------------------------------------------------
# /api/downloads  — History
# ---------------------------------------------------------------------------

@router.get("/downloads")
async def get_downloads():
    history = load_history()
    return {"success": True, "downloads": history}


@router.delete("/downloads/{entry_id}")
async def delete_download(entry_id: str):
    deleted = delete_history_entry(entry_id)
    if deleted:
        return {"success": True}
    return _safe_error("History entry not found.", status_code=404)


# ---------------------------------------------------------------------------
# /api/open-file/{entry_id} and /api/open-folder/{entry_id}
# ---------------------------------------------------------------------------

@router.post("/open-file/{entry_id}")
async def open_file(entry_id: str):
    """Open the downloaded file in the default OS handler."""
    history = load_history()
    entry = next((e for e in history if e.get("id") == entry_id), None)
    if not entry:
        return _safe_error("History entry not found.", status_code=404)

    file_path = entry.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return _safe_error("File no longer exists.")

    path_valid, _ = validate_local_file_path(file_path)
    if not path_valid:
        return _safe_error("Access denied.", status_code=403)

    try:
        if sys.platform == "win32" and hasattr(os, "startfile"):
            os.startfile(file_path)
        else:
            return {"success": True, "message": "File stored on server: " + file_path}
    except Exception as e:
        logger.error("open-file error: %s", e)
        return _safe_error("Could not open file.")

    return {"success": True}


@router.post("/open-folder/{entry_id}")
async def open_folder(entry_id: str):
    """Open the containing folder in Windows Explorer."""
    history = load_history()
    entry = next((e for e in history if e.get("id") == entry_id), None)
    if not entry:
        return _safe_error("History entry not found.", status_code=404)

    directory = entry.get("directory", "")
    if not directory or not os.path.isdir(directory):
        directory = get_safe_download_root()

    path_valid, resolved = validate_download_path(directory)
    if not path_valid:
        resolved = get_safe_download_root()

    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", resolved])
        else:
            return {"success": True, "message": "Server-side directory: " + resolved}
    except Exception as e:
        logger.error("open-folder error: %s", e)
        return _safe_error("Could not open folder.")

    return {"success": True}


# ---------------------------------------------------------------------------
# /api/download-file/{entry_id}  — Stream file to client browser
# ---------------------------------------------------------------------------

@router.get("/download-file/{entry_id}")
async def download_file(entry_id: str):
    """Download the completed media file directly to the client browser."""
    job = job_manager.get_job(entry_id)
    file_path = None
    filename = None

    if job and job.status == "completed":
        file_path = job.file_path or (getattr(job, "result", None) or {}).get("file")
        filename = job.filename or (getattr(job, "result", None) or {}).get("filename")

    if not file_path or not os.path.exists(file_path):
        history = load_history()
        entry = next((e for e in history if e.get("id") == entry_id), None)
        if entry:
            file_path = entry.get("file_path")
            filename = entry.get("filename")

    if not file_path or not os.path.exists(file_path):
        return _safe_error("File not found or has been cleaned up from server.", status_code=404)

    return FileResponse(
        path=file_path,
        filename=filename or os.path.basename(file_path),
        media_type="application/octet-stream"
    )


# ---------------------------------------------------------------------------
# /api/settings
# ---------------------------------------------------------------------------

@router.get("/settings")
async def get_settings():
    settings = _load_settings()
    return {"success": True, "settings": settings}


@router.post("/settings")
async def save_settings(body: SettingsUpdateRequest):
    settings = _load_settings()

    if body.default_quality is not None:
        q = str(body.default_quality)
        if q in ("best", "audio", "2160", "1440", "1080", "720", "480", "360", "240", "144"):
            settings["default_quality"] = q

    if body.max_concurrent is not None:
        try:
            mc = int(body.max_concurrent)
            settings["max_concurrent"] = max(1, min(mc, 5))
        except (ValueError, TypeError):
            pass

    if body.theme is not None:
        if body.theme in ("dark", "light"):
            settings["theme"] = body.theme

    if body.download_dir is not None:
        requested = str(body.download_dir).strip()
        path_valid, resolved = validate_download_path(requested)
        if path_valid:
            settings["download_dir"] = resolved
        else:
            return _safe_error(f"Invalid download directory: {resolved}")

    try:
        _save_settings(settings)
    except Exception as e:
        logger.error("Failed to save settings: %s", e)
        return _safe_error("Failed to save settings.", status_code=500)

    return {"success": True, "settings": settings}


# ---------------------------------------------------------------------------
# /api/cookies — Cookie management for Cloud & YouTube Bot Bypass
# ---------------------------------------------------------------------------

@router.get("/cookies/status")
async def get_cookie_status():
    cookie_path = _get_cookie_file()
    has_cookies = bool(cookie_path and os.path.exists(cookie_path) and os.path.getsize(cookie_path) > 0)
    source = "env" if bool(os.environ.get("YOUTUBE_COOKIES")) else ("file" if has_cookies else None)
    return {
        "success": True,
        "has_cookies": has_cookies,
        "source": source,
    }


@router.post("/cookies")
async def save_cookies(request: Request):
    try:
        body = await request.json()
        cookies_content = (body.get("cookies") or "").strip()
        if not cookies_content:
            return _safe_error("No cookie content provided.", status_code=400)

        target_file = os.path.join(_PROJECT_ROOT, "cookies.txt")
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(cookies_content)

        return {"success": True, "message": "Cookies saved successfully."}
    except Exception as e:
        logger.error("Failed to save cookies: %s", e)
        return _safe_error("Failed to save cookies.", status_code=500)


@router.delete("/cookies")
async def clear_cookies():
    target_file = os.path.join(_PROJECT_ROOT, "cookies.txt")
    if os.path.exists(target_file):
        try:
            os.remove(target_file)
        except Exception as e:
            logger.error("Failed to remove cookies.txt: %s", e)
            return _safe_error("Could not remove cookies file.", status_code=500)
    return {"success": True, "message": "Cookies cleared."}

