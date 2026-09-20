# System Architecture — YT_DOWNLOADER

## 1. High-Level System Architecture

`YT_DOWNLOADER` is architected as a decoupled, asynchronous web application featuring a **FastAPI backend** and a lightweight **Vanilla JavaScript / CSS Glassmorphism SPA frontend**, connected via RESTful API routes and Server-Sent Events (SSE).

```
 ┌─────────────────────────────────────────────────────────────┐
 │                    User Web Browser                         │
 │  Single Page Application (HTML5, Vanilla CSS, JS ES6+)      │
 └──────────────┬──────────────────────────────▲───────────────┘
                │ HTTP POST /api/analyze       │
                │ HTTP POST /api/download      │ SSE Live Progress
                │ HTTP GET  /api/history       │ /api/stream/{job_id}
                ▼                              │
 ┌─────────────────────────────────────────────┴───────────────┐
 │                   FastAPI Web Server                        │
 │                   (Uvicorn @ localhost:5000)                │
 ├─────────────────────────────────────────────────────────────┤
 │ • Security & Rate Limiter Middleware (`security.py`)        │
 │ • Input URL & Path Traversal Validator (`validator.py`)     │
 │ • API Endpoint Controller (`routes.py`)                     │
 │ • Pydantic Request/Response Models (`schemas.py`)           │
 └──────────────┬──────────────────────────────▲───────────────┘
                │                              │
                ▼                              │ Job Progress Callbacks
 ┌─────────────────────────────────────────────┴───────────────┐
 │              Background Job Manager (`jobs.py`)             │
 │ • Async Task Queue & Active Job Registry                    │
 │ • Event Stream Dispatcher (SSE Queues)                      │
 └──────────────┬──────────────────────────────────────────────┘
                │ Invokes yt-dlp API Hooks
                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │           Download Engine Layer (`downloader.py`)           │
 │ • yt-dlp (Metadata & Stream Extraction)                     │
 │ • yt-dlp-ejs (EJS / JS challenge solver)                    │
 │ • FFmpeg (Audio-Video Merging & MP3 Transcoding)            │
 └──────────────┬──────────────────────────────────────────────┘
                │
                ▼ Writes Media & JSON Logs
 ┌─────────────────────────────────────────────────────────────┐
 │                 Local Filesystem Storage                    │
 │ • `downloads/` (Video & Audio Files)                        │
 │ • `settings.json` & `downloads_history.json`                │
 │ • `logs/app.log`                                            │
 └─────────────────────────────────────────────────────────────┘
```

---

## 2. Technology Stack Breakdown

| Architectural Layer | Technology | Purpose / Rationale |
|---|---|---|
| **Frontend UI** | HTML5, Vanilla CSS3, JavaScript (ES6+) | Zero external framework dependencies; maximum loading speed & responsive glassmorphism UI. |
| **Progress Transport** | Server-Sent Events (SSE) | Light-weight unidirectional HTTP streaming for real-time progress, speed, and ETA metrics. |
| **Backend Framework** | Python 3.10+, FastAPI | High performance, async native architecture, automatic Pydantic request validation, OpenAPI docs. |
| **Server Engine** | Uvicorn (ASGI) | Lightning-fast ASGI web server hosting FastAPI application on `127.0.0.1:5000`. |
| **Download Core** | `yt-dlp` | Industry standard, actively maintained YouTube extraction engine. |
| **JS Challenge Solver** | `yt-dlp-ejs` | Embedded JavaScript interpreter integration to solve signature challenges cleanly. |
| **Media Processor** | FFmpeg (`imageio-ffmpeg` fallback) | Transcoding audio to MP3, multiplexing video + audio streams into high-res MP4/MKV. |
| **Data Storage** | Local Filesystem JSON | Zero database configuration required (`downloads_history.json`, `settings.json`). |

---

## 3. Directory & Component Structure

```
YT_downloader/
├── app.py                     # FastAPI application entry point, middleware, exception handlers & startup diagnostics
├── youtube.py                 # Original standalone CLI interface
├── requirements.txt           # Python dependency definitions
├── settings.json              # Persistent user preferences configuration
├── downloads_history.json     # Download history log data
├── render.yaml                # Deployment manifest
├── Dockerfile                 # Container image specification
│
├── backend/                   # Core Python Backend Package
│   ├── __init__.py
│   ├── routes.py              # FastAPI endpoints (/api/analyze, /api/download, /api/stream/{job_id}, etc.)
│   ├── schemas.py             # Pydantic data schemas & request validation models
│   ├── downloader.py          # yt-dlp wrapper, format resolver, FFmpeg path discovery
│   ├── validator.py           # Security validation (URL whitelist & path traversal protection)
│   ├── jobs.py                # Asynchronous background job queue & SSE progress stream manager
│   └── security.py            # Client rate-limiting middleware & sliding window tracker
│
├── frontend/                  # Modern UI Frontend Assets
│   ├── templates/
│   │   └── index.html         # SPA HTML template
│   └── static/
│       ├── css/
│       │   └── style.css      # Dark glassmorphism theme, CSS variables & animations
│       └── js/
│           └── app.js         # Single-page application logic, API calls, SSE event handling
│
├── downloads/                 # Default media output directory
└── logs/                      # Server runtime log directory (`app.log`)
```

---

## 4. End-to-End Data Flow

### 4.1 URL Analysis Flow
1. User enters YouTube link in Frontend UI.
2. Frontend sends `POST /api/analyze` with payload `{"url": "..."}`.
3. [`validator.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/validator.py) checks domain whitelist (`youtube.com`, `youtu.be`).
4. [`downloader.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/downloader.py) invokes `yt-dlp` in extract-flat mode to fetch title, duration, thumbnail, and format resolutions.
5. FastAPI returns JSON payload containing format options array.

### 4.2 Async Download & SSE Progress Flow
1. User selects resolution (e.g., `1080p`) and clicks **Download**.
2. Frontend calls `POST /api/download`.
3. [`jobs.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/jobs.py) creates a new `Job` with unique `job_id`, enqueues background task, and returns `{"job_id": "..."}`.
4. Frontend establishes SSE connection to `GET /api/stream/{job_id}`.
5. `yt-dlp` download hook emits progress events to job queue -> SSE stream emits `progress` event -> Frontend updates UI progress bar live.
6. Upon completion, FFmpeg merges video and audio tracks if necessary, updates `downloads_history.json`, and sends `complete` SSE event.
