"""
app.py — YT_DOWNLOADER FastAPI application entry point.

Run with:
    python app.py
or:
    uvicorn app:app --host 127.0.0.1 --port 5000

Then open: http://127.0.0.1:5000
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "app.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("yt_downloader")


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Check FFmpeg
    from backend.downloader import get_ffmpeg_path
    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        logger.info("FFmpeg detected: %s", ffmpeg)
    else:
        logger.warning(
            "FFmpeg NOT found. Video+audio merging will not work. "
            "Install FFmpeg and ensure it is on your PATH."
        )
    yield
    # Shutdown logic if needed
    logger.info("Shutting down YT_DOWNLOADER server.")


# ---------------------------------------------------------------------------
# App initialization
# ---------------------------------------------------------------------------

app = FastAPI(
    title="YT_DOWNLOADER",
    description="Secure YouTube Downloader Web Application",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

# Static files & templates
static_path = os.path.join(BASE_DIR, "frontend", "static")
templates_path = os.path.join(BASE_DIR, "frontend", "templates")

if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

templates = Jinja2Templates(directory=templates_path)

# Register API Router
from backend.routes import router as api_router
app.include_router(api_router)


# ---------------------------------------------------------------------------
# Frontend SPA route
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Not found."},
        )
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        return JSONResponse(
            status_code=429,
            content={"success": False, "error": "Too many requests. Please slow down."},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    msg = errors[0].get("msg", "Invalid request parameters.") if errors else "Bad request."
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"success": False, "error": msg},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Internal server error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"success": False, "error": "An internal error occurred."},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("   YT_DOWNLOADER — Secure YouTube Downloader (FastAPI)")
    print("=" * 60)
    print("   Running at: http://127.0.0.1:5000")
    print("   Swagger Docs: http://127.0.0.1:5000/docs")
    print("   Press Ctrl+C to stop.")
    print("=" * 60 + "\n")

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=5000,
        log_level="info",
        reload=False,
    )
