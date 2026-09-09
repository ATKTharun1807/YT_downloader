"""
jobs.py — Background download job manager.

Thread-based job system, no Redis/Celery required.
Limits concurrent downloads to MAX_CONCURRENT (default: 2).

Job lifecycle:
  pending → downloading → (merging|extracting_audio) → cleaning → completed
                        ↘ failed
"""

import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger(__name__)

MAX_CONCURRENT_DOWNLOADS = 2

# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------

JOB_STATES = {
    "pending",
    "downloading",
    "merging",
    "extracting_audio",
    "cleaning",
    "completed",
    "failed",
    "cancelled",
}

_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "downloads_history.json",
)


class Job:
    """Represents a single download job."""

    def __init__(
        self,
        job_id: str,
        url: str,
        quality: str,
        save_dir: str,
        audio_only: bool,
        noplaylist: bool,
        title: str = "",
        thumbnail: str = "",
        channel: str = "",
        duration_str: str = "",
        platform: str = "youtube",
        media_type: str = "video",
        selected_items: Optional[List[int]] = None,
    ):
        self.job_id = job_id
        self.url = url
        self.quality = quality
        self.save_dir = save_dir
        self.audio_only = audio_only
        self.noplaylist = noplaylist
        self.title = title
        self.thumbnail = thumbnail
        self.channel = channel
        self.duration_str = duration_str
        self.platform = platform
        self.media_type = media_type
        self.selected_items = selected_items or []

        self.status = "pending"
        self.percentage = 0.0
        self.speed = ""
        self.eta = ""
        self.stage = "pending"
        self.error: Optional[str] = None
        self.file_path: Optional[str] = None
        self.filename: Optional[str] = None
        self.created_at = datetime.utcnow().isoformat()
        self.completed_at: Optional[str] = None

        # SSE event queue — consumers read from this
        self._event_queue: queue.Queue = queue.Queue(maxsize=200)

    def push_event(self, event: dict) -> None:
        """Push a progress event. Non-blocking; drops if queue full."""
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            pass

    def iter_events(self, timeout: float = 30.0):
        """
        Generator that yields events from the queue.
        Yields None (heartbeat) after `timeout` seconds of silence.
        Stops when job reaches terminal state and queue is empty.
        """
        while True:
            try:
                event = self._event_queue.get(timeout=timeout)
                yield event
                if event.get("status") in ("completed", "failed", "cancelled"):
                    # Drain remaining events then stop
                    while not self._event_queue.empty():
                        yield self._event_queue.get_nowait()
                    return
            except queue.Empty:
                if self.status in ("completed", "failed", "cancelled"):
                    return
                yield {"type": "heartbeat"}  # keep SSE connection alive

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "percentage": self.percentage,
            "speed": self.speed,
            "eta": self.eta,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "channel": self.channel,
            "duration_str": self.duration_str,
            "quality": self.quality,
            "audio_only": self.audio_only,
            "platform": self.platform,
            "media_type": self.media_type,
            "selected_items": self.selected_items,
            "file_path": self.file_path,
            "filename": self.filename,
            "directory": self.save_dir,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


# ---------------------------------------------------------------------------
# Job Manager
# ---------------------------------------------------------------------------

