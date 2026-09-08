# YT_DOWNLOADER

**A secure, fast, and private local web interface for downloading YouTube videos.**

> Fast. Simple. Private.

---

## Features

- 🎬 **Download any quality** — 4K (2160p), 2K (1440p), Full HD (1080p), HD, SD, 360p, 240p, 144p
- 🎵 **Audio extraction** — MP3 at 192kbps via FFmpeg
- 📋 **Playlist support** — Choose single video or entire playlist
- 📊 **Real-time progress** — Live progress bar via Server-Sent Events (SSE)
- 📁 **Download history** — Browse, open, and manage completed downloads
- ⚙ **Settings** — Configure quality, directory, theme, and concurrency
- 🔒 **Security-first** — URL whitelist, path traversal prevention, rate limiting, no shell injection
- 🌙 **Dark / Light theme** — Beautiful glassmorphism dark UI
- 📱 **Responsive** — Works on desktop, tablet, and mobile

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| Download Engine | yt-dlp |
| Stream Merging | FFmpeg |
| Frontend | HTML5, CSS3, Vanilla JavaScript |
| Progress | Server-Sent Events (SSE) |

---

## Project Structure

```
YT_downloader/
├── app.py                    # FastAPI entry point
├── requirements.txt
├── settings.json             # User settings (auto-created)
├── downloads_history.json    # Download history (auto-created)
│
├── backend/
│   ├── __init__.py
│   ├── routes.py             # API endpoints (FastAPI APIRouter)
│   ├── schemas.py            # Pydantic request models
│   ├── downloader.py         # yt-dlp core logic
│   ├── validator.py          # URL + path security
│   ├── jobs.py               # Background job manager
│   └── security.py           # Rate limiting
│
├── frontend/
│   ├── templates/
│   │   └── index.html        # SPA shell
│   └── static/
│       ├── css/style.css     # Dark glassmorphism theme
│       ├── js/app.js         # SPA JavaScript
│       └── assets/
│
├── downloads/                # Default download directory
├── logs/                     # Server-side logs
└── youtube.py                # Original CLI (still works!)
```

---

## Installation

### 1. Prerequisites

- **Python 3.10+**
- **FFmpeg** (required for video+audio merging)

### 2. FFmpeg Setup

**Option A — System PATH (recommended):**
```
winget install ffmpeg
```
or download from https://ffmpeg.org/download.html and add to PATH.

**Option B — Python package (automatic):**
```
pip install imageio-ffmpeg
```
The app will detect `imageio-ffmpeg` automatically if system FFmpeg is not found.

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

Or in a virtual environment:
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

## Running the Application

```bash
python app.py
```

Then open your browser:

```
http://127.0.0.1:5000
```

The app runs **only on localhost** — it is not exposed to your network by default.

---

## Using the CLI (Original)

The original CLI downloader is preserved and still fully functional:

```bash
python youtube.py
```

---

## API Documentation

### `POST /api/analyze`
Fetch video information.

**Request:**
```json
{ "url": "https://www.youtube.com/watch?v=..." }
```

**Response:**
```json
{
  "success": true,
  "title": "Video Title",
  "thumbnail": "https://...",
  "channel": "Channel Name",
  "duration": 272,
  "duration_str": "04:32",
  "quality_options": [...],
  "is_playlist": false
}
```

---

### `POST /api/download`
Start a background download job.

**Request:**
```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "quality": "1080",
  "audio_only": false,
  "noplaylist": true
}
```

**Response:**
```json
{ "success": true, "job_id": "uuid-..." }
```

---

### `GET /api/progress/<job_id>`
Server-Sent Events stream for live progress.

**Events:**
```json
{ "status": "downloading", "percentage": 82, "speed": "8.6 MiB/s", "eta": "00:03" }
{ "status": "merging", "percentage": 100 }
{ "status": "completed", "file": "downloads/video.mp4" }
```

---

### `GET /api/downloads`
Return download history.

### `DELETE /api/downloads/<id>`
Delete a history entry.

### `GET /api/settings` / `POST /api/settings`
Read or update application settings.

### `GET /api/ffmpeg-status`
Check FFmpeg availability.

---

## Security

| Threat | Mitigation |
|---|---|
| SSRF | Only whitelisted YouTube domains (`youtube.com`, `youtu.be`, `m.youtube.com`) |
| Path traversal | `os.path.realpath()` checked against allowed download root |
| Shell injection | `shell=False` always; yt-dlp Python API only, never `os.system()` |
| XSS | Jinja2 auto-escaping; JS uses `textContent` not `innerHTML` for user data |
| Rate limiting | Per-IP: 10 analyze/min, 5 download/min |
| Stack trace exposure | Global error handlers; raw errors logged server-side only |
| Large requests | `MAX_CONTENT_LENGTH = 1 MB` |

---

## Troubleshooting

**FFmpeg not found**
The sidebar will show a red dot. Install FFmpeg via `winget install ffmpeg` or `pip install imageio-ffmpeg`.

**Port 5000 in use**
Edit `app.py` and change `port=5000` to another port.

**Video unavailable**
Some videos are region-locked, private, or age-restricted. These cannot be downloaded.

**Download stuck at 0%**
YouTube occasionally throttles connections. The app uses chunked downloading (10MB chunks) with 20 retries automatically.

**Windows Defender blocks writes**
If Controlled Folder Access is enabled, add Python to the allowed apps list, or change the download directory to a non-protected folder.

---

## Future Improvements

- [ ] Browser-native folder picker (File System Access API)
- [ ] Subtitle/caption download support
- [ ] Thumbnail download
- [ ] Download queue management UI
- [ ] Docker container support
- [ ] Configurable FFmpeg path in Settings UI

---

## Legal Notice

Use this application only for content you are authorized to download and in accordance with YouTube's Terms of Service and applicable copyright laws. This tool does not bypass DRM, authentication, paywalls, or any access restrictions.
