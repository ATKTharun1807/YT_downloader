"""
schemas.py — Pydantic schemas for YT_DOWNLOADER FastAPI backend.
"""

from typing import Optional
from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    url: str = Field(..., description="YouTube video or playlist URL")


class DownloadRequest(BaseModel):
    url: str = Field(..., description="YouTube video or playlist URL")
    quality: str = Field(default="best", description="Requested video quality or 'audio'")
    download_dir: Optional[str] = Field(default=None, description="Optional custom download directory")
    audio_only: bool = Field(default=False, description="Whether to extract audio only")
    noplaylist: bool = Field(default=True, description="Whether to download single video only if playlist detected")
    title: Optional[str] = Field(default="", max_length=300)
    thumbnail: Optional[str] = Field(default="", max_length=2000)
    channel: Optional[str] = Field(default="", max_length=200)
    duration_str: Optional[str] = Field(default="", max_length=20)


class SettingsUpdateRequest(BaseModel):
    default_quality: Optional[str] = Field(default=None)
    max_concurrent: Optional[int] = Field(default=None)
    theme: Optional[str] = Field(default=None)
    download_dir: Optional[str] = Field(default=None)
