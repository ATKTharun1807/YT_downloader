# Development Rules & Coding Standards — YT_DOWNLOADER

This document serves as an explicit rulebook for all developers and AI assistants working on **YT_DOWNLOADER**. All code changes must strictly conform to these rules.

---

## 1. General Engineering Principles

1. **Architecture & Scope Awareness**: Always review [`PRD.md`](file:///d:/MAIN%20PROJECT/YT_downloader/PRD.md) and [`ARCHITECTURE.md`](file:///d:/MAIN%20PROJECT/YT_downloader/ARCHITECTURE.md) before implementing changes. Keep modifications modular, isolated, and focused.
2. **Zero Shell Execution with Unsafe Inputs**: NEVER pass raw user strings into `os.system()` or `subprocess.Popen(..., shell=True)`. Always pass arguments as sanitized lists.
3. **Async Non-Blocking Operations**: Long-running operations (video metadata fetching, stream downloads, FFmpeg encoding) MUST run asynchronously inside background worker tasks or execution threads so the main event loop is never blocked.
4. **No Hidden Failures / Silent Exceptions**: Catch specific exceptions and log tracebacks. Provide user-facing error messages via standardized JSON error structures.

---

## 2. Backend Coding Standards (Python / FastAPI)

- **Python Version**: Target Python 3.10+.
- **Type Hinting**: Use explicit type annotations on all function signatures, parameters, and return types.
- **Data Validation**: Enforce request/response structures using **Pydantic v2** models in [`backend/schemas.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/schemas.py).
- **Security Validation**:
  - All input URLs MUST pass through [`backend/validator.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/validator.py) (`validate_url`).
  - Output file paths MUST be checked against path traversal using `os.path.realpath` / `Path.resolve()` to ensure target files remain within the designated downloads directory.
- **Logging**: Use standard `logging.getLogger("yt_downloader")` instead of bare `print()` statements.

---

## 3. Frontend Standards (HTML / CSS / JavaScript)

- **Framework Policy**: Use Vanilla JavaScript (ES6+) and Vanilla CSS3. Do NOT add heavy third-party UI libraries (React, Vue, Tailwind) unless explicitly requested.
- **CSS Architecture**:
  - Maintain styling in [`frontend/static/css/style.css`](file:///d:/MAIN%20PROJECT/YT_downloader/frontend/static/css/style.css).
  - Use CSS Custom Properties (variables) defined in `:root` for colors, glass opacity, border-radii, and transition timing.
  - Apply glassmorphism styling using `backdrop-filter: blur(...)` and semi-transparent background surfaces (`rgba(...)`).
- **DOM Manipulation & Security**:
  - Never use unsafe `innerHTML` with unsanitized user inputs. Use `textContent` or controlled DOM construction to avoid XSS vulnerabilities.
- **Network & State**:
  - Wrap API fetch calls with try/catch blocks and user notification handlers.
  - Handle SSE connection lifecycle cleanly (close EventSource on completion or error).

---

## 4. Project Structure & File Boundaries

| Component | Allowed File Locations |
|---|---|
| API Endpoints | [`backend/routes.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/routes.py) |
| Request & Response Schemas | [`backend/schemas.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/schemas.py) |
| Core yt-dlp / FFmpeg Logic | [`backend/downloader.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/downloader.py) |
| Security & Validation | [`backend/validator.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/validator.py), [`backend/security.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/security.py) |
| Background Jobs & SSE | [`backend/jobs.py`](file:///d:/MAIN%20PROJECT/YT_downloader/backend/jobs.py) |
| Frontend Assets | [`frontend/static/css/`](file:///d:/MAIN%20PROJECT/YT_downloader/frontend/static/css/), [`frontend/static/js/`](file:///d:/MAIN%20PROJECT/YT_downloader/frontend/static/js/) |
| HTML Shell | [`frontend/templates/index.html`](file:///d:/MAIN%20PROJECT/YT_downloader/frontend/templates/index.html) |

---

## 5. Security & Safety Rules

> [!CAUTION]
> **Strict Security Guardrails**:
> 1. **URL Domain Whitelist**: Only allow `youtube.com`, `www.youtube.com`, `m.youtube.com`, and `youtu.be`. Reject all external domains or private IP ranges.
> 2. **Path Traversal Guard**: Reject any file path containing `..`, leading slashes, or absolute paths outside designated directories.
> 3. **Rate Limiting**: Retain the 60 requests/minute client rate-limiting on sensitive endpoints.