class JobManager:
    """Thread-safe manager for download jobs."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_DOWNLOADS):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(max_concurrent)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_job(
        self,
        url: str,
        quality: str,
        save_dir: str,
        audio_only: bool,
        noplaylist: bool,
        title: str = "",
        thumbnail: str = "",
        channel: str = "",
        duration_str: str = "",
        platform: str = "youtube",
        media_type: str = "video",
        selected_items: Optional[List[int]] = None,
    ) -> "Job":
        """Create a new job and schedule it for background execution."""
        job_id = str(uuid.uuid4())
        job = Job(
            job_id=job_id,
            url=url,
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

        with self._lock:
            self._jobs[job_id] = job

        # Launch background worker thread
        t = threading.Thread(
            target=self._run_job,
            args=(job,),
            daemon=True,
            name=f"job-{job_id[:8]}",
        )
        t.start()

        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.status in ("pending", "downloading", "merging", "extracting_audio", "cleaning")
            )

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    def _run_job(self, job: Job) -> None:
        """Execute a download job in the background thread."""
        self._semaphore.acquire()
        try:
            self._execute(job)
        finally:
            self._semaphore.release()

    def _execute(self, job: Job) -> None:
        from backend.downloader import execute_download, execute_carousel_download

        def on_progress(d: dict) -> None:
            """Progress hook → update job state and push event."""
            status = d.get("status", "")

            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                pct = (downloaded / total * 100) if total else 0

                speed_bps = d.get("speed") or 0
                speed_str = _format_speed(speed_bps)
                eta_sec = d.get("eta") or 0
                eta_str = _format_eta(eta_sec)

                job.percentage = round(pct, 1)
                job.speed = speed_str
                job.eta = eta_str
                job.status = "downloading"
                job.stage = "downloading"

                job.push_event({
                    "type": "progress",
                    "status": "downloading",
                    "stage": "downloading",
                    "percentage": job.percentage,
                    "speed": speed_str,
                    "eta": eta_str,
                })

            elif status == "finished":
                job.push_event({
                    "type": "progress",
                    "status": "processing",
                    "stage": job.stage,
                    "percentage": 100,
                    "speed": "",
                    "eta": "",
                })

        def on_stage(stage: str) -> None:
            job.stage = stage
            job.status = stage
            job.push_event({
                "type": "stage",
                "status": stage,
                "stage": stage,
                "percentage": job.percentage,
                "speed": job.speed,
                "eta": job.eta,
            })

        try:
            if job.platform == "instagram" and job.media_type == "carousel":
                result = execute_carousel_download(
                    url=job.url,
                    selected_items=job.selected_items,
                    save_dir=job.save_dir,
                    title=job.title,
                    on_progress=on_progress,
                    on_stage=on_stage,
                )
            else:
                result = execute_download(
                    url=job.url,
                    quality=job.quality,
                    save_dir=job.save_dir,
                    audio_only=job.audio_only,
                    noplaylist=job.noplaylist,
                    on_progress=on_progress,
                    on_stage=on_stage,
                    media_type=job.media_type,
                    platform=job.platform,
                    title=job.title,
                )

            job.status = "completed"
            job.stage = "completed"
            job.percentage = 100.0
            job.speed = ""
            job.eta = ""
            job.file_path = result.get("file", "")
            job.filename = result.get("filename", "")
            job.completed_at = datetime.utcnow().isoformat()

            job.push_event({
                "type": "completed",
                "status": "completed",
                "stage": "completed",
                "percentage": 100,
                "file": job.file_path,
                "filename": job.filename,
                "directory": job.save_dir,
            })

            _save_to_history(job)

        except Exception as e:
            logger.error("Job %s failed: %s", job.job_id, e, exc_info=True)
            job.status = "failed"
            job.stage = "failed"
            job.error = str(e)
            job.completed_at = datetime.utcnow().isoformat()

            job.push_event({
                "type": "failed",
                "status": "failed",
                "stage": "failed",
                "error": str(e),
            })

            _save_to_history(job)


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------

_history_lock = threading.Lock()


def _save_to_history(job: Job) -> None:
    """Append completed/failed job to the JSON history file."""
    format_type = "mp3" if job.audio_only else ("zip" if job.media_type == "carousel" and len(job.selected_items) > 1 else ("jpg" if job.media_type == "image" else "mp4"))
    entry = {
        "id": job.job_id,
        "title": job.title,
        "thumbnail": job.thumbnail,
        "channel": job.channel,
        "duration_str": job.duration_str,
        "quality": job.quality,
        "audio_only": job.audio_only,
        "platform": job.platform,
        "media_type": job.media_type,
        "format": format_type,
        "status": job.status,
        "file_path": job.file_path,
        "filename": job.filename,
        "directory": job.save_dir,
        "error": job.error,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }

    with _history_lock:
        history = _load_history_raw()
        history.insert(0, entry)
        history = history[:200]
        try:
            with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Could not save history: %s", e)


def _load_history_raw() -> list[dict]:
    if not os.path.exists(_HISTORY_FILE):
        return []
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def load_history() -> list[dict]:
    with _history_lock:
        return _load_history_raw()


def delete_history_entry(entry_id: str) -> bool:
    with _history_lock:
        history = _load_history_raw()
        new_hist = [h for h in history if h.get("id") != entry_id]
        if len(new_hist) == len(history):
            return False
        try:
            with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(new_hist, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_speed(bps: float) -> str:
    if not bps:
        return ""
    if bps >= 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def _format_eta(sec: int) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"ETA {h}h {m}m"
    if m:
        return f"ETA {m}m {s:02d}s"
    return f"ETA {s}s"


job_manager = JobManager()
