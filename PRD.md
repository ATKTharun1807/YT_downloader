# Product Requirements Document (PRD) — YT_DOWNLOADER

## 1. Product Overview

**YT_DOWNLOADER** is a secure, fast, private local web interface and command-line utility for downloading YouTube videos and audio streams across all available qualities (144p to 4K / 2160p) and converting audio to MP3 format.

Built with **FastAPI**, **yt-dlp**, **FFmpeg**, and a modern **Vanilla JavaScript & CSS Glassmorphism SPA**, YT_DOWNLOADER eliminates the need for third-party downloader websites laden with aggressive advertising, malware risks, rate limits, and privacy violations.

---

## 2. Problem Statement

Existing web-based YouTube downloaders present severe usability and security issues:
- **Security & Privacy Risks**: Malware-infested popups, deceptive ad buttons, tracking scripts, and data logging.
- **Performance Caps**: Throttled download speeds, queue delays, and paid paywalls for high-definition (1080p, 4K) or audio extraction.
- **Stream Merging Failures**: High-resolution YouTube streams separate video and audio tracks, which simple tools fail to combine correctly.
- **Unreliable User Experience**: Cluttered interfaces, broken links, and lack of real-time progress feedback during long video or playlist downloads.

**YT_DOWNLOADER** solves these problems by providing a self-hosted, lightweight, high-performance local web app that runs on `localhost` with zero ads, hardware-accelerated stream merging via FFmpeg, and real-time Server-Sent Events (SSE) progress tracking.

---

## 3. Goals & Key Objectives

1. **High-Speed & Full Quality Support**: Support all video resolutions up to 4K (2160p, 1440p, 1080p, 720p, 480p, 360p) and standalone high-bitrate MP3 audio extraction (192kbps).
2. **Real-Time Progress Visualization**: Provide accurate live progress metrics (download percentage, speed in MB/s, ETA, downloaded size) using Server-Sent Events (SSE).
3. **Security-First Architecture**: Enforce strict URL domain whitelisting, path traversal protection, process isolation (no shell execution), and client rate-limiting.
4. **Clean & Modern UI**: Deliver a stunning dark glassmorphic single-page web application with zero external framework overhead.
5. **Playlist & Single Video Processing**: Seamlessly handle individual YouTube video links as well as full playlists with item selection.

---

## 4. Target Users

- **Content Creators & Editors**: Professionals who require high-quality source footage and audio clips without watermark or compression artifacts.
- **Students & Educators**: Users collecting video lectures, tutorials, and educational content for offline viewing.
- **Media Archivists & Offline Viewers**: Users with limited internet connectivity who store personal offline video libraries.
- **Privacy-Conscious Individuals**: Tech-savvy users who reject third-party ad-driven web scrapers and prioritize data privacy.

---

## 5. Core Features (MVP Scope)

### 5.1 Video Analysis & Quality Selection
- Input YouTube URL validation (`youtube.com`, `youtu.be`).
- Asynchronous metadata extraction (Title, Channel, Duration, Thumbnail, Available Formats).
- Quality options menu (4K 2160p, 2K 1440p, 1080p60/1080p, 720p, 480p, 360p, Audio MP3).

### 5.2 Real-Time SSE Download Engine
- Asynchronous background download job executor (`backend/jobs.py`).
- Server-Sent Events stream (`GET /api/stream/{job_id}`) delivering live download stats.
- FFmpeg audio-video merging for formats above 720p.

### 5.3 Download History & File Management
- Persistent history storage (`downloads_history.json`).
- Browse completed downloads, view metadata, open destination folder, or delete items.

### 5.4 Settings & Configuration
- Customizable output directory path with validation.
- Maximum concurrent downloads limit setting.
- Interface theme toggle (Dark / Light).
- Persistent settings file (`settings.json`).

### 5.5 Security & Guardrails
- Input URL domain whitelist verification.
- Output path traversal prevention (strict path resolution inside target folder).
- Rate limiting middleware (sliding window requests control).
- Automatic diagnostic check on startup (`yt-dlp`, `FFmpeg`, `Deno` JS runtime detection).
